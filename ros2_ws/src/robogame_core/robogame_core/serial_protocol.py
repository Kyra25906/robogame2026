from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from math import isfinite

SOF = b"\xAA\x55"
VERSION = 1
MAX_PAYLOAD = 512
HEADER = struct.Struct("<2sBBHH")
CRC = struct.Struct("<H")
VELOCITY_PAYLOAD = struct.Struct("<fffB")
HEARTBEAT_PAYLOAD = struct.Struct("<I")
ODOM_PAYLOAD = struct.Struct("<Ifff")
IMU_PAYLOAD = struct.Struct("<IfB")
STATUS_PAYLOAD = struct.Struct("<IHHHH")
MECHANISM_COMMAND_PAYLOAD = struct.Struct("<HBBiI")
MECHANISM_STATUS_PAYLOAD = struct.Struct("<HBBHI")
ACK_PAYLOAD = struct.Struct("<HBB")

MSG_TYPE_VELOCITY = 0x01
MSG_TYPE_HEARTBEAT = 0x02
MSG_TYPE_HELLO = 0x03
MSG_TYPE_ODOM = 0x10
MSG_TYPE_IMU = 0x11
MSG_TYPE_STATUS = 0x12
MSG_TYPE_ACK = 0x13
MSG_TYPE_MECHANISM_COMMAND = 0x20
MSG_TYPE_MECHANISM_STATUS = 0x21
MSG_TYPE_EMERGENCY_STOP = 0x22

STATUS_EMERGENCY_STOP = 1 << 0
STATUS_PHYSICAL_START = 1 << 1
STATUS_IMU_CALIBRATING = 1 << 2
STATUS_IMU_VALID = 1 << 3
STATUS_GRIPPER_CLOSED = 1 << 4
STATUS_CUBE_PRESENT = 1 << 5
STATUS_MECHANISM_FAULT = 1 << 6
STATUS_CHASSIS_FAULT = 1 << 7
STATUS_WATCHDOG_STOP = 1 << 8
STATUS_BATTERY_LOW = 1 << 9
STATUS_KNOWN_MASK = (1 << 10) - 1


class MechanismOperation(IntEnum):
    GRAB = 1
    RELEASE = 2
    LIFT_ABS = 3
    STOP = 5
    HOME = 6


class MechanismState(IntEnum):
    ACCEPTED = 1
    RUNNING = 2
    SUCCEEDED = 3
    FAILED = 4
    CANCELLED = 5
    REJECTED = 6


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    message_type: int
    sequence: int
    payload: bytes


@dataclass(frozen=True)
class OdomSample:
    mcu_tick_ms: int
    vx_mps: float
    vy_mps: float
    wz_radps: float


@dataclass(frozen=True)
class ImuSample:
    mcu_tick_ms: int
    gyro_z_radps: float
    valid: bool


@dataclass(frozen=True)
class StatusSample:
    mcu_tick_ms: int
    flags: int
    error_code: int
    battery_mv: int
    boot_id: int

    def has(self, flag: int) -> bool:
        return bool(self.flags & flag)


@dataclass(frozen=True)
class MechanismCommand:
    command_id: int
    operation: MechanismOperation
    parameter: int
    timeout_ms: int
    flags: int = 0


@dataclass(frozen=True)
class MechanismStatus:
    command_id: int
    operation: MechanismOperation
    state: MechanismState
    error_code: int
    duration_ms: int


def crc16_ccitt(data: bytes, initial: int = 0xFFFF) -> int:
    crc = initial
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def encode_frame(message_type: int, sequence: int, payload: bytes = b"") -> bytes:
    if not 0 <= message_type <= 255:
        raise ValueError("message_type must fit uint8")
    if not 0 <= sequence <= 65535:
        raise ValueError("sequence must fit uint16")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too large")
    header = HEADER.pack(SOF, VERSION, message_type, sequence, len(payload))
    body = header + payload
    return body + CRC.pack(crc16_ccitt(body))


