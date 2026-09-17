import ast
import unittest
from pathlib import Path

from tools.grasp_alignment_sim import (
    GRASP_DEFAULTS,
    LAW_DEFAULTS,
    SIM_DEFAULTS,
    evaluate_detection,
    simulate_grasp,
)

NODE_PATH = (
    Path(__file__).resolve().parents[1]
    / "ros2_ws"
    / "src"
    / "manipulator_client"
    / "manipulator_client"
    / "node.py"
)


def node_defaults() -> dict:
    """从节点源码里取出 declare_parameter 的默认值，防止两处漂移。"""
    source = ast.parse(NODE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(source):
        if not isinstance(node, ast.For) or not isinstance(node.iter, ast.Call):
            continue
        function = node.iter.func
        if not (isinstance(function, ast.Attribute) and function.attr == "items"):
            continue
        literal = node.iter.func.value
        if isinstance(literal, ast.Dict):
            return {ast.literal_eval(key): ast.literal_eval(value)
                    for key, value in zip(literal.keys, literal.values)}
    raise AssertionError("node parameter block not found")


class SimulatorDefaultsTests(unittest.TestCase):
    def test_law_defaults_match_manipulator_client_node(self):
        defaults = node_defaults()

        for key, value in LAW_DEFAULTS.items():
            with self.subTest(key=key):
                self.assertIn(key, defaults)
                self.assertEqual(defaults[key], value)

    def test_sim_defaults_target_sits_ahead_of_start(self):
        self.assertGreater(SIM_DEFAULTS["target_x_m"], 0.0)
        self.assertGreater(GRASP_DEFAULTS["target_distance_m"], 0.0)

    def test_unknown_parameter_is_rejected_not_ignored(self):
        with self.assertRaises(ValueError):
            simulate_grasp(approach_yaw_offset_rad=1.5708)
        with self.assertRaises(ValueError):
            simulate_grasp(typo_m=1.0)


class SimulatorBehaviourTests(unittest.TestCase):
    def test_offset_start_converges_to_ready(self):
        result = simulate_grasp()

        self.assertTrue(result["converged"])
        self.assertEqual(result["stop_reason"], "ready")
        self.assertLessEqual(abs(result["final"]["cross_m"]), LAW_DEFAULTS["cross_tolerance_m"])
        self.assertLessEqual(
            abs(result["final"]["along_m"] - LAW_DEFAULTS["target_distance_m"]),
            LAW_DEFAULTS["distance_tolerance_m"],
        )

    def test_turning_happens_before_approaching(self):
        result = simulate_grasp()
        phases = [step["phase"] for step in result["trace"]]
        first_approach = phases.index("APPROACH") if "APPROACH" in phases else len(phases)

        self.assertIn("TURN", phases[:first_approach] or ["TURN"])
        self.assertNotIn("APPROACH", phases[:first_approach])

    def test_never_drives_while_outside_cross_tolerance(self):
        result = simulate_grasp()

        self.assertEqual(result["safety_violations"], 0)
        for step in result["trace"]:
            if abs(step["cross_m"]) > LAW_DEFAULTS["cross_tolerance_m"]:
                self.assertEqual(step["linear_x"], 0.0)

    def test_lateral_velocity_is_never_commanded(self):
        result = simulate_grasp(target_y_m=0.25)

        self.assertTrue(result["lateral_velocity_always_zero"])
        self.assertTrue(all(step["linear_y"] == 0.0 for step in result["trace"]))

    def test_converges_from_every_start_in_a_reachable_grid(self):
        failures = []
        for start_x in (-0.4, -0.2, 0.0, 0.2, 0.4):
            for start_y in (-0.3, -0.1, 0.0, 0.1, 0.3):
                for start_yaw_rad in (-0.6, 0.0, 0.6):
                    result = simulate_grasp(
                        start_x_m=start_x, start_y_m=start_y, start_yaw_rad=start_yaw_rad,
                        target_x_m=0.24, target_y_m=-0.10,
                    )
                    if not result["converged"]:
                        failures.append((start_x, start_y, start_yaw_rad,
                                         result["stop_reason"], result["steps"]))
        self.assertEqual(failures, [])

    def test_converges_with_camera_mounted_off_axis(self):
        result = simulate_grasp(camera_yaw_offset_rad=-0.35, target_y_m=-0.12)

        self.assertTrue(result["converged"])
        self.assertEqual(result["safety_violations"], 0)

    def test_target_behind_start_still_converges(self):
        result = simulate_grasp(start_x_m=0.9, start_y_m=-0.05, target_x_m=0.24, target_y_m=-0.10)

        self.assertTrue(result["converged"])
        self.assertEqual(result["safety_violations"], 0)

    def test_same_input_gives_identical_trace(self):
        self.assertEqual(simulate_grasp()["trace"], simulate_grasp()["trace"])

    def test_step_limit_is_respected(self):
        result = simulate_grasp(max_steps=3)

        self.assertEqual(result["steps"], 3)
        self.assertEqual(result["stop_reason"], "max_steps")

    def test_invalid_configuration_is_rejected(self):
        for overrides in ({"dt_s": 0.0}, {"dt_s": -0.1}, {"max_steps": 0},
                          {"target_distance_m": 0.0}, {"max_turn_rate": 0.0}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    simulate_grasp(**overrides)


class PanelEvaluationTests(unittest.TestCase):
    def test_panel_reports_turn_phase_for_offset_target(self):
        state = evaluate_detection(forward_m=0.30, lateral_right_m=0.10)

        self.assertTrue(state["valid"])
        self.assertEqual(state["phase"], "TURN")
        self.assertEqual(state["linear_x"], 0.0)
        self.assertLess(state["angular_z"], 0.0)  # 目标在右 → 右转
        self.assertEqual(state["linear_y"], 0.0)

    def test_panel_reports_ready_for_on_axis_target(self):
        state = evaluate_detection(forward_m=0.25, lateral_right_m=0.005)

        self.assertTrue(state["ready_to_grab"])
        self.assertEqual(state["phase"], "READY")

    def test_panel_rejects_non_positive_range(self):
        state = evaluate_detection(forward_m=0.0, lateral_right_m=0.0)

        self.assertFalse(state["valid"])
        self.assertEqual(state["reject_reason"], "non_positive_range")


if __name__ == "__main__":
    unittest.main()
