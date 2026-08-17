import unittest

from robogame_core.mechanism_transport import (
    MechanismCommandTracker,
    PollAction,
)
from robogame_core.serial_protocol import (
    MechanismCommand,
    MechanismOperation,
    MechanismState,
    MechanismStatus,
)


def command(command_id=7):
    return MechanismCommand(command_id, MechanismOperation.GRAB, 0, 3000)


def status(state, error_code=0, command_id=7):
    return MechanismStatus(
        command_id,
        MechanismOperation.GRAB,
        state,
        error_code,
        10,
    )


class MechanismCommandTrackerTests(unittest.TestCase):
    def test_success_requires_matching_ack_and_terminal_status(self):
        tracker = MechanismCommandTracker(command(), started_s=1.0, timeout_s=3.0)
        tracker.mark_sent(20, 1.0)
        self.assertFalse(tracker.handle_ack(19, 0, 1.01))
        self.assertTrue(tracker.handle_ack(20, 0, 1.02))
        self.assertTrue(tracker.handle_status(status(MechanismState.ACCEPTED), 1.03))
        self.assertTrue(tracker.handle_status(status(MechanismState.RUNNING), 1.10))
        self.assertTrue(tracker.handle_status(status(MechanismState.SUCCEEDED), 1.50))
        self.assertTrue(tracker.result.success)
        self.assertEqual(tracker.result.error_code, 0)

    def test_ack_rejection_is_terminal_failure(self):
        tracker = MechanismCommandTracker(command(), started_s=0.0, timeout_s=3.0)
        tracker.mark_sent(2, 0.0)
        tracker.handle_ack(2, 5, 0.01)
        self.assertFalse(tracker.result.success)
        self.assertEqual(tracker.result.error_code, 5)

    def test_retry_keeps_command_id_but_accepts_new_frame_sequence(self):
        tracker = MechanismCommandTracker(command(42), started_s=0.0, timeout_s=3.0)
        tracker.mark_sent(100, 0.0)
        self.assertEqual(tracker.poll(0.11), PollAction.RETRY)
        tracker.mark_sent(101, 0.11)
        self.assertEqual(tracker.command.command_id, 42)
        self.assertFalse(tracker.handle_ack(100, 0, 0.12))
        self.assertTrue(tracker.handle_ack(101, 0, 0.12))

    def test_status_silence_retries_then_fails(self):
        tracker = MechanismCommandTracker(command(), started_s=0.0, timeout_s=5.0)
        for sequence, now in ((1, 0.0), (2, 0.11), (3, 0.22)):
            tracker.mark_sent(sequence, now)
            tracker.handle_ack(sequence, 0, now + 0.01)
            if sequence < 3:
                self.assertEqual(tracker.poll(now + 0.32), PollAction.RETRY)
        self.assertEqual(tracker.poll(0.53), PollAction.NONE)
        self.assertFalse(tracker.result.success)
        self.assertEqual(tracker.result.error_code, 9003)

    def test_failed_cancelled_and_rejected_preserve_mcu_error(self):
        for terminal in (
            MechanismState.FAILED,
            MechanismState.CANCELLED,
            MechanismState.REJECTED,
        ):
            with self.subTest(terminal=terminal):
                tracker = MechanismCommandTracker(command(), started_s=0.0, timeout_s=2.0)
                tracker.mark_sent(1, 0.0)
                tracker.handle_ack(1, 0, 0.01)
                tracker.handle_status(status(terminal, 1002), 0.02)
                self.assertFalse(tracker.result.success)
                self.assertEqual(tracker.result.error_code, 1002)

    def test_disconnect_or_restart_can_cancel_pending_command(self):
        tracker = MechanismCommandTracker(command(), started_s=0.0, timeout_s=2.0)
        tracker.mark_sent(1, 0.0)
        tracker.cancel(9004, "MCU restarted", 0.5)
        self.assertFalse(tracker.result.success)
        self.assertEqual(tracker.result.error_code, 9004)

    def test_backward_state_and_invalid_success_fail_closed(self):
        tracker = MechanismCommandTracker(command(), started_s=0.0, timeout_s=2.0)
        tracker.mark_sent(1, 0.0)
        tracker.handle_ack(1, 0, 0.01)
        tracker.handle_status(status(MechanismState.RUNNING), 0.02)
        tracker.handle_status(status(MechanismState.ACCEPTED), 0.03)
        self.assertEqual(tracker.result.error_code, 9006)

        tracker = MechanismCommandTracker(command(), started_s=0.0, timeout_s=2.0)
        tracker.mark_sent(1, 0.0)
        tracker.handle_ack(1, 0, 0.01)
        tracker.handle_status(status(MechanismState.SUCCEEDED, 7), 0.02)
        self.assertEqual(tracker.result.error_code, 9006)


if __name__ == "__main__":
    unittest.main()
