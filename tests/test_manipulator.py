import unittest

from robogame_core.manipulator import (
    CancellationDecision,
    GrabVerificationPolicy,
    ManipulatorState,
    MechanismOperation,
    PlaceVerificationPolicy,
    VerificationDecision,
    cancellation_decision,
    ServiceWaitDecision,
    calculate_alignment_command,
    format_mechanism_failure,
    format_service_exception,
    grab_verification_decision,
    place_verification_decision,
    select_place_height,
    service_wait_decision,
)


def command(distance_m=0.24, lateral_m=0.0, **overrides):
    parameters = {
        "distance_m": distance_m,
        "lateral_m": lateral_m,
        "target_distance_m": 0.24,
        "distance_tolerance_m": 0.025,
        "lateral_tolerance_m": 0.018,
        "kp_distance": 0.8,
        "kp_lateral": 1.2,
        "max_speed": 0.18,
    }
    parameters.update(overrides)
    return calculate_alignment_command(**parameters)


class ManipulatorAlignmentTests(unittest.TestCase):
    def test_target_inside_tolerance_is_ready_and_stopped(self):
        result = command(distance_m=0.25, lateral_m=0.01)

        self.assertTrue(result.ready_to_grab)
        self.assertEqual((result.linear_x, result.linear_y), (0.0, 0.0))

    def test_distant_target_commands_forward_motion(self):
        result = command(distance_m=0.34)

        self.assertFalse(result.ready_to_grab)
        self.assertGreater(result.linear_x, 0.0)

    def test_close_target_commands_reverse_motion(self):
        self.assertLess(command(distance_m=0.14).linear_x, 0.0)

    def test_lateral_command_has_correct_direction(self):
        self.assertLess(command(lateral_m=0.05).linear_y, 0.0)
        self.assertGreater(command(lateral_m=-0.05).linear_y, 0.0)

    def test_each_axis_is_limited_to_max_speed(self):
        result = command(distance_m=2.0, lateral_m=-2.0)

        self.assertEqual(result.linear_x, 0.18)
        self.assertEqual(result.linear_y, 0.18)

    def test_invalid_limits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "max_speed"):
            command(max_speed=0.0)


class PlaceHeightTests(unittest.TestCase):
    def test_each_layer_uses_its_configured_height(self):
        heights = [0.10, 0.20, 0.30]

        self.assertEqual(select_place_height(0, heights), 0.10)
        self.assertEqual(select_place_height(1, heights), 0.20)
        self.assertEqual(select_place_height(2, heights), 0.30)

    def test_layers_beyond_configuration_retain_highest_height(self):
        self.assertEqual(select_place_height(5, [0.10, 0.20, 0.30]), 0.30)

    def test_invalid_height_configuration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            select_place_height(0, [])
        with self.assertRaisesRegex(ValueError, "positive"):
            select_place_height(0, [0.10, 0.0])
        with self.assertRaisesRegex(ValueError, "negative"):
            select_place_height(-1, [0.10])


class ServiceWaitTests(unittest.TestCase):
    def test_ready_service_can_be_called_immediately(self):
        self.assertEqual(
            service_wait_decision(ready=True, elapsed_s=0.0, timeout_s=1.0),
            ServiceWaitDecision.READY,
        )

    def test_unavailable_service_waits_before_deadline(self):
        self.assertEqual(
            service_wait_decision(ready=False, elapsed_s=0.99, timeout_s=1.0),
            ServiceWaitDecision.WAIT,
        )

    def test_unavailable_service_times_out_at_deadline(self):
        self.assertEqual(
            service_wait_decision(ready=False, elapsed_s=1.0, timeout_s=1.0),
            ServiceWaitDecision.TIMEOUT,
        )

    def test_invalid_time_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "elapsed_s"):
            service_wait_decision(ready=False, elapsed_s=-0.1, timeout_s=1.0)
        with self.assertRaisesRegex(ValueError, "positive"):
            service_wait_decision(ready=False, elapsed_s=0.0, timeout_s=0.0)


