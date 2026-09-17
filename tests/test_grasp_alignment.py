import ast
import inspect
import math
import unittest
from dataclasses import fields
from pathlib import Path

from robogame_core.grasp_alignment import (
    AlignmentPhase,
    TurnAlignmentCommand,
    calculate_turn_alignment_command,
    target_in_body_frame,
)

NODE_PATH = (
    Path(__file__).resolve().parents[1]
    / "ros2_ws"
    / "src"
    / "manipulator_client"
    / "manipulator_client"
    / "node.py"
)


def frame(forward_m=0.24, lateral_right_m=0.0, **overrides):
    parameters = {"forward_m": forward_m, "lateral_right_m": lateral_right_m,
                  "camera_yaw_offset_rad": 0.0}
    parameters.update(overrides)
    return target_in_body_frame(**parameters)


def alignment(forward_m=0.24, left_m=0.0, **overrides):
    parameters = {
        "forward_m": forward_m,
        "left_m": left_m,
        "target_distance_m": 0.24,
        "distance_tolerance_m": 0.025,
        "cross_tolerance_m": 0.018,
        "kp_distance": 0.8,
        "kp_bearing": 1.2,
        "max_speed": 0.18,
        "max_turn_rate": 0.6,
        "min_turn_rate": 0.0,
    }
    parameters.update(overrides)
    return calculate_turn_alignment_command(**parameters)


class BodyFrameGeometryTests(unittest.TestCase):
    def test_camera_forward_detection_maps_straight_to_body_frame(self):
        result = frame(forward_m=0.30, lateral_right_m=0.0)

        self.assertTrue(result.valid)
        self.assertAlmostEqual(result.forward_m, 0.30)
        self.assertAlmostEqual(result.left_m, 0.0)
        self.assertAlmostEqual(result.bearing_rad, 0.0)

    def test_target_right_of_image_is_right_in_body_frame(self):
        result = frame(forward_m=0.24, lateral_right_m=0.05)

        self.assertAlmostEqual(result.forward_m, 0.24)
        self.assertAlmostEqual(result.left_m, -0.05)
        self.assertAlmostEqual(result.range_m, math.hypot(0.24, 0.05))

    def test_target_left_of_image_is_left_in_body_frame(self):
        self.assertAlmostEqual(frame(forward_m=0.24, lateral_right_m=-0.05).left_m, 0.05)

    def test_camera_turned_right_sees_body_right_as_image_center(self):
        # 相机朝右 90°：车右侧的目标出现在画面正中
        result = frame(forward_m=0.30, lateral_right_m=0.0, camera_yaw_offset_rad=-math.pi / 2.0)

        self.assertAlmostEqual(result.forward_m, 0.0, places=9)
        self.assertAlmostEqual(result.left_m, -0.30)

    def test_camera_offset_round_trips_a_known_body_point(self):
        # 车体系 (0.3, -0.2) 经相机偏角 -90° 成像后，必须换回同一点
        camera_yaw = -math.pi / 2.0
        forward_m, left_m = 0.3, -0.2
        forward_cam = forward_m * math.cos(camera_yaw) + left_m * math.sin(camera_yaw)
        lateral_right = forward_m * math.sin(camera_yaw) - left_m * math.cos(camera_yaw)

        result = frame(forward_m=forward_cam, lateral_right_m=lateral_right,
                       camera_yaw_offset_rad=camera_yaw)

        self.assertAlmostEqual(result.forward_m, forward_m)
        self.assertAlmostEqual(result.left_m, left_m)

    def test_non_positive_range_is_rejected_not_controlled(self):
        result = frame(forward_m=0.0, lateral_right_m=0.02)

        self.assertFalse(result.valid)
        self.assertEqual(result.reject_reason, "non_positive_range")

    def test_non_finite_geometry_raises(self):
        with self.assertRaises(ValueError):
            frame(forward_m=float("nan"))
        with self.assertRaises(ValueError):
            frame(lateral_right_m=float("-inf"))
        with self.assertRaises(ValueError):
            frame(camera_yaw_offset_rad=float("inf"))


