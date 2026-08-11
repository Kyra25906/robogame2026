import struct
import unittest

from robogame_core.serial_protocol import (
    MAX_PAYLOAD,
    ProtocolError,
    SOF,
    StreamDecoder,
    VERSION,
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

    def test_stream_accepts_one_byte_at_a_time(self):
        decoder = StreamDecoder()
        encoded = encode_frame(0x12, 7, b"status")
        frames = []
        for byte in encoded:
            frames.extend(decoder.feed(bytes([byte])))
        self.assertEqual(frames, [decode_frame(encoded)])

    def test_stream_extracts_multiple_concatenated_frames(self):
        decoder = StreamDecoder()
        encoded = b"".join(
            encode_frame(0x10, sequence, bytes([sequence]))
            for sequence in range(4)
        )
        frames = decoder.feed(encoded)
        self.assertEqual([frame.sequence for frame in frames], [0, 1, 2, 3])

    def test_start_marker_can_be_split_across_reads(self):
        decoder = StreamDecoder()
        encoded = encode_frame(0x11, 9, b"imu")
        self.assertEqual(decoder.feed(b"noise\xAA"), [])
        frames = decoder.feed(encoded[1:])
        self.assertEqual([frame.sequence for frame in frames], [9])

    def test_payload_may_contain_start_marker(self):
        encoded = encode_frame(0x10, 12, b"left" + SOF + b"right")
        frames = StreamDecoder().feed(encoded)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].payload, b"left" + SOF + b"right")

    def test_corrupt_frame_is_skipped_before_next_valid_frame(self):
        corrupt = bytearray(encode_frame(0x10, 20, b"broken"))
        corrupt[-1] ^= 0x01
        valid = encode_frame(0x10, 21, b"valid")
        frames = StreamDecoder().feed(bytes(corrupt) + valid)
        self.assertEqual([frame.sequence for frame in frames], [21])

    def test_truncated_frame_does_not_hide_following_valid_frame(self):
        truncated = encode_frame(0x11, 30, b"incomplete")[:-3]
        valid = encode_frame(0x11, 31, b"complete")
        decoder = StreamDecoder()
        self.assertEqual(decoder.feed(truncated), [])
        frames = decoder.feed(valid)
        self.assertEqual([frame.sequence for frame in frames], [31])

    def test_wrong_version_is_rejected_and_stream_recovers(self):
        wrong_version = bytearray(encode_frame(0x12, 40, b"status"))
        wrong_version[2] = VERSION + 1
        with self.assertRaisesRegex(ProtocolError, "unsupported protocol version"):
            decode_frame(bytes(wrong_version))

        valid = encode_frame(0x12, 41, b"status")
        frames = StreamDecoder().feed(bytes(wrong_version) + valid)
        self.assertEqual([frame.sequence for frame in frames], [41])

    def test_oversized_declared_payload_is_rejected_and_stream_recovers(self):
        oversized_header = struct.pack(
            "<2sBBHH", SOF, VERSION, 0x12, 50, MAX_PAYLOAD + 1
        )
        valid = encode_frame(0x12, 51, b"status")
        frames = StreamDecoder().feed(oversized_header + valid)
        self.assertEqual([frame.sequence for frame in frames], [51])

    def test_sequence_wrap_is_preserved(self):
        decoder = StreamDecoder()
        frames = decoder.feed(
            encode_frame(0x02, 65535) + encode_frame(0x02, 0)
        )
        self.assertEqual([frame.sequence for frame in frames], [65535, 0])

    def test_long_noise_does_not_prevent_later_recovery(self):
        decoder = StreamDecoder()
        self.assertEqual(decoder.feed(b"\x01" * 10000), [])
        frames = decoder.feed(encode_frame(0x13, 60, b"ack"))
        self.assertEqual([frame.sequence for frame in frames], [60])

    def test_encoder_rejects_out_of_range_fields(self):
        for message_type in (-1, 256):
            with self.subTest(message_type=message_type), self.assertRaises(ValueError):
                encode_frame(message_type, 0)
        for sequence in (-1, 65536):
            with self.subTest(sequence=sequence), self.assertRaises(ValueError):
                encode_frame(0x01, sequence)
        with self.assertRaisesRegex(ValueError, "payload too large"):
            encode_frame(0x01, 0, b"x" * (MAX_PAYLOAD + 1))


if __name__ == "__main__":
    unittest.main()