class MechanismFailureTests(unittest.TestCase):
    def test_successful_response_has_no_failure_message(self):
        self.assertIsNone(format_mechanism_failure(
            operation=MechanismOperation.GRAB, success=True, error_code=0, detail="done"
        ))

    def test_failure_preserves_operation_code_and_detail(self):
        self.assertEqual(
            format_mechanism_failure(
                operation=MechanismOperation.GRAB,
                success=False,
                error_code=12,
                detail="gripper limit reached",
            ),
            "MECHANISM_ERROR: grab failed (code=12): gripper limit reached",
        )

    def test_blank_detail_does_not_leave_trailing_separator(self):
        self.assertEqual(
            format_mechanism_failure(
                operation=MechanismOperation.LIFT, success=False, error_code=7, detail="  "
            ),
            "MECHANISM_ERROR: lift failed (code=7)",
        )

    def test_unknown_phase_uses_generic_operation_name(self):
        self.assertEqual(
            format_mechanism_failure(
                operation=MechanismOperation.NONE, success=False, error_code=1, detail="unknown"
            ),
            "MECHANISM_ERROR: mechanism failed (code=1): unknown",
        )

    def test_service_exception_preserves_operation_and_reason(self):
        self.assertEqual(
            format_service_exception(MechanismOperation.RELEASE, RuntimeError("server stopped")),
            "MECHANISM_ERROR: release service exception: server stopped",
        )

    def test_empty_exception_uses_exception_class_name(self):
        self.assertEqual(
            format_service_exception(MechanismOperation.GRAB, RuntimeError()),
            "MECHANISM_ERROR: grab service exception: RuntimeError",
        )

    def test_unknown_exception_phase_uses_generic_operation(self):
        self.assertEqual(
            format_service_exception(MechanismOperation.NONE, ValueError("bad response")),
            "MECHANISM_ERROR: mechanism service exception: bad response",
        )


class GrabVerificationTests(unittest.TestCase):
    def test_service_only_passes_without_sensor_evidence(self):
        self.assertEqual(
            grab_verification_decision(
                policy=GrabVerificationPolicy.SERVICE_ONLY,
                cube_present=False,
                gripper_closed=False,
                evidence_updated_after_action=False,
                elapsed_s=0.0,
                timeout_s=1.0,
            ),
            VerificationDecision.PASS,
        )

    def test_cube_present_policy_waits_then_passes(self):
        waiting = grab_verification_decision(
            policy=GrabVerificationPolicy.CUBE_PRESENT,
            cube_present=False,
            gripper_closed=True,
            evidence_updated_after_action=True,
            elapsed_s=0.5,
            timeout_s=1.0,
        )
        passed = grab_verification_decision(
            policy=GrabVerificationPolicy.CUBE_PRESENT,
            cube_present=True,
            gripper_closed=False,
            evidence_updated_after_action=True,
            elapsed_s=0.6,
            timeout_s=1.0,
        )

        self.assertEqual(waiting, VerificationDecision.WAIT)
        self.assertEqual(passed, VerificationDecision.PASS)

    def test_combined_policy_requires_both_signals(self):
        self.assertEqual(
            grab_verification_decision(
                policy=GrabVerificationPolicy.GRIPPER_AND_CUBE,
                cube_present=True,
                gripper_closed=False,
                evidence_updated_after_action=True,
                elapsed_s=0.5,
                timeout_s=1.0,
            ),
            VerificationDecision.WAIT,
        )
        self.assertEqual(
            grab_verification_decision(
                policy=GrabVerificationPolicy.GRIPPER_AND_CUBE,
                cube_present=True,
                gripper_closed=True,
                evidence_updated_after_action=True,
                elapsed_s=0.5,
                timeout_s=1.0,
            ),
            VerificationDecision.PASS,
        )

    def test_missing_evidence_times_out_at_deadline(self):
        self.assertEqual(
            grab_verification_decision(
                policy=GrabVerificationPolicy.CUBE_PRESENT,
                cube_present=False,
                gripper_closed=False,
                evidence_updated_after_action=True,
                elapsed_s=1.0,
                timeout_s=1.0,
            ),
            VerificationDecision.TIMEOUT,
        )

    def test_invalid_verification_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "elapsed_s"):
            grab_verification_decision(
                policy=GrabVerificationPolicy.SERVICE_ONLY,
                cube_present=False,
                gripper_closed=False,
                evidence_updated_after_action=False,
                elapsed_s=-0.1,
                timeout_s=1.0,
            )
        with self.assertRaisesRegex(ValueError, "positive"):
            grab_verification_decision(
                policy=GrabVerificationPolicy.SERVICE_ONLY,
                cube_present=False,
                gripper_closed=False,
                evidence_updated_after_action=False,
                elapsed_s=0.0,
                timeout_s=0.0,
            )

    def test_old_positive_evidence_is_not_accepted(self):
        self.assertEqual(
            grab_verification_decision(
                policy=GrabVerificationPolicy.CUBE_PRESENT,
                cube_present=True,
                gripper_closed=True,
                evidence_updated_after_action=False,
                elapsed_s=0.5,
                timeout_s=1.0,
            ),
            VerificationDecision.WAIT,
        )

    def test_old_positive_evidence_eventually_times_out(self):
        self.assertEqual(
            grab_verification_decision(
                policy=GrabVerificationPolicy.CUBE_PRESENT,
                cube_present=True,
                gripper_closed=True,
                evidence_updated_after_action=False,
                elapsed_s=1.0,
                timeout_s=1.0,
            ),
            VerificationDecision.TIMEOUT,
        )


