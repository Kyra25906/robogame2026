from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .serial_protocol import (
    MechanismCommand,
    MechanismState,
    MechanismStatus,
)


class CommandPhase(str, Enum):
    WAITING_ACK = "WAITING_ACK"
    WAITING_STATUS = "WAITING_STATUS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class PollAction(str, Enum):
    NONE = "NONE"
    RETRY = "RETRY"


@dataclass(frozen=True)
class CommandResult:
    success: bool
    error_code: int
    duration_s: float
    detail: str


class MechanismCommandTracker:
    """Track one V1 mechanism command from transmission to a terminal status."""

    def __init__(
        self,
        command: MechanismCommand,
        *,
        started_s: float,
        timeout_s: float,
        ack_timeout_s: float = 0.1,
        status_timeout_s: float = 0.3,
        max_attempts: int = 3,
    ) -> None:
        if timeout_s <= 0.0:
            raise ValueError("mechanism timeout must be positive")
        if ack_timeout_s <= 0.0 or status_timeout_s <= 0.0:
            raise ValueError("mechanism transport timeouts must be positive")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self.command = command
        self.started_s = started_s
        self.deadline_s = started_s + timeout_s
        self.ack_timeout_s = ack_timeout_s
        self.status_timeout_s = status_timeout_s
        self.max_attempts = max_attempts
        self.phase = CommandPhase.WAITING_ACK
        self.attempts = 0
        self.awaiting_sequence: int | None = None
        self.response_deadline_s = started_s
        self.last_state: MechanismState | None = None
        self.result: CommandResult | None = None

    @property
    def done(self) -> bool:
        return self.result is not None

    def mark_sent(self, sequence: int, now_s: float) -> None:
        if self.done:
            return
        self.attempts += 1
        self.awaiting_sequence = sequence
        self.phase = CommandPhase.WAITING_ACK
        self.response_deadline_s = now_s + self.ack_timeout_s

    def handle_ack(
        self, acknowledged_sequence: int, result: int, now_s: float
    ) -> bool:
        if self.done or acknowledged_sequence != self.awaiting_sequence:
            return False
        if result != 0:
            self._finish(False, int(result), now_s, f"MCU rejected frame ACK={result}")
            return True
        self.phase = CommandPhase.WAITING_STATUS
        self.response_deadline_s = now_s + self.status_timeout_s
        return True

    def handle_status(self, status: MechanismStatus, now_s: float) -> bool:
        if self.done:
            return False
        if (
            status.command_id != self.command.command_id
            or status.operation != self.command.operation
        ):
            return False

        if status.state in (MechanismState.ACCEPTED, MechanismState.RUNNING):
            if (
                self.last_state is MechanismState.RUNNING
                and status.state is MechanismState.ACCEPTED
            ):
                self._finish(False, 9006, now_s, "mechanism state moved backwards")
                return True
            self.last_state = status.state
            self.phase = CommandPhase.WAITING_STATUS
            self.response_deadline_s = now_s + self.status_timeout_s
            return True

        if status.state is MechanismState.SUCCEEDED:
            if status.error_code != 0:
                self._finish(False, 9006, now_s, "SUCCEEDED carried a non-zero error")
            else:
                self._finish(True, 0, now_s, "mechanism command succeeded")
            return True

        self._finish(
            False,
            int(status.error_code) or 9006,
            now_s,
            f"mechanism command ended as {status.state.name}",
        )
        return True

    def poll(self, now_s: float) -> PollAction:
        if self.done:
            return PollAction.NONE
        if now_s >= self.deadline_s:
            self._finish(False, 2002, now_s, "mechanism command timed out")
            return PollAction.NONE
        if now_s < self.response_deadline_s:
            return PollAction.NONE
        if self.attempts >= self.max_attempts:
            self._finish(False, 9003, now_s, "mechanism response timed out")
            return PollAction.NONE
        return PollAction.RETRY

    def cancel(self, error_code: int, detail: str, now_s: float) -> None:
        if not self.done:
            self._finish(False, error_code, now_s, detail)

    def _finish(
        self, success: bool, error_code: int, now_s: float, detail: str
    ) -> None:
        self.phase = CommandPhase.SUCCEEDED if success else CommandPhase.FAILED
        self.result = CommandResult(
            success=success,
            error_code=error_code,
            duration_s=max(0.0, now_s - self.started_s),
            detail=detail,
        )
