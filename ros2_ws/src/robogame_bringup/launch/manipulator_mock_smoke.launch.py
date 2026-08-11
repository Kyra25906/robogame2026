from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    evidence_policy = LaunchConfiguration("placement_evidence_policy")
    stable_duration = LaunchConfiguration("placement_stable_duration_s")
    observation_timeout = LaunchConfiguration("placement_observation_timeout_s")
    unavailable_gap = LaunchConfiguration("placement_max_unavailable_gap_s")
    expected_result = LaunchConfiguration("expected_place_result")
    cancel_during_stability = LaunchConfiguration("cancel_during_stability")
    cancel_delay = LaunchConfiguration("cancel_delay_s")
    bridge = Node(
        package="robot_bridge",
        executable="robot_bridge",
        parameters=[{
            "mock_mode": True,
            "mock_start_after_s": 0.0,
            "mock_grab_success": True,
            "mock_release_success": True,
            "mock_lift_success": True,
        }],
    )
    perception = Node(package="cube_perception", executable="mock_perception")
    manipulator = Node(
        package="manipulator_client",
        executable="manipulator_client",
        parameters=[{
            "grab_verification_policy": "gripper_and_cube",
            "grab_verification_timeout_s": 1.0,
            "place_verification_policy": "gripper_open_and_cube_absent",
            "place_verification_timeout_s": 1.0,
            "placement_evidence_policy": ParameterValue(
                evidence_policy, value_type=str
            ),
            "placement_stable_duration_s": ParameterValue(
                stable_duration, value_type=float
            ),
            "placement_observation_timeout_s": ParameterValue(
                observation_timeout, value_type=float
            ),
            "placement_max_unavailable_gap_s": ParameterValue(
                unavailable_gap, value_type=float
            ),
        }],
    )
    smoke = Node(
        package="robogame_bringup",
        executable="manipulator_mock_smoke",
        output="screen",
        parameters=[{
            "expected_place_result": ParameterValue(
                expected_result, value_type=str
            ),
            "cancel_during_stability": ParameterValue(
                cancel_during_stability, value_type=bool
            ),
            "cancel_delay_s": ParameterValue(cancel_delay, value_type=float),
        }],
    )
    shutdown_on_smoke_exit = RegisterEventHandler(OnProcessExit(
        target_action=smoke,
        on_exit=[EmitEvent(event=Shutdown(reason="manipulator smoke finished"))],
    ))
    return LaunchDescription([
        DeclareLaunchArgument(
            "placement_evidence_policy", default_value="mock_qualified"
        ),
        DeclareLaunchArgument(
            "placement_stable_duration_s", default_value="3.0"
        ),
        DeclareLaunchArgument(
            "placement_observation_timeout_s", default_value="6.0"
        ),
        DeclareLaunchArgument(
            "placement_max_unavailable_gap_s", default_value="0.0"
        ),
        DeclareLaunchArgument(
            "expected_place_result", default_value="STABLE"
        ),
        DeclareLaunchArgument(
            "cancel_during_stability", default_value="false"
        ),
        DeclareLaunchArgument(
            "cancel_delay_s", default_value="0.15"
        ),
        bridge,
        perception,
        manipulator,
        smoke,
        shutdown_on_smoke_exit,
    ])
