import unittest

from robogame_core.models import MissionResult, control_safety_result


class ControlSafetyTests(unittest.TestCase):
    def test_missing_status_blocks_control(self):
        self.assertEqual(
            control_safety_result(status_received=False),
            MissionResult.COMMUNICATION_ERROR,
        )

    def test_healthy_status_allows_control(self):
        self.assertIsNone(control_safety_result(True, communication_ok=True))

    def test_emergency_stop_has_highest_priority(self):
        self.assertEqual(
            control_safety_result(True, communication_ok=False, emergency_stop=True),
            MissionResult.SAFETY_STOP,
        )

    def test_mechanism_fault_blocks_manipulation(self):
        self.assertEqual(
            control_safety_result(True, communication_ok=True, mechanism_fault=True),
            MissionResult.MECHANISM_ERROR,
        )


if __name__ == "__main__":
    unittest.main()
