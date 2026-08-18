from __future__ import annotations

import math
from typing import Any, Mapping


RUNTIME_MODES = {"mock", "field"}
MOCK_PLACEMENT_EVIDENCE_POLICIES = {"mock_qualified", "mock_failed"}


def _flag_word(value: Any, *, true_word: str, false_word: str) -> str:
    if value is None:
        return "UNKNOWN"
    return true_word if bool(value) else false_word


def format_mechanism_status_summary(status: Mapping[str, Any]) -> str:
    """Format the five field-critical status flags on one terminal line."""
    return " ".join(
        (
            "comm=" + _flag_word(
                status.get("communication_ok"), true_word="OK", false_word="BAD"
            ),
            "estop=" + _flag_word(
                status.get("emergency_stop"), true_word="ON", false_word="OFF"
            ),
            "calibrating=" + _flag_word(
                status.get("calibrating"), true_word="YES", false_word="NO"
            ),
            "imu_valid=" + _flag_word(
                status.get("imu_valid"), true_word="YES", false_word="NO"
            ),
            "mechanism_fault=" + _flag_word(
                status.get("mechanism_fault"), true_word="YES", false_word="NO"
            ),
        )
    )


def validate_runtime_evidence_policy(
    *, runtime_mode: str, placement_evidence_policy: str
) -> None:
    """Reject simulated evidence in a field runtime."""
    if runtime_mode not in RUNTIME_MODES:
        raise ValueError(
            f"runtime_mode must be one of: {', '.join(sorted(RUNTIME_MODES))}"
        )
    if (
        runtime_mode == "field"
        and placement_evidence_policy in MOCK_PLACEMENT_EVIDENCE_POLICIES
    ):
        raise ValueError(
            "field runtime cannot use simulated placement evidence: "
            f"{placement_evidence_policy}"
        )


def receive_timestamp_is_fresh(
    *, last_received_s: float | None, now_s: float, timeout_s: float
) -> bool:
    """Return whether one specifically decoded message is recent enough.

    Callers must pass the timestamp for the required message type. A generic
    serial-frame timestamp is not evidence that RobotStatus was decoded.
    """
    if not math.isfinite(now_s):
        raise ValueError("now_s must be finite")
    if not math.isfinite(timeout_s) or timeout_s <= 0.0:
        raise ValueError("timeout_s must be positive and finite")
    if last_received_s is None:
        return False
    if not math.isfinite(last_received_s):
        raise ValueError("last_received_s must be finite or None")
    age_s = now_s - last_received_s
    inside_deadline = age_s < timeout_s or math.isclose(
        age_s, timeout_s, rel_tol=1e-12, abs_tol=1e-12
    )
    return age_s >= 0.0 and inside_deadline


def robot_status_communication_ok(
    *,
    mock_mode: bool,
    serial_open: bool,
    last_decoded_status_s: float | None,
    now_s: float,
    timeout_s: float,
) -> bool:
    """Apply the bridge fail-safe rule for the RobotStatus health flag."""
    if mock_mode:
        return True
    if not serial_open:
        return False
    return receive_timestamp_is_fresh(
        last_received_s=last_decoded_status_s,
        now_s=now_s,
        timeout_s=timeout_s,
    )


def mock_communication_ok(
    *, boot_started_s: float, now_s: float, ready_after_s: float
) -> bool:
    """Faithful mock boot sequence: communication_ok turns true only after the
    simulated power-on window, mirroring the real HELLO->ACK handshake plus the
    first decoded 0x12 STATUS frame.

    A ``ready_after_s`` of 0.0 preserves the legacy lenient mock behaviour
    (immediately healthy) so existing tests and mock_demo keep working.
    """
    if not math.isfinite(boot_started_s) or not math.isfinite(now_s):
        raise ValueError("boot_started_s and now_s must be finite")
    if not math.isfinite(ready_after_s) or ready_after_s < 0.0:
        raise ValueError("ready_after_s must be finite and non-negative")
    if now_s < boot_started_s:
        raise ValueError("now_s cannot precede boot_started_s")
    elapsed = now_s - boot_started_s
    return elapsed >= ready_after_s or math.isclose(elapsed, ready_after_s)


def mock_velocity_limits_ready(
    *, limits_ready: bool, vx: float, vy: float, wz: float
) -> bool:
    """Faithful mock velocity-limit gate: reject non-zero commands when the
    simulated MCU limits are not armed, mirroring the real firmware
    (``rpi_protocol.c`` rejects enable=1 commands while ``rpi_limits_ready``
    is false; over-limit commands latch a protocol fault).

    Zero-speed commands are always accepted (mirrors the real ``enable=0``
    stop path). Returns True when the command may pass.

    A ``limits_ready=True`` preserves the legacy lenient mock behaviour.
    """
    if not all(math.isfinite(v) for v in (vx, vy, wz)):
        raise ValueError("vx, vy, wz must be finite")
    if limits_ready:
        return True
    # 限幅未就绪：非零速度拒绝；零速（停车）永远允许。
    return vx == 0.0 and vy == 0.0 and wz == 0.0