class CancellationTests(unittest.TestCase):
    def test_idle_cancel_reports_no_active_action(self):
        self.assertEqual(
            cancellation_decision(
                ManipulatorState.IDLE, MechanismOperation.NONE
            ),
            CancellationDecision.NO_ACTIVE_ACTION,
        )

    def test_waiting_or_aligning_action_can_stop_locally(self):
        self.assertEqual(
            cancellation_decision(
                ManipulatorState.ALIGNING, MechanismOperation.NONE
            ),
            CancellationDecision.STOP_WORKFLOW,
        )
        self.assertEqual(
            cancellation_decision(
                ManipulatorState.WAITING_SERVICE, MechanismOperation.GRAB
            ),
            CancellationDecision.STOP_WORKFLOW,
        )

    def test_executing_operation_requires_hardware_cancel_warning(self):
        self.assertEqual(
            cancellation_decision(
                ManipulatorState.EXECUTING, MechanismOperation.LIFT
            ),
            CancellationDecision.STOP_WORKFLOW_ACTIVE_OPERATION,
        )

    def test_verification_can_stop_without_active_service_warning(self):
        self.assertEqual(
            cancellation_decision(
                ManipulatorState.VERIFYING, MechanismOperation.GRAB
            ),
            CancellationDecision.STOP_WORKFLOW,
        )


class PlaceVerificationTests(unittest.TestCase):
    def test_service_only_passes_without_new_sensor_evidence(self):
        self.assertEqual(
            place_verification_decision(
                policy=PlaceVerificationPolicy.SERVICE_ONLY,
                cube_present=True,
                gripper_closed=True,
                evidence_updated_after_action=False,
                elapsed_s=0.0,
                timeout_s=1.0,
            ),
            VerificationDecision.PASS,
        )

    def test_cube_absent_requires_new_absent_status(self):
        self.assertEqual(
            place_verification_decision(
                policy=PlaceVerificationPolicy.CUBE_ABSENT,
                cube_present=False,
                gripper_closed=True,
                evidence_updated_after_action=False,
                elapsed_s=0.5,
                timeout_s=1.0,
            ),
            VerificationDecision.WAIT,
        )
        self.assertEqual(
            place_verification_decision(
                policy=PlaceVerificationPolicy.CUBE_ABSENT,
                cube_present=False,
                gripper_closed=True,
                evidence_updated_after_action=True,
                elapsed_s=0.6,
                timeout_s=1.0,
            ),
            VerificationDecision.PASS,
        )

    def test_combined_policy_requires_open_gripper_and_absent_cube(self):
        self.assertEqual(
            place_verification_decision(
                policy=PlaceVerificationPolicy.GRIPPER_OPEN_AND_CUBE_ABSENT,
                cube_present=False,
                gripper_closed=True,
                evidence_updated_after_action=True,
                elapsed_s=0.5,
                timeout_s=1.0,
            ),
            VerificationDecision.WAIT,
        )
        self.assertEqual(
            place_verification_decision(
                policy=PlaceVerificationPolicy.GRIPPER_OPEN_AND_CUBE_ABSENT,
                cube_present=False,
                gripper_closed=False,
                evidence_updated_after_action=True,
                elapsed_s=0.5,
                timeout_s=1.0,
            ),
            VerificationDecision.PASS,
        )

    def test_present_cube_times_out_without_increment_evidence(self):
        self.assertEqual(
            place_verification_decision(
                policy=PlaceVerificationPolicy.CUBE_ABSENT,
                cube_present=True,
                gripper_closed=False,
                evidence_updated_after_action=True,
                elapsed_s=1.0,
                timeout_s=1.0,
            ),
            VerificationDecision.TIMEOUT,
        )

    def test_invalid_place_verification_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "elapsed_s"):
            place_verification_decision(
                policy=PlaceVerificationPolicy.SERVICE_ONLY,
                cube_present=False,
                gripper_closed=False,
                evidence_updated_after_action=False,
                elapsed_s=-0.1,
                timeout_s=1.0,
            )
        with self.assertRaisesRegex(ValueError, "positive"):
            place_verification_decision(
                policy=PlaceVerificationPolicy.SERVICE_ONLY,
                cube_present=False,
                gripper_closed=False,
                evidence_updated_after_action=False,
                elapsed_s=0.0,
                timeout_s=0.0,
            )


if __name__ == "__main__":
    unittest.main()
