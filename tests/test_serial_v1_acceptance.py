import importlib.util
import unittest
from pathlib import Path

from robogame_core.serial_protocol import MSG_TYPE_ACK, encode_ack, encode_frame


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


if __name__ == "__main__":
    unittest.main()
