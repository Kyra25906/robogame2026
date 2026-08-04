from launch import LaunchDescription
from launch.actions import EmitEvent, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch_ros.actions import Node


def generate_launch_description():
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
        }],
    )
    smoke = Node(
        package="robogame_bringup",
        executable="manipulator_mock_smoke",
        output="screen",
    )
    shutdown_on_smoke_exit = RegisterEventHandler(OnProcessExit(
        target_action=smoke,
        on_exit=[EmitEvent(event=Shutdown(reason="manipulator smoke finished"))],
    ))
    return LaunchDescription([
        bridge,
        perception,
        manipulator,
        smoke,
        shutdown_on_smoke_exit,
    ])