def decode_frame(data: bytes) -> Frame:
    if len(data) < HEADER.size + CRC.size:
        raise ProtocolError("frame is incomplete")
    sof, version, message_type, sequence, payload_length = HEADER.unpack_from(data)
    if sof != SOF:
        raise ProtocolError("invalid start marker")
    if version != VERSION:
        raise ProtocolError(f"unsupported protocol version {version}")
    if payload_length > MAX_PAYLOAD:
        raise ProtocolError("declared payload is too large")
    expected_length = HEADER.size + payload_length + CRC.size
    if len(data) != expected_length:
        raise ProtocolError("frame length mismatch")
    expected_crc = CRC.unpack_from(data, len(data) - CRC.size)[0]
    if crc16_ccitt(data[:-CRC.size]) != expected_crc:
        raise ProtocolError("CRC mismatch")
    return Frame(message_type, sequence, data[HEADER.size:-CRC.size])


def encode_velocity(vx: float, vy: float, wz: float, mode: int = 1) -> bytes:
    _require_finite(vx, vy, wz)
    if mode not in (0, 1):
        raise ValueError("velocity mode must be 0 (stop) or 1 (enabled)")
    return VELOCITY_PAYLOAD.pack(vx, vy, wz, mode)


def encode_hello() -> bytes:
    """Return a HELLO frame payload carrying the protocol version."""
    return bytes([VERSION])


def encode_heartbeat(host_tick_ms: int) -> bytes:
    return HEARTBEAT_PAYLOAD.pack(_uint(host_tick_ms, 32, "host_tick_ms"))


def decode_heartbeat(payload: bytes) -> int:
    _require_size(payload, HEARTBEAT_PAYLOAD, "HEARTBEAT")
    return HEARTBEAT_PAYLOAD.unpack(payload)[0]


def encode_odom(sample: OdomSample) -> bytes:
    _require_finite(sample.vx_mps, sample.vy_mps, sample.wz_radps)
    return ODOM_PAYLOAD.pack(
        _uint(sample.mcu_tick_ms, 32, "mcu_tick_ms"),
        sample.vx_mps,
        sample.vy_mps,
        sample.wz_radps,
    )


def decode_odom(payload: bytes) -> OdomSample:
    _require_size(payload, ODOM_PAYLOAD, "ODOM")
    sample = OdomSample(*ODOM_PAYLOAD.unpack(payload))
    _require_finite(sample.vx_mps, sample.vy_mps, sample.wz_radps)
    return sample


def encode_imu(sample: ImuSample) -> bytes:
    _require_finite(sample.gyro_z_radps)
    return IMU_PAYLOAD.pack(
        _uint(sample.mcu_tick_ms, 32, "mcu_tick_ms"),
        sample.gyro_z_radps,
        int(sample.valid),
    )


def decode_imu(payload: bytes) -> ImuSample:
    _require_size(payload, IMU_PAYLOAD, "IMU")
    tick, gyro_z, valid = IMU_PAYLOAD.unpack(payload)
    if valid not in (0, 1):
        raise ProtocolError("IMU valid must be 0 or 1")
    _require_finite(gyro_z)
    return ImuSample(tick, gyro_z, bool(valid))


def encode_status(sample: StatusSample) -> bytes:
    _validate_status(sample)
    return STATUS_PAYLOAD.pack(
        sample.mcu_tick_ms,
        sample.flags,
        sample.error_code,
        sample.battery_mv,
        sample.boot_id,
    )


def decode_status(payload: bytes) -> StatusSample:
    _require_size(payload, STATUS_PAYLOAD, "STATUS")
    sample = StatusSample(*STATUS_PAYLOAD.unpack(payload))
    _validate_status(sample, ProtocolError)
    return sample


def encode_mechanism_command(command: MechanismCommand) -> bytes:
    if command.flags != 0:
        raise ValueError("mechanism command flags must be zero in protocol V1")
    return MECHANISM_COMMAND_PAYLOAD.pack(
        _uint(command.command_id, 16, "command_id"),
        int(MechanismOperation(command.operation)),
        _uint(command.flags, 8, "flags"),
        _int32(command.parameter, "parameter"),
        _uint(command.timeout_ms, 32, "timeout_ms"),
    )


