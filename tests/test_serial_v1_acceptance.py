import importlib.util
import unittest
from pathlib import Path

from robogame_core.serial_protocol import (
    MSG_TYPE_ACK,
    MSG_TYPE_IMU,
    MSG_TYPE_ODOM,
    MSG_TYPE_STATUS,
    STATUS_IMU_VALID,
    STATUS_WATCHDOG_STOP,
    ImuSample,
    OdomSample,
    StatusSample,
    encode_ack,
    encode_frame,
    encode_imu,
    encode_odom,
    encode_status,
)


_TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "serial_v1_acceptance.py"
_SPEC = importlib.util.spec_from_file_location("serial_v1_acceptance", _TOOL_PATH)
acceptance = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(acceptance)


class FakeSerial:
    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.pending = b""
        self.written = b""

    def write(self, data):
        self.written += data
        self.pending += self.response_factory(data)
        return len(data)

    def read(self, size=1):
        data, self.pending = self.pending[:size], self.pending[size:]
        return data


class SerialV1AcceptanceTests(unittest.TestCase):
    def test_loopback_requires_exact_v1_frame_echo(self):
        port = FakeSerial(lambda data: data)
        passed, detail = acceptance.verify_loopback(port, timeout_s=0.01)
        self.assertTrue(passed, detail)
        self.assertEqual(port.written.hex().upper(), "AA5501030100010001BF97")

    def test_loopback_rejects_changed_bytes(self):
        port = FakeSerial(lambda data: data[:-1] + b"\x00")
        passed, detail = acceptance.verify_loopback(port, timeout_s=0.01)
        self.assertFalse(passed)
        self.assertIn("expected=", detail)

    def test_handshake_accepts_matching_success_ack(self):
        port = FakeSerial(
            lambda _data: encode_frame(MSG_TYPE_ACK, 8, encode_ack(17, result=0))
        )
        passed, detail = acceptance.verify_handshake(
            port, sequence=17, timeout_s=0.01
        )
        self.assertTrue(passed, detail)

    def test_handshake_rejects_wrong_sequence_or_error(self):
        for payload in (encode_ack(16, 0), encode_ack(17, 4)):
            with self.subTest(payload=payload):
                port = FakeSerial(lambda _data, payload=payload: encode_frame(0x13, 1, payload))
                passed, _ = acceptance.verify_handshake(
                    port, sequence=17, timeout_s=0.01
                )
                self.assertFalse(passed)

    def test_status_accepts_matching_ack_and_valid_status(self):
        status = StatusSample(1250, STATUS_IMU_VALID, 0, 24150, 7)
        port = FakeSerial(
            lambda _data: (
                encode_frame(MSG_TYPE_ACK, 8, encode_ack(17, result=0))
                + encode_frame(MSG_TYPE_STATUS, 9, encode_status(status))
            )
        )
        passed, detail = acceptance.verify_status(
            port, sequence=17, timeout_s=0.01
        )
        self.assertTrue(passed, detail)
        self.assertIn("battery_mv=24150", detail)
        self.assertIn("boot_id=7", detail)

    def test_status_requires_ack_and_status(self):
        responses = (
            encode_frame(MSG_TYPE_ACK, 8, encode_ack(17, result=0)),
            encode_frame(
                MSG_TYPE_STATUS,
                9,
                encode_status(StatusSample(1250, 0, 0, 24150, 7)),
            ),
        )
        for response in responses:
            with self.subTest(response=response):
                port = FakeSerial(lambda _data, response=response: response)
                passed, _ = acceptance.verify_status(
                    port, sequence=17, timeout_s=0.01
                )
                self.assertFalse(passed)

    def test_status_rejects_malformed_payload(self):
        port = FakeSerial(
            lambda _data: (
                encode_frame(MSG_TYPE_ACK, 8, encode_ack(17, result=0))
                + encode_frame(MSG_TYPE_STATUS, 9, b"short")
            )
        )
        passed, detail = acceptance.verify_status(
            port, sequence=17, timeout_s=0.01
        )
        self.assertFalse(passed)
        self.assertIn("status_ok=False", detail)

    def test_safe_suite_decoder_accepts_all_required_telemetry(self):
        frames = acceptance.StreamDecoder().feed(
            encode_frame(MSG_TYPE_ACK, 1, encode_ack(17, result=0))
            + encode_frame(MSG_TYPE_STATUS, 2, encode_status(
                StatusSample(100, 0, 0, 24000, 1)
            ))
            + encode_frame(MSG_TYPE_ODOM, 3, encode_odom(
                OdomSample(101, 0.1, -0.2, 0.3)
            ))
            + encode_frame(MSG_TYPE_IMU, 4, encode_imu(
                ImuSample(102, 0.25, True)
            ))
        )
        decoded = acceptance._decode_suite_frames(frames, 17)
        self.assertTrue(decoded["ack_ok"])
        self.assertEqual(len(decoded["statuses"]), 1)
        self.assertEqual(len(decoded["odoms"]), 1)
        self.assertEqual(len(decoded["imus"]), 1)
        self.assertEqual(decoded["invalid_payloads"], 0)

    def test_safe_suite_decoder_counts_invalid_required_payload(self):
        frames = acceptance.StreamDecoder().feed(
            encode_frame(MSG_TYPE_ODOM, 1, b"short")
        )
        decoded = acceptance._decode_suite_frames(frames, 17)
        self.assertEqual(decoded["invalid_payloads"], 1)

    def test_watchdog_flag_states_are_distinguishable(self):
        clear = StatusSample(100, 0, 0, 24000, 1)
        stopped = StatusSample(300, STATUS_WATCHDOG_STOP, 4001, 24000, 1)
        self.assertFalse(clear.flags & STATUS_WATCHDOG_STOP)
        self.assertTrue(stopped.flags & STATUS_WATCHDOG_STOP)


if __name__ == "__main__":
    unittest.main()
