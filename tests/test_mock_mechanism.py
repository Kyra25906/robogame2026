import unittest

from robogame_core.manipulator import MechanismOperation
from robogame_core.mock_mechanism import MockMechanismState, execute_mock_mechanism


class MockMechanismTests(unittest.TestCase):
    def test_successful_grab_sets_cube_and_gripper_evidence(self):
        result = execute_mock_mechanism(
            MockMechanismState(), MechanismOperation.GRAB
        )

        self.assertTrue(result.success)
        self.assertTrue(result.state.cube_present)
        self.assertTrue(result.state.gripper_closed)

    def test_failed_grab_does_not_change_state(self):
        initial = MockMechanismState()
        result = execute_mock_mechanism(
            initial, MechanismOperation.GRAB, configured_success=False
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, 1101)
        self.assertEqual(result.state, initial)

    def test_successful_release_clears_cube_and_gripper_evidence(self):
        initial = MockMechanismState(cube_present=True, gripper_closed=True)
        result = execute_mock_mechanism(initial, MechanismOperation.RELEASE)

        self.assertTrue(result.success)
        self.assertFalse(result.state.cube_present)
        self.assertFalse(result.state.gripper_closed)

    def test_lift_updates_height_without_changing_cargo_evidence(self):
        initial = MockMechanismState(cube_present=True, gripper_closed=True)
        result = execute_mock_mechanism(
            initial, MechanismOperation.LIFT, height_m=0.20
        )

        self.assertTrue(result.success)
        self.assertEqual(result.state.lift_height_m, 0.20)
        self.assertTrue(result.state.cube_present)
        self.assertTrue(result.state.gripper_closed)

    def test_invalid_or_configured_failed_lift_preserves_state(self):
        initial = MockMechanismState(cube_present=True, gripper_closed=True)
        outside = execute_mock_mechanism(
            initial, MechanismOperation.LIFT, height_m=0.81
        )
        failed = execute_mock_mechanism(
            initial,
            MechanismOperation.LIFT,
            height_m=0.20,
            configured_success=False,
        )

        self.assertFalse(outside.success)
        self.assertEqual(outside.error_code, 1002)
        self.assertEqual(outside.state, initial)
        self.assertFalse(failed.success)
        self.assertEqual(failed.error_code, 1103)
        self.assertEqual(failed.state, initial)

    def test_lift_requires_a_height(self):
        with self.assertRaisesRegex(ValueError, "height_m"):
            execute_mock_mechanism(
                MockMechanismState(), MechanismOperation.LIFT
            )


if __name__ == "__main__":
    unittest.main()
