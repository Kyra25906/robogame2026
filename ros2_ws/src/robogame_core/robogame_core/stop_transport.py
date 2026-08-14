"""Fail-safe transport helper for the real chassis STOP path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .serial_protocol import (
    MSG_TYPE_EMERGENCY_STOP,
    MSG_TYPE_VELOCITY,
    encode_frame,
    encode_velocity,
)


@dataclass(frozen=True)
class StopDispatchResult:
    success: bool
    next_sequence: int
    zero_velocity_sent: bool
    emergency_stop_sent: bool


def dispatch_stop_frames(
    write_frame: Callable[[bytes], bool], sequence: int
) -> StopDispatchResult:
    """Attempt zero-velocity and emergency-stop frames in that order.

    ``write_frame`` owns I/O exception containment. Both writes are attempted;
    success requires both frames. Sequence advances only for accepted writes,
    matching the bridge's existing serial-send convention.
    """
    zero_sent = write_frame(
        encode_frame(
            MSG_TYPE_VELOCITY,
            sequence,
            encode_velocity(0.0, 0.0, 0.0, mode=0),
        )
    )
    if zero_sent:
        sequence = (sequence + 1) & 0xFFFF

    emergency_sent = write_frame(
        encode_frame(MSG_TYPE_EMERGENCY_STOP, sequence)
    )
    if emergency_sent:
        sequence = (sequence + 1) & 0xFFFF

    return StopDispatchResult(
        success=zero_sent and emergency_sent,
        next_sequence=sequence,
        zero_velocity_sent=zero_sent,
        emergency_stop_sent=emergency_sent,
    )
