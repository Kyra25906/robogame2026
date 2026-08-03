import unittest

from robogame_core.serial_protocol import (
    ProtocolError,
    StreamDecoder,
    decode_frame,
    encode_frame,
    encode_velocity,
)


class SerialProtocolTests(unittest.TestCase):
    def test_round_trip(self):
        payload = encode_velocity(0.2, -0.1, 0.5)
        frame = decode_frame(encode_frame(1, 42, payload))
        self.assertEqual(frame.message_type, 1)
        self.assertEqual(frame.sequence, 42)
        self.assertEqual(frame.payload, payload)

    def test_crc_rejects_corruption(self):
        data = bytearray(encode_frame(2, 1, b"grab"))
        data[-3] ^= 0x40
        with self.assertRaises(ProtocolError):
            decode_frame(bytes(data))

    def test_stream_resynchronizes(self):
        decoder = StreamDecoder()
        first = encode_frame(1, 1, b"a")
        second = encode_frame(1, 2, b"b")
        self.assertEqual(decoder.feed(b"noise" + first[:4]), [])
        frames = decoder.feed(first[4:] + b"bad" + second)
        self.assertEqual([frame.sequence for frame in frames], [1, 2])


if __name__ == "__main__":
    unittest.main()