class TurnAlignmentCommandTests(unittest.TestCase):
    def test_misaligned_target_turns_in_place_without_driving_forward(self):
        result = alignment(forward_m=0.24, left_m=0.05)

        self.assertIs(result.phase, AlignmentPhase.TURN)
        self.assertEqual(result.linear_x, 0.0)
        self.assertFalse(result.ready_to_grab)

    def test_sweep_never_drives_while_outside_cross_tolerance(self):
        for left in (-0.5, -0.1, -0.019, 0.019, 0.1, 0.5):
            with self.subTest(left=left):
                self.assertEqual(alignment(left_m=left).linear_x, 0.0)

    def test_turn_direction_follows_target_side(self):
        self.assertGreater(alignment(left_m=0.05).angular_z, 0.0)
        self.assertLess(alignment(left_m=-0.05).angular_z, 0.0)

    def test_turn_rate_is_limited(self):
        self.assertAlmostEqual(alignment(left_m=0.5, kp_bearing=5.0).angular_z, 0.6)

    def test_small_turn_uses_min_turn_rate_deadband(self):
        result = alignment(left_m=0.02, kp_bearing=0.1, min_turn_rate=0.15)

        self.assertAlmostEqual(result.angular_z, 0.15)

    def test_no_deadband_compensation_when_disabled(self):
        result = alignment(left_m=0.019, kp_bearing=0.1)

        self.assertLess(abs(result.angular_z), 0.15)

    def test_aligned_but_far_target_approaches_forward(self):
        result = alignment(forward_m=0.40, left_m=0.0)

        self.assertIs(result.phase, AlignmentPhase.APPROACH)
        self.assertGreater(result.linear_x, 0.0)
        self.assertEqual(result.angular_z, 0.0)

    def test_aligned_but_close_target_reverses(self):
        result = alignment(forward_m=0.10, left_m=0.0)

        self.assertIs(result.phase, AlignmentPhase.APPROACH)
        self.assertLess(result.linear_x, 0.0)

    def test_approach_speed_is_limited(self):
        self.assertAlmostEqual(alignment(forward_m=1.0, kp_distance=5.0).linear_x, 0.18)

    def test_inside_both_tolerances_is_ready_and_stopped(self):
        result = alignment(forward_m=0.25, left_m=0.01)

        self.assertIs(result.phase, AlignmentPhase.READY)
        self.assertTrue(result.ready_to_grab)
        self.assertEqual((result.linear_x, result.angular_z), (0.0, 0.0))

    def test_inside_tolerances_is_ready(self):
        self.assertTrue(alignment(forward_m=0.255, left_m=0.010).ready_to_grab)

    def test_just_outside_distance_tolerance_is_not_ready(self):
        # 边界附近受浮点误差影响，不声明"恰好等于容差"，只声明内外两侧的行为
        result = alignment(forward_m=0.270, left_m=0.0)

        self.assertFalse(result.ready_to_grab)
        self.assertIs(result.phase, AlignmentPhase.APPROACH)

    def test_ready_requires_both_axes(self):
        result = alignment(forward_m=0.25, left_m=0.05)

        self.assertFalse(result.ready_to_grab)
        self.assertIs(result.phase, AlignmentPhase.TURN)

    def test_target_beside_or_behind_turns_hard_without_driving(self):
        for forward in (-0.1, 0.0):
            with self.subTest(forward=forward):
                result = alignment(forward_m=forward, left_m=0.05)
                self.assertIs(result.phase, AlignmentPhase.TURN)
                self.assertEqual(result.linear_x, 0.0)
                self.assertAlmostEqual(result.angular_z, 0.6)

    def test_target_beside_on_right_turns_negative(self):
        self.assertLess(alignment(forward_m=0.0, left_m=-0.05).angular_z, 0.0)

    def test_linear_y_is_always_zero(self):
        commands = [alignment(left_m=0.05), alignment(forward_m=0.4),
                    alignment(forward_m=0.24, left_m=0.0)]

        self.assertEqual([command.linear_y for command in commands], [0.0, 0.0, 0.0])

    def test_command_struct_cannot_express_lateral_motion(self):
        self.assertNotIn("linear_y", {field.name for field in fields(TurnAlignmentCommand)})

    def test_invalid_configuration_is_rejected(self):
        for overrides in (
            {"distance_tolerance_m": -0.01},
            {"cross_tolerance_m": -0.01},
            {"target_distance_m": 0.0},
            {"max_speed": 0.0},
            {"max_turn_rate": 0.0},
            {"kp_distance": 0.0},
            {"kp_bearing": 0.0},
            {"min_turn_rate": 0.9},
            {"forward_m": float("nan")},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    alignment(**overrides)


class SideApproachIsStructurallyImpossibleTests(unittest.TestCase):
    """VY=0 + 侧向接近轴在数学上不收敛，所以模块里不允许存在该参数。"""

    def test_no_approach_axis_offset_parameter_exists(self):
        for function in (target_in_body_frame, calculate_turn_alignment_command):
            with self.subTest(function=function.__name__):
                parameters = set(inspect.signature(function).parameters)
                self.assertNotIn("approach_yaw_offset_rad", parameters)
                self.assertFalse({name for name in parameters if "approach" in name})

    def test_node_does_not_declare_approach_offset_parameter(self):
        source = NODE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("approach_yaw_offset_rad", source)


class ManipulatorClientWiringTests(unittest.TestCase):
    @staticmethod
    def _dotted(node) -> str:
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    def _msg_assignments(self) -> dict[str, list]:
        assignments: dict[str, list] = {}
        for node in ast.walk(ast.parse(NODE_PATH.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            name = self._dotted(node.targets[0])
            if name.startswith("msg."):
                assignments.setdefault(name, []).append(node.value)
        return assignments

    def test_node_publishes_turn_rate_from_alignment_command(self):
        assignments = self._msg_assignments()

        self.assertIn("msg.angular.z", assignments)
        self.assertIn("alignment.angular_z",
                      {ast.unparse(value) for value in assignments["msg.angular.z"]})

    def test_node_never_commands_lateral_velocity(self):
        assignments = self._msg_assignments()

        self.assertIn("msg.linear.y", assignments)
        for value in assignments["msg.linear.y"]:
            self.assertEqual(ast.unparse(value), "0.0")

    def test_node_uses_turn_alignment_law(self):
        source = NODE_PATH.read_text(encoding="utf-8")

        self.assertIn("calculate_turn_alignment_command", source)
        self.assertNotIn("calculate_alignment_command(", source)


if __name__ == "__main__":
    unittest.main()
