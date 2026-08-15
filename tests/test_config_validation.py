import copy
import json
from pathlib import Path
import re
import unittest

from robogame_core.config_validation import validate_config_bundle


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _node(**params):
    return {"ros__parameters": params}


def valid_bundle():
    common = {
        "robot_bridge": _node(
            command_timeout_s=0.15,
            odom_pose_xy_variance=0.25,
            odom_pose_yaw_variance=0.1219,
            odom_twist_linear_variance=0.04,
            odom_twist_yaw_variance=0.09,
            imu_yaw_rate_variance=0.09,
            unavailable_variance=1000000.0,
            max_mcu_sample_gap_ms=250,
        ),
        "motion_controller": _node(
            kx=1.2, ky=1.2, kyaw=1.8,
            max_vx=0.6, max_vy=0.5, max_wz=1.2,
            position_tolerance=0.05, yaw_tolerance=0.08, slow_radius=0.35,
            max_ax=0.8, max_ay=0.8, max_awz=2.0,
            max_control_dt_s=0.1, goal_timeout_s=15.0,
            pose_stale_s=0.25, status_stale_s=0.3,
            min_x=-0.2, max_x=7.4, min_y=-0.2, max_y=5.0,
        ),
        "cube_perception": _node(
            orange_hsv=[5, 100, 80, 25, 255, 255],
            purple_hsv=[125, 60, 50, 165, 255, 255],
            min_area_px=400.0, cube_size_m=0.1, fallback_focal_px=700.0,
            morph_kernel=5, min_confidence=0.45, confirm_frames=3,
            match_distance_px=80.0, smoothing_alpha=0.4,
            roi_x_min_ratio=0.0, roi_x_max_ratio=1.0,
            roi_y_min_ratio=0.0, roi_y_max_ratio=1.0,
            min_rectangularity=0.35, min_solidity=0.5, min_side_px=12.0,
        ),
        "manipulator_client": _node(
            target_distance_m=0.24, distance_tolerance_m=0.025,
            lateral_tolerance_m=0.018, kp_distance=0.8, kp_lateral=1.2,
            max_speed=0.18, target_stale_s=0.5, action_timeout_s=12.0,
            status_stale_s=0.3, placement_stable_duration_s=3.0,
            placement_observation_timeout_s=6.0,
            placement_max_unavailable_gap_s=0.0,
            place_heights_m=[0.1, 0.2, 0.3],
        ),
        "mission_manager": _node(
            state_timeout_s=20.0, build_stability_s=3.0,
            orange_waypoint=[1.0, 0.5, 0.0],
            purple_waypoint=[1.5, 1.0, 0.0],
            build_waypoint=[0.5, 1.5, 1.57],
            retreat_waypoint=[0.5, 1.2, 1.57],
        ),
        "localization": _node(
            imu_stale_s=0.2, max_speed_mps=3.0,
            divergence_threshold=0.5,
        ),
    }
    mock = {
        "robot_bridge": _node(mock_mode=True),
        "manipulator_client": _node(
            runtime_mode="mock", placement_evidence_policy="mock_qualified"
        ),
    }
    field = {
        "robot_bridge": _node(
            mock_mode=False, serial_port="/dev/ttyACM0", baud_rate=115200
        ),
        "manipulator_client": _node(
            runtime_mode="field", placement_evidence_policy="unavailable"
        ),
    }
    single_cube = {
        "mission_manager": _node(orange_target=1, purple_target=0)
    }
    hardware_bridge = dict(common["robot_bridge"]["ros__parameters"])
    hardware_bridge.update(field["robot_bridge"]["ros__parameters"])
    hardware = {"robot_bridge": _node(**hardware_bridge)}
    return common, mock, field, single_cube, hardware


def validate(bundle):
    common, mock, field, single_cube, hardware = bundle
    return validate_config_bundle(
        common=common,
        mock=mock,
        field=field,
        single_cube=single_cube,
        legacy_hardware=hardware,
    )


