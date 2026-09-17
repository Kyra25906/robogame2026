from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    pattern = LaunchConfiguration("pattern")
    vx_base = LaunchConfiguration("vx_base")
    sample_duration = LaunchConfiguration("sample_duration_s")

    bridge = Node(
        package="robot_bridge",
        executable="robot_bridge",
        parameters=[{
            "mock_mode": True,
            "mock_start_after_s": 0.0,
        }],
    )
    sensor_mock = Node(
        package="motion_control",
        executable="line_sensor_mock",
        parameters=[{
            "pattern": ParameterValue(pattern, value_type=str),
        }],
    )
    line_follow = Node(
        package="motion_control",
        executable="line_follow_controller",
        parameters=[{
            "active_source": "line_follow",
            "vx_base": ParameterValue(vx_base, value_type=float),
        }],
    )
    smoke = Node(
        package="robogame_bringup",
        executable="line_follow_mock_smoke",
        output="screen",
        parameters=[{
            "sample_duration_s": ParameterValue(
                sample_duration, value_type=float
            ),
        }],
    )
    shutdown_on_smoke_exit = RegisterEventHandler(OnProcessExit(
        target_action=smoke,
        on_exit=[EmitEvent(event=Shutdown(reason="line follow smoke finished"))],
    ))
    return LaunchDescription([
        DeclareLaunchArgument("pattern", default_value="sine"),
        DeclareLaunchArgument("vx_base", default_value="0.1"),
        DeclareLaunchArgument("sample_duration_s", default_value="3.0"),
        bridge,
        sensor_mock,
        line_follow,
        smoke,
        shutdown_on_smoke_exit,
    ])
