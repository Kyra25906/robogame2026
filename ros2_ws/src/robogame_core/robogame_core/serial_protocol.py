from __future__ import annotations

import struct
from dataclasses import dataclass

SOF = b"\xAA\x55"
VERSION = 1
MAX_PAYLOAD = 512
HEADER = struct.Struct("<2sBBHH")
CRC = struct.Struct("<H")
VELOCITY_PAYLOAD = struct.Struct("<fffB")

MSG_TYPE_VELOCITY = 0x01
MSG_TYPE_HEARTBEAT = 0x02
MSG_TYPE_ODOM = 0x10
MSG_TYPE_IMU = 0x11
MSG_TYPE_STATUS = 0x12
MSG_TYPE_ACK = 0x13


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    message_type: int
    sequence: int
    payload: bytes


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
    return VELOCITY_PAYLOAD.pack(vx, vy, wz, mode)


def encode_hello() -> bytes:
    """Return a HELLO frame payload carrying the protocol version."""
    return bytes([VERSION])


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

