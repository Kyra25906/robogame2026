from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ConfigIssue:
    level: str
    path: str
    message: str


def _params(config: Mapping[str, Any], node: str) -> Mapping[str, Any]:
    section = config.get(node, {})
    return section.get("ros__parameters", {}) if isinstance(section, Mapping) else {}


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_config_bundle(
    *,
    common: Mapping[str, Any],
    mock: Mapping[str, Any],
    field: Mapping[str, Any],
    single_cube: Mapping[str, Any],
    legacy_hardware: Mapping[str, Any] | None = None,
) -> list[ConfigIssue]:
    """Validate relationships across the common, mock and field YAML layers."""
    issues: list[ConfigIssue] = []

    def error(path: str, message: str) -> None:
        issues.append(ConfigIssue("ERROR", path, message))

    def warning(path: str, message: str) -> None:
        issues.append(ConfigIssue("WARNING", path, message))

    required_nodes = {
        "robot_bridge", "motion_controller", "cube_perception",
        "manipulator_client", "mission_manager", "localization",
    }
    for node in sorted(required_nodes):
        if not _params(common, node):
            error(f"robot.yaml:{node}", "missing ros__parameters")

    motion = _params(common, "motion_controller")
    manipulator = _params(common, "manipulator_client")
    perception = _params(common, "cube_perception")
    mission = _params(common, "mission_manager")
    bridge = _params(common, "robot_bridge")
    localization = _params(common, "localization")

    positive_groups = {
        "robot.yaml:robot_bridge": (bridge, [
            "command_timeout_s", "odom_pose_xy_variance",
            "odom_pose_yaw_variance", "odom_twist_linear_variance",
            "odom_twist_yaw_variance", "imu_yaw_rate_variance",
            "unavailable_variance", "max_mcu_sample_gap_ms",
        ]),
        "robot.yaml:motion_controller": (motion, [
            "kx", "ky", "kyaw", "max_vx", "max_vy", "max_wz",
            "position_tolerance", "yaw_tolerance", "slow_radius",
            "max_ax", "max_ay", "max_awz", "max_control_dt_s",
            "goal_timeout_s", "pose_stale_s", "status_stale_s",
        ]),
        "robot.yaml:manipulator_client": (manipulator, [
            "target_distance_m", "distance_tolerance_m", "lateral_tolerance_m",
            "kp_distance", "kp_lateral", "max_speed", "target_stale_s",
            "action_timeout_s", "status_stale_s",
            "placement_stable_duration_s", "placement_observation_timeout_s",
        ]),
        "robot.yaml:cube_perception": (perception, [
            "min_area_px", "cube_size_m", "fallback_focal_px", "morph_kernel",
            "confirm_frames", "match_distance_px", "min_side_px",
        ]),
        "robot.yaml:mission_manager": (mission, [
            "state_timeout_s", "build_stability_s",
        ]),
        "robot.yaml:localization": (localization, [
            "imu_stale_s", "max_speed_mps", "divergence_threshold",
        ]),
    }
    for prefix, (params, names) in positive_groups.items():
        for name in names:
            value = params.get(name)
            if not _finite_number(value) or value <= 0:
                error(f"{prefix}.{name}", "must be a positive finite number")

    sample_gap = bridge.get("max_mcu_sample_gap_ms")
    if not isinstance(sample_gap, int) or isinstance(sample_gap, bool) or sample_gap <= 0:
        error(
            "robot.yaml:robot_bridge.max_mcu_sample_gap_ms",
            "must be a positive integer number of milliseconds",
        )

    gap = manipulator.get("placement_max_unavailable_gap_s")
    if not _finite_number(gap) or gap < 0:
        error(
            "robot.yaml:manipulator_client.placement_max_unavailable_gap_s",
            "must be a non-negative finite number",
        )

    stable = manipulator.get("placement_stable_duration_s")
    observation = manipulator.get("placement_observation_timeout_s")
    action_timeout = manipulator.get("action_timeout_s")
    if all(_finite_number(value) for value in (stable, observation)) and stable > observation:
        error(
            "robot.yaml:manipulator_client",
            "placement_stable_duration_s cannot exceed placement_observation_timeout_s",
        )
    if all(_finite_number(value) for value in (observation, action_timeout)) and observation > action_timeout:
        error(
            "robot.yaml:manipulator_client",
            "placement_observation_timeout_s cannot exceed action_timeout_s",
        )
    if all(_finite_number(value) for value in (gap, observation)) and gap > observation:
        error(
            "robot.yaml:manipulator_client",
            "placement_max_unavailable_gap_s cannot exceed observation timeout",
        )

    bounds = [motion.get(name) for name in ("min_x", "max_x", "min_y", "max_y")]
    if not all(_finite_number(value) for value in bounds):
        error("robot.yaml:motion_controller.field_bounds", "all bounds must be finite")
    elif not (bounds[0] < bounds[1] and bounds[2] < bounds[3]):
        error("robot.yaml:motion_controller.field_bounds", "min bounds must be below max bounds")
    else:
        for name in ("orange_waypoint", "purple_waypoint", "build_waypoint", "retreat_waypoint"):
            waypoint = mission.get(name)
            if not isinstance(waypoint, list) or len(waypoint) != 3 or not all(
                _finite_number(value) for value in waypoint
            ):
                error(f"robot.yaml:mission_manager.{name}", "must contain three finite numbers")
            elif not (bounds[0] <= waypoint[0] <= bounds[1] and bounds[2] <= waypoint[1] <= bounds[3]):
                error(f"robot.yaml:mission_manager.{name}", "lies outside configured field bounds")

    for name in ("min_confidence", "smoothing_alpha", "min_rectangularity", "min_solidity"):
        value = perception.get(name)
        if not _finite_number(value) or not 0.0 <= value <= 1.0:
            error(f"robot.yaml:cube_perception.{name}", "must be within [0, 1]")
    for axis in ("x", "y"):
        low = perception.get(f"roi_{axis}_min_ratio")
        high = perception.get(f"roi_{axis}_max_ratio")
        if not all(_finite_number(value) for value in (low, high)) or not 0 <= low < high <= 1:
            error(f"robot.yaml:cube_perception.roi_{axis}", "must satisfy 0 <= min < max <= 1")
    for name in ("orange_hsv", "purple_hsv"):
        hsv = perception.get(name)
        if (
            not isinstance(hsv, list)
            or len(hsv) != 6
            or not all(_finite_number(value) for value in hsv)
        ):
            error(f"robot.yaml:cube_perception.{name}", "must contain six values")
        elif not (
            0 <= hsv[0] <= hsv[3] <= 179
            and 0 <= hsv[1] <= hsv[4] <= 255
            and 0 <= hsv[2] <= hsv[5] <= 255
        ):
            error(f"robot.yaml:cube_perception.{name}", "contains invalid OpenCV HSV bounds")

    heights = manipulator.get("place_heights_m")
    if not isinstance(heights, list) or not heights or not all(
        _finite_number(value) and value > 0 for value in heights
    ):
        error("robot.yaml:manipulator_client.place_heights_m", "must contain positive heights")
    elif heights != sorted(set(heights)):
        error("robot.yaml:manipulator_client.place_heights_m", "must be unique and ascending")

    forbidden_common = {
        "robot_bridge": {"mock_mode", "serial_port", "baud_rate"},
        "manipulator_client": {"runtime_mode", "placement_evidence_policy"},
    }
    for node, names in forbidden_common.items():
        present = names.intersection(_params(common, node))
        if present:
            error(f"robot.yaml:{node}", f"environment fields belong in mock/field layer: {sorted(present)}")

    mock_bridge = _params(mock, "robot_bridge")
    mock_manipulator = _params(mock, "manipulator_client")
    field_bridge = _params(field, "robot_bridge")
    field_manipulator = _params(field, "manipulator_client")
    if mock_bridge.get("mock_mode") is not True:
        error("robot_mock.yaml:robot_bridge.mock_mode", "must be true")
    if mock_manipulator.get("runtime_mode") != "mock":
        error("robot_mock.yaml:manipulator_client.runtime_mode", "must be mock")
    if not str(mock_manipulator.get("placement_evidence_policy", "")).startswith("mock_"):
        error("robot_mock.yaml:manipulator_client.placement_evidence_policy", "must use mock evidence")
    if field_bridge.get("mock_mode") is not False:
        error("robot_field.yaml:robot_bridge.mock_mode", "must be false")
    if field_manipulator.get("runtime_mode") != "field":
        error("robot_field.yaml:manipulator_client.runtime_mode", "must be field")
    if str(field_manipulator.get("placement_evidence_policy", "")).startswith("mock_"):
        error("robot_field.yaml:manipulator_client.placement_evidence_policy", "cannot use mock evidence")
    if not isinstance(field_bridge.get("serial_port"), str) or not field_bridge.get("serial_port"):
        error("robot_field.yaml:robot_bridge.serial_port", "must be a non-empty path")
    if (
        not isinstance(field_bridge.get("baud_rate"), int)
        or isinstance(field_bridge.get("baud_rate"), bool)
        or field_bridge.get("baud_rate", 0) <= 0
    ):
        error("robot_field.yaml:robot_bridge.baud_rate", "must be a positive integer")

    if motion.get("status_stale_s") != manipulator.get("status_stale_s"):
        warning("robot.yaml:status_stale_s", "motion and manipulator use different status deadlines")
    if mission.get("build_stability_s") != manipulator.get("placement_stable_duration_s"):
        warning("robot.yaml:stability", "mission and placement stability durations differ")

    single = _params(single_cube, "mission_manager")
    for name in ("orange_target", "purple_target"):
        value = single.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            error(f"single_cube.yaml:mission_manager.{name}", "must be a non-negative integer")
    if all(isinstance(single.get(name), int) for name in ("orange_target", "purple_target")):
        if single["orange_target"] + single["purple_target"] != 1:
            error("single_cube.yaml:mission_manager", "single-cube targets must total exactly one")

    if legacy_hardware is not None:
        expected_bridge = dict(bridge)
        expected_bridge.update(field_bridge)
        actual_bridge = dict(_params(legacy_hardware, "robot_bridge"))
        if actual_bridge != expected_bridge:
            warning(
                "hardware.yaml:robot_bridge",
                "legacy hardware config differs from robot.yaml + robot_field.yaml",
            )

    return issues
