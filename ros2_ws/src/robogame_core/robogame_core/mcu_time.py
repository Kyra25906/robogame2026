from __future__ import annotations

from dataclasses import dataclass


UINT32_MODULUS = 1 << 32
UINT32_HALF_RANGE = 1 << 31


@dataclass(frozen=True)
class McuTickResult:
    accepted: bool
    delta_ms: int | None
    reason: str
    resync: bool = False


def validate_mcu_tick(
    previous_tick_ms: int | None,
    current_tick_ms: int,
    *,
    max_gap_ms: int,
) -> McuTickResult:
    """Validate one uint32 MCU timestamp, including natural wraparound."""
    if not 0 <= current_tick_ms < UINT32_MODULUS:
        raise ValueError("current_tick_ms must fit uint32")
    if previous_tick_ms is not None and not 0 <= previous_tick_ms < UINT32_MODULUS:
        raise ValueError("previous_tick_ms must fit uint32 or be None")
    if not isinstance(max_gap_ms, int) or isinstance(max_gap_ms, bool) or max_gap_ms <= 0:
        raise ValueError("max_gap_ms must be a positive integer")
    if max_gap_ms >= UINT32_HALF_RANGE:
        raise ValueError("max_gap_ms must be below the uint32 half range")
    if previous_tick_ms is None:
        return McuTickResult(True, None, "initial")

    delta_ms = (current_tick_ms - previous_tick_ms) % UINT32_MODULUS
    if delta_ms == 0:
        return McuTickResult(False, 0, "duplicate")
    if delta_ms >= UINT32_HALF_RANGE:
        return McuTickResult(False, delta_ms, "backward")
    if delta_ms > max_gap_ms:
        return McuTickResult(False, delta_ms, "gap", resync=True)
    return McuTickResult(True, delta_ms, "forward")
