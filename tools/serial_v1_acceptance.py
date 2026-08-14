#!/usr/bin/env python3
"""Validate the RoboGame V1 protocol over a serial port.

``loopback`` requires TX and RX to be physically connected and proves that an
exact V1 frame survives the host serial path. ``handshake`` requires STM32 V1
firmware and proves that a HELLO receives a matching successful ACK. ``status``
adds a passive check that a complete V1 STATUS can be decoded after HELLO.
``safe-suite`` completes the unpowered-actuator acceptance: it sends HELLO and
HEARTBEAT only, requires STATUS/ODOM/IMU telemetry, then stops HEARTBEAT and
requires the MCU watchdog-stop flag.
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
    MSG_TYPE_HEARTBEAT,
    MSG_TYPE_HELLO,
    MSG_TYPE_IMU,
    MSG_TYPE_ODOM,
    MSG_TYPE_STATUS,
    ProtocolError,
    STATUS_WATCHDOG_STOP,
    StreamDecoder,
    decode_ack,
    decode_imu,
    decode_odom,
    decode_status,
    encode_frame,
    encode_heartbeat,
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


def verify_status(
    port: SerialPort, *, sequence: int = 1, timeout_s: float = 1.0
) -> tuple[bool, str]:
    """Send HELLO, then require both its successful ACK and a valid STATUS."""
    port.write(_hello_frame(sequence))
    raw = _read_until(port, time.monotonic() + timeout_s)
    frames = StreamDecoder().feed(raw)
    ack_ok = False
    status_sample = None
    for frame in frames:
        if frame.message_type == MSG_TYPE_ACK:
            try:
                acknowledged, result, _version = decode_ack(frame.payload)
            except ProtocolError:
                continue
            ack_ok |= acknowledged == sequence and result == 0
        elif frame.message_type == MSG_TYPE_STATUS:
            try:
                status_sample = decode_status(frame.payload)
            except ProtocolError:
                continue

    if ack_ok and status_sample is not None:
        return True, (
            "STM32 V1 STATUS PASS: "
            f"tick_ms={status_sample.mcu_tick_ms} "
            f"flags=0x{status_sample.flags:04X} "
            f"error_code={status_sample.error_code} "
            f"battery_mv={status_sample.battery_mv} "
            f"boot_id={status_sample.boot_id}"
        )
    return False, (
        "STM32 V1 STATUS FAIL: "
        f"ack_ok={ack_ok} status_ok={status_sample is not None}; "
        f"received={raw.hex().upper() or '<empty>'}"
    )


def _collect_frames(
    port: SerialPort,
    decoder: StreamDecoder,
    *,
    deadline: float,
    heartbeat_sequence: int | None = None,
    heartbeat_interval_s: float = 0.05,
    clock=time.monotonic,
) -> tuple[list, int | None]:
    frames = []
    next_heartbeat = clock()
    sequence = heartbeat_sequence
    while clock() < deadline:
        now = clock()
        if sequence is not None and now >= next_heartbeat:
            port.write(
                encode_frame(
                    MSG_TYPE_HEARTBEAT,
                    sequence,
                    encode_heartbeat(int(now * 1000.0) & 0xFFFFFFFF),
                )
            )
            sequence = (sequence + 1) & 0xFFFF
            next_heartbeat = now + heartbeat_interval_s
        chunk = port.read(512)
        if chunk:
            frames.extend(decoder.feed(chunk))
        else:
            time.sleep(0.005)
    return frames, sequence


def _decode_suite_frames(frames: list, hello_sequence: int) -> dict:
    result = {
        "ack_ok": False,
        "statuses": [],
        "odoms": [],
        "imus": [],
        "invalid_payloads": 0,
    }
    for frame in frames:
        try:
            if frame.message_type == MSG_TYPE_ACK:
                acknowledged, ack_result, _version = decode_ack(frame.payload)
                result["ack_ok"] |= (
                    acknowledged == hello_sequence and ack_result == 0
                )
            elif frame.message_type == MSG_TYPE_STATUS:
                result["statuses"].append(decode_status(frame.payload))
            elif frame.message_type == MSG_TYPE_ODOM:
                result["odoms"].append(decode_odom(frame.payload))
            elif frame.message_type == MSG_TYPE_IMU:
                result["imus"].append(decode_imu(frame.payload))
        except ProtocolError:
            result["invalid_payloads"] += 1
    return result


def verify_safe_suite(
    port: SerialPort,
    *,
    sequence: int = 1,
    duration_s: float = 2.0,
    watchdog_wait_s: float = 0.40,
) -> tuple[bool, str]:
    """Validate all V1 behavior allowed while actuators remain unpowered."""
    decoder = StreamDecoder()
    port.write(_hello_frame(sequence))
    active_frames, _next_sequence = _collect_frames(
        port,
        decoder,
        deadline=time.monotonic() + duration_s,
        heartbeat_sequence=(sequence + 1) & 0xFFFF,
    )
    active = _decode_suite_frames(active_frames, sequence)

    watchdog_frames, _ = _collect_frames(
        port,
        decoder,
        deadline=time.monotonic() + watchdog_wait_s,
    )
    watchdog = _decode_suite_frames(watchdog_frames, sequence)

    active_watchdog_clear = any(
        not (sample.flags & STATUS_WATCHDOG_STOP)
        for sample in active["statuses"]
    )
    watchdog_set = any(
        sample.flags & STATUS_WATCHDOG_STOP
        for sample in watchdog["statuses"]
    )
    passed = all((
        active["ack_ok"],
        active_watchdog_clear,
        bool(active["odoms"]),
        bool(active["imus"]),
        watchdog_set,
        active["invalid_payloads"] == 0,
        watchdog["invalid_payloads"] == 0,
    ))
    status_count = len(active["statuses"])
    odom_count = len(active["odoms"])
    imu_count = len(active["imus"])
    latest_odom = active["odoms"][-1] if active["odoms"] else None
    latest_imu = active["imus"][-1] if active["imus"] else None
    prefix = "STM32 V1 SAFE SUITE PASS" if passed else "STM32 V1 SAFE SUITE FAIL"
    detail = (
        f"{prefix}: ack_ok={active['ack_ok']} "
        f"status={status_count}({status_count / duration_s:.1f}Hz) "
        f"odom={odom_count}({odom_count / duration_s:.1f}Hz) "
        f"imu={imu_count}({imu_count / duration_s:.1f}Hz) "
        f"watchdog_clear={active_watchdog_clear} watchdog_set={watchdog_set} "
        f"invalid_payloads={active['invalid_payloads'] + watchdog['invalid_payloads']}"
    )
    if latest_odom is not None:
        detail += (
            f" odom_latest=({latest_odom.vx:.4f},{latest_odom.vy:.4f},"
            f"{latest_odom.wz:.4f})"
        )
    if latest_imu is not None:
        detail += (
            f" imu_latest=(gyro_z={latest_imu.gyro_z:.4f},"
            f"valid={latest_imu.valid})"
        )
    return passed, detail


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("loopback", "handshake", "status", "safe-suite")
    )
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--sequence", type=int, default=1)
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument("--watchdog-wait", type=float, default=0.40)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than zero")
    if not 0 <= args.sequence <= 0xFFFF:
        raise SystemExit("--sequence must be between 0 and 65535")
    if args.duration <= 0:
        raise SystemExit("--duration must be greater than zero")
    if args.watchdog_wait <= 0:
        raise SystemExit("--watchdog-wait must be greater than zero")

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
            elif args.mode == "handshake":
                passed, detail = verify_handshake(
                    port, sequence=args.sequence, timeout_s=args.timeout
                )
            elif args.mode == "status":
                passed, detail = verify_status(
                    port, sequence=args.sequence, timeout_s=args.timeout
                )
            else:
                passed, detail = verify_safe_suite(
                    port,
                    sequence=args.sequence,
                    duration_s=args.duration,
                    watchdog_wait_s=args.watchdog_wait,
                )
    except (OSError, serial.SerialException) as exc:
        print(f"SERIAL OPEN/IO FAIL: {exc}", file=sys.stderr)
        return 2

    print(detail)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
