from __future__ import annotations

import math


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
