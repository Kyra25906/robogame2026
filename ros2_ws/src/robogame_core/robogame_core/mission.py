from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from .models import Cargo, CubeColor, MissionResult


class MissionState(str, Enum):
    # A2 / P0-1: 上电启动前置状态——真实固件握手完成前 communication_ok=False
    # 是正常时序，此状态下只等待、不评估、不 fail（见 MissionMachine.tick）。
    WAIT_FOR_COMMUNICATION = "WAIT_FOR_COMMUNICATION"
    SELF_CHECK = "SELF_CHECK"
    WAIT_FOR_PHYSICAL_START = "WAIT_FOR_PHYSICAL_START"
    GO_TO_ORANGE = "GO_TO_ORANGE"
    PICK_ORANGE = "PICK_ORANGE"
    GO_TO_PURPLE = "GO_TO_PURPLE"
    PICK_PURPLE = "PICK_PURPLE"
    GO_TO_BUILD = "GO_TO_BUILD"
    PLACE_ORANGE = "PLACE_ORANGE"
    PLACE_PURPLE = "PLACE_PURPLE"
    RETREAT = "RETREAT"
    VERIFY_BUILD = "VERIFY_BUILD"
    SAFE_STOP = "SAFE_STOP"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class MissionConfig:
    orange_target: int = 1
    purple_target: int = 0
    max_retries: int = 2
    state_timeout_s: float = 20.0
    build_stability_s: float = 3.0
    # A2 / P0-1: 上电等待通信就绪的上限（覆盖握手 3s×3 + 余量），
    # 超时才 fail(COMMUNICATION_ERROR)；等待期内不评估、不 fail。
    startup_wait_timeout_s: float = 15.0


def classify_action_result(data: str) -> tuple[bool, MissionResult | None, str]:
    """Classify an action-result string into a machine tick.

    Returns ``(succeeded, failure_result, detail)``:

    - ``SUCCESS`` / ``STABLE`` / ``INCONCLUSIVE`` -> succeeded; INCONCLUSIVE
      intentionally does NOT fail the mission: it enters the mission-level
      VERIFY_BUILD timer instead of being treated as MECHANISM_ERROR (A4/P0-6).
    - anything else -> failed, with the longest matching ``MissionResult``
      member (``INCONCLUSIVE`` is matched before the generic fallback).
    """
    prefix = data.split(":", 1)[0].strip()
    if prefix in {"SUCCESS", "STABLE", MissionResult.INCONCLUSIVE.value}:
        return True, None, data
    result = MissionResult.__members__.get(prefix)
    if result is None:
        result = MissionResult.MECHANISM_ERROR
    return False, result, data