class ConfigValidationTests(unittest.TestCase):
    def test_camera_specific_focal_calibration_is_separated_from_defaults(self):
        vision_default = json.loads(
            (
                PROJECT_ROOT
                / "ros2_ws/src/cube_perception/config/vision_default.json"
            ).read_text(encoding="utf-8")
        )
        vision_demo = json.loads(
            (
                PROJECT_ROOT
                / "ros2_ws/src/cube_perception/config/vision_demo_roi.json"
            ).read_text(encoding="utf-8")
        )
        vision_gf100 = json.loads(
            (
                PROJECT_ROOT
                / "ros2_ws/src/cube_perception/config/vision_gf100_1280x720_bench.json"
            ).read_text(encoding="utf-8")
        )
        robot_text = (
            PROJECT_ROOT
            / "ros2_ws/src/robogame_bringup/config/robot.yaml"
        ).read_text(encoding="utf-8")
        field_text = (
            PROJECT_ROOT
            / "ros2_ws/src/robogame_bringup/config/robot_field.yaml"
        ).read_text(encoding="utf-8")
        common_focal_matches = re.findall(
            r"^\s+fallback_focal_px:\s*([0-9]+(?:\.[0-9]+)?)\s*$",
            robot_text,
            flags=re.MULTILINE,
        )
        field_focal_matches = re.findall(
            r"^\s+fallback_focal_px:\s*([0-9]+(?:\.[0-9]+)?)\s*$",
            field_text,
            flags=re.MULTILINE,
        )

        self.assertEqual(vision_default["focal_px"], 700.0)
        self.assertEqual(vision_demo["focal_px"], vision_default["focal_px"])
        self.assertEqual(common_focal_matches, [str(vision_default["focal_px"])])
        self.assertEqual(vision_gf100["focal_px"], 2550.0)
        self.assertEqual(field_focal_matches, [str(vision_gf100["focal_px"])])

    def test_valid_bundle_has_no_issues(self):
        self.assertEqual(validate(valid_bundle()), [])

    def test_impossible_stability_time_is_rejected(self):
        bundle = valid_bundle()
        common = bundle[0]
        common["manipulator_client"]["ros__parameters"][
            "placement_stable_duration_s"
        ] = 7.0
        issues = validate(bundle)
        self.assertTrue(any(
            issue.level == "ERROR" and "stable_duration" in issue.message
            for issue in issues
        ))

    def test_field_layer_cannot_enable_mock_evidence(self):
        bundle = valid_bundle()
        bundle[2]["manipulator_client"]["ros__parameters"][
            "placement_evidence_policy"
        ] = "mock_qualified"
        issues = validate(bundle)
        self.assertTrue(any(
            issue.level == "ERROR" and "cannot use mock evidence" in issue.message
            for issue in issues
        ))

    def test_waypoint_outside_field_is_rejected(self):
        bundle = valid_bundle()
        bundle[0]["mission_manager"]["ros__parameters"]["build_waypoint"] = [8.0, 1.0, 0.0]
        self.assertTrue(any(
            "outside configured field" in issue.message for issue in validate(bundle)
        ))

    def test_environment_fields_do_not_belong_in_common_layer(self):
        bundle = valid_bundle()
        bundle[0]["robot_bridge"]["ros__parameters"]["mock_mode"] = False
        self.assertTrue(any(
            "environment fields" in issue.message for issue in validate(bundle)
        ))

    def test_legacy_hardware_drift_is_reported_as_warning(self):
        bundle = valid_bundle()
        bundle[4]["robot_bridge"]["ros__parameters"]["baud_rate"] = 9600
        issues = validate(bundle)
        self.assertTrue(any(
            issue.level == "WARNING" and "legacy hardware" in issue.message
            for issue in issues
        ))

    def test_malformed_hsv_is_reported_without_crashing(self):
        bundle = valid_bundle()
        bundle[0]["cube_perception"]["ros__parameters"]["orange_hsv"] = ["bad"] * 6
        issues = validate(bundle)
        self.assertTrue(any("orange_hsv" in issue.path for issue in issues))

    def test_invalid_localization_safety_parameter_is_rejected(self):
        bundle = valid_bundle()
        bundle[0]["localization"]["ros__parameters"]["imu_stale_s"] = 0.0
        issues = validate(bundle)
        self.assertTrue(any(
            issue.level == "ERROR" and "imu_stale_s" in issue.path
            for issue in issues
        ))

    def test_zero_covariance_is_rejected(self):
        bundle = valid_bundle()
        bundle[0]["robot_bridge"]["ros__parameters"][
            "imu_yaw_rate_variance"
        ] = 0.0
        issues = validate(bundle)
        self.assertTrue(any(
            issue.level == "ERROR" and "imu_yaw_rate_variance" in issue.path
            for issue in issues
        ))

    def test_fractional_mcu_sample_gap_is_rejected(self):
        bundle = valid_bundle()
        bundle[0]["robot_bridge"]["ros__parameters"][
            "max_mcu_sample_gap_ms"
        ] = 250.5
        issues = validate(bundle)
        self.assertTrue(any(
            issue.level == "ERROR" and "max_mcu_sample_gap_ms" in issue.path
            for issue in issues
        ))

    def test_input_can_be_copied_without_hidden_mutation(self):
        bundle = valid_bundle()
        before = copy.deepcopy(bundle)
        validate(bundle)
        self.assertEqual(bundle, before)


if __name__ == "__main__":
    unittest.main()