def decode_mechanism_command(payload: bytes) -> MechanismCommand:
    _require_size(payload, MECHANISM_COMMAND_PAYLOAD, "MECHANISM_COMMAND")
    command_id, operation, flags, parameter, timeout_ms = MECHANISM_COMMAND_PAYLOAD.unpack(payload)
    try:
        operation_value = MechanismOperation(operation)
    except ValueError as exc:
        raise ProtocolError(f"unknown mechanism operation {operation}") from exc
    if flags != 0:
        raise ProtocolError("mechanism command flags must be zero in protocol V1")
    return MechanismCommand(command_id, operation_value, parameter, timeout_ms, flags)


def encode_mechanism_status(status: MechanismStatus) -> bytes:
    return MECHANISM_STATUS_PAYLOAD.pack(
        _uint(status.command_id, 16, "command_id"),
        int(MechanismOperation(status.operation)),
        int(MechanismState(status.state)),
        _uint(status.error_code, 16, "error_code"),
        _uint(status.duration_ms, 32, "duration_ms"),
    )


def decode_mechanism_status(payload: bytes) -> MechanismStatus:
    _require_size(payload, MECHANISM_STATUS_PAYLOAD, "MECHANISM_STATUS")
    command_id, operation, state, error_code, duration_ms = MECHANISM_STATUS_PAYLOAD.unpack(payload)
    try:
        operation_value = MechanismOperation(operation)
        state_value = MechanismState(state)
    except ValueError as exc:
        raise ProtocolError("unknown mechanism operation or state") from exc
    return MechanismStatus(command_id, operation_value, state_value, error_code, duration_ms)


def encode_ack(acknowledged_sequence: int, result: int = 0) -> bytes:
    return ACK_PAYLOAD.pack(
        _uint(acknowledged_sequence, 16, "acknowledged_sequence"),
        _uint(result, 8, "result"),
        VERSION,
    )


def decode_ack(payload: bytes) -> tuple[int, int, int]:
    _require_size(payload, ACK_PAYLOAD, "ACK")
    sequence, result, protocol_version = ACK_PAYLOAD.unpack(payload)
    if protocol_version != VERSION:
        raise ProtocolError(f"ACK protocol version {protocol_version} is unsupported")
    return sequence, result, protocol_version


def _require_size(payload: bytes, layout: struct.Struct, name: str) -> None:
    if len(payload) != layout.size:
        raise ProtocolError(f"{name} payload must be {layout.size} bytes, got {len(payload)}")


def _require_finite(*values: float) -> None:
    if not all(isfinite(value) for value in values):
        raise ProtocolError("floating-point payload contains NaN or infinity")


def _uint(value: int, bits: int, name: str) -> int:
    if not 0 <= int(value) < 1 << bits:
        raise ValueError(f"{name} must fit uint{bits}")
    return int(value)


def _int32(value: int, name: str) -> int:
    if not -(1 << 31) <= int(value) < 1 << 31:
        raise ValueError(f"{name} must fit int32")
    return int(value)


def _validate_status(sample: StatusSample, error_type=ValueError) -> None:
    try:
        _uint(sample.mcu_tick_ms, 32, "mcu_tick_ms")
        _uint(sample.flags, 16, "flags")
        _uint(sample.error_code, 16, "error_code")
        _uint(sample.battery_mv, 16, "battery_mv")
        _uint(sample.boot_id, 16, "boot_id")
    except ValueError as exc:
        raise error_type(str(exc)) from exc
    if sample.flags & ~STATUS_KNOWN_MASK:
        raise error_type("STATUS reserved flag bits must be zero")


class StreamDecoder:
    """Incrementally extracts frames and resynchronizes after corrupted bytes."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[Frame]:
        self._buffer.extend(chunk)
        frames: list[Frame] = []
        while True:
            start = self._buffer.find(SOF)
            if start < 0:
                self._buffer[:] = self._buffer[-1:]
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < HEADER.size:
                break
            _, _, _, _, length = HEADER.unpack_from(self._buffer)
            total = HEADER.size + length + CRC.size
            if length > MAX_PAYLOAD:
                del self._buffer[0]
                continue
            if len(self._buffer) < total:
                break
            raw = bytes(self._buffer[:total])
            try:
                frames.append(decode_frame(raw))
                del self._buffer[:total]
            except ProtocolError:
                del self._buffer[0]
        return frames