@dataclass
class MissionMachine:
    config: MissionConfig = field(default_factory=MissionConfig)
    cargo: Cargo = field(default_factory=Cargo)
    state: MissionState = MissionState.WAIT_FOR_COMMUNICATION
    retries: int = 0
    result: MissionResult = MissionResult.RUNNING
    detail: str = ""
    entered_at: float = field(default_factory=time.monotonic)
    def _enter(self, state: MissionState, now: float) -> None:
        self.state = state
        self.entered_at = now
        self.retries = 0
        self.result = MissionResult.RUNNING
        self.detail = ""

    def fail(self, result: MissionResult, detail: str, now: float | None = None) -> MissionState:
        if result is MissionResult.RUNNING or result is MissionResult.SUCCESS:
            raise ValueError("failure requires a failure result")
        self.result = result
        self.detail = detail
        self.state = MissionState.FAILED
        self.entered_at = time.monotonic() if now is None else now
        return self.state

    def tick(
        self,
        *,
        now: float | None = None,
        communication_ok: bool = True,
        emergency_stop: bool = False,
        physical_start: bool = False,
        action_succeeded: bool = False,
        action_failed: bool = False,
        failure_result: MissionResult = MissionResult.MECHANISM_ERROR,
        failure_detail: str = "action reported failure",
    ) -> MissionState:
        now = time.monotonic() if now is None else now
        if emergency_stop:
            self._enter(MissionState.SAFE_STOP, now)
            self.result = MissionResult.SAFETY_STOP
            self.detail = "emergency stop active"
            return self.state
        if self.state is MissionState.WAIT_FOR_COMMUNICATION:
            # A2 / P0-1: 上电握手期间 communication_ok=False 是正常时序。
            # 在 startup_wait_timeout_s 内持续等待、不评估、不 fail；
            # 超时才 fail(COMMUNICATION_ERROR)。通信就绪则转入 SELF_CHECK，
            # 并在同一 tick 内继续按 SELF_CHECK 语义处理（mechanism 就绪即前进）。
            if not communication_ok:
                if now - self.entered_at > self.config.startup_wait_timeout_s:
                    return self.fail(
                        MissionResult.COMMUNICATION_ERROR,
                        "communication not ready within startup window",
                        now,
                    )
                return self.state
            self._enter(MissionState.SELF_CHECK, now)
        elif not communication_ok:
            # 运行时语义不变：离开启动等待后，心跳丢失仍立即按失败处理。
            return self.fail(MissionResult.COMMUNICATION_ERROR, "hardware heartbeat lost", now)
        if self.state in {MissionState.COMPLETE, MissionState.FAILED, MissionState.SAFE_STOP}:
            return self.state
        if now - self.entered_at > self.config.state_timeout_s:
            return self._retry_or_fail(MissionResult.TIMEOUT, "state timeout", now)

        if self.state is MissionState.SELF_CHECK and action_succeeded:
            self._enter(MissionState.WAIT_FOR_PHYSICAL_START, now)
        elif self.state is MissionState.WAIT_FOR_PHYSICAL_START and physical_start:
            self._enter(MissionState.GO_TO_ORANGE, now)
        elif action_failed:
            return self._retry_or_fail(failure_result, failure_detail, now)
        elif action_succeeded:
            self._advance_after_success(now)
        return self.state

    def _retry_or_fail(self, result: MissionResult, detail: str, now: float) -> MissionState:
        self.retries += 1
        self.entered_at = now
        if self.retries <= self.config.max_retries:
            self.result = result
            self.detail = f"retry {self.retries}: {detail}"
            return self.state
        return self.fail(result, f"retry limit exceeded: {detail}", now)

    def _advance_after_success(self, now: float) -> None:
        if self.state is MissionState.GO_TO_ORANGE:
            self._enter(MissionState.PICK_ORANGE, now)
        elif self.state is MissionState.PICK_ORANGE:
            self.cargo.add(CubeColor.ORANGE)
            if self.cargo.orange < self.config.orange_target:
                self._enter(MissionState.GO_TO_ORANGE, now)
            elif self.config.purple_target:
                self._enter(MissionState.GO_TO_PURPLE, now)
            else:
                self._enter(MissionState.GO_TO_BUILD, now)
        elif self.state is MissionState.GO_TO_PURPLE:
            self._enter(MissionState.PICK_PURPLE, now)
        elif self.state is MissionState.PICK_PURPLE:
            self.cargo.add(CubeColor.PURPLE)
            self._enter(MissionState.GO_TO_BUILD, now)
        elif self.state is MissionState.GO_TO_BUILD:
            self._enter(MissionState.PLACE_ORANGE, now)
        elif self.state is MissionState.PLACE_ORANGE:
            self.cargo.remove(CubeColor.ORANGE)
            if self.cargo.orange:
                self._enter(MissionState.PLACE_ORANGE, now)
            elif self.cargo.purple:
                self._enter(MissionState.PLACE_PURPLE, now)
            else:
                self._enter(MissionState.RETREAT, now)
        elif self.state is MissionState.PLACE_PURPLE:
            self.cargo.remove(CubeColor.PURPLE)
            self._enter(MissionState.RETREAT, now)
        elif self.state is MissionState.RETREAT:
            self._enter(MissionState.VERIFY_BUILD, now)
        elif self.state is MissionState.VERIFY_BUILD:
            self._enter(MissionState.COMPLETE, now)
            self.result = MissionResult.SUCCESS
            self.detail = "mission complete"
