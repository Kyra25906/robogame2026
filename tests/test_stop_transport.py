import struct
import unittest

from robogame_core.serial_protocol import (
    MSG_TYPE_EMERGENCY_STOP,
    MSG_TYPE_VELOCITY,
    StreamDecoder,
)
from robogame_core.stop_transport import dispatch_stop_frames


class StopTransportTests(unittest.TestCase):
    def test_success_sends_zero_velocity_then_emergency_stop(self):
        written = []

        result = dispatch_stop_frames(lambda frame: written.append(frame) or True, 41)

        frames = StreamDecoder().feed(b"".join(written))
        self.assertTrue(result.success)
        self.assertEqual(result.next_sequence, 43)
        self.assertEqual([frame.message_type for frame in frames], [
            MSG_TYPE_VELOCITY,
            MSG_TYPE_EMERGENCY_STOP,
        ])
        self.assertEqual([frame.sequence for frame in frames], [41, 42])
        self.assertEqual(struct.unpack("<fffB", frames[0].payload), (0.0, 0.0, 0.0, 0))
        self.assertEqual(frames[1].payload, b"")

    def test_emergency_stop_is_attempted_when_zero_write_fails(self):
        attempts = []

        def write(frame):
            attempts.append(frame)
            return len(attempts) == 2

        result = dispatch_stop_frames(write, 9)

        frames = StreamDecoder().feed(b"".join(attempts))
        self.assertFalse(result.success)
        self.assertFalse(result.zero_velocity_sent)
        self.assertTrue(result.emergency_stop_sent)
        self.assertEqual(result.next_sequence, 10)
        self.assertEqual([frame.message_type for frame in frames], [
            MSG_TYPE_VELOCITY,
            MSG_TYPE_EMERGENCY_STOP,
        ])
        self.assertEqual([frame.sequence for frame in frames], [9, 9])

    def test_partial_emergency_write_reports_failure(self):
        outcomes = iter((True, False))

        result = dispatch_stop_frames(lambda _frame: next(outcomes), 65535)

        self.assertFalse(result.success)
        self.assertTrue(result.zero_velocity_sent)
        self.assertFalse(result.emergency_stop_sent)
        self.assertEqual(result.next_sequence, 0)

    def test_both_write_failures_preserve_sequence(self):
        result = dispatch_stop_frames(lambda _frame: False, 7)

        self.assertFalse(result.success)
        self.assertEqual(result.next_sequence, 7)


if __name__ == "__main__":
    unittest.main()
