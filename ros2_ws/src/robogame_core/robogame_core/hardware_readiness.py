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
