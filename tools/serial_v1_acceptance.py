#!/usr/bin/env python3
"""Validate the RoboGame V1 protocol over a serial port.

``loopback`` requires TX and RX to be physically connected and proves that an
exact V1 frame survives the host serial path. ``handshake`` requires STM32 V1
firmware and proves that a HELLO receives a matching successful ACK.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Protocol


_ROOT = Path(__file__).resolve().parents[1]
_CORE_SRC = _ROOT / "ros2_ws" / "src" / "robogame_core"
if str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

from robogame_core.serial_protocol import (  # noqa: E402
    MSG_TYPE_ACK,
    MSG_TYPE_HELLO,
    StreamDecoder,
    decode_ack,
    encode_frame,
    encode_hello,
)


class SerialPort(Protocol):
    def write(self, data: bytes) -> int | None: ...
    def read(self, size: int = 1) -> bytes: ...


def _hello_frame(sequence: int) -> bytes:
    return encode_frame(MSG_TYPE_HELLO, sequence, encode_hello())


def _read_until(port: SerialPort, deadline: float, clock=time.monotonic) -> bytes:
    received = bytearray()
    while clock() < deadline:
        chunk = port.read(512)
        if chunk:
            received.extend(chunk)
        else:
            time.sleep(0.005)
    return bytes(received)


def verify_loopback(
    port: SerialPort, *, sequence: int = 1, timeout_s: float = 1.0
) -> tuple[bool, str]:
    """Send one HELLO frame and require an exact byte-for-byte echo."""
    expected = _hello_frame(sequence)
    port.write(expected)
    actual = _read_until(port, time.monotonic() + timeout_s)
    if actual == expected:
        return True, f"V1 LOOPBACK PASS: {actual.hex().upper()}"
    return False, (
        "V1 LOOPBACK FAIL: "
        f"expected={expected.hex().upper()} actual={actual.hex().upper() or '<empty>'}"
    )


def verify_handshake(
    port: SerialPort, *, sequence: int = 1, timeout_s: float = 1.0
) -> tuple[bool, str]:
    """Send HELLO and require a valid ACK for its sequence with result zero."""
    port.write(_hello_frame(sequence))
    raw = _read_until(port, time.monotonic() + timeout_s)
    frames = StreamDecoder().feed(raw)
    for frame in frames:
        if frame.message_type != MSG_TYPE_ACK:
            continue
        acknowledged, result, version = decode_ack(frame.payload)
        if acknowledged == sequence and result == 0:
            return True, (
                "STM32 V1 HANDSHAKE PASS: "
                f"ack_sequence={acknowledged} result={result} version={version}"
            )
    return False, (
        "STM32 V1 HANDSHAKE FAIL: no matching successful ACK; "
        f"received={raw.hex().upper() or '<empty>'}"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("loopback", "handshake"))
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--sequence", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than zero")
    if not 0 <= args.sequence <= 0xFFFF:
        raise SystemExit("--sequence must be between 0 and 65535")

    import serial

    try:
        with serial.Serial(
            args.port, args.baud, timeout=min(args.timeout, 0.05), write_timeout=args.timeout
        ) as port:
            port.reset_input_buffer()
            if args.mode == "loopback":
                passed, detail = verify_loopback(
                    port, sequence=args.sequence, timeout_s=args.timeout
                )
            else:
                passed, detail = verify_handshake(
                    port, sequence=args.sequence, timeout_s=args.timeout
                )
    except (OSError, serial.SerialException) as exc:
        print(f"SERIAL OPEN/IO FAIL: {exc}", file=sys.stderr)
        return 2

    print(detail)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
