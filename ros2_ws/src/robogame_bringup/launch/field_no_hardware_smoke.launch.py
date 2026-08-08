from launch import LaunchDescription
from launch.actions import EmitEvent, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch_ros.actions import Node


def generate_launch_description():
    bridge = Node(
        package="robot_bridge",
        executable="robot_bridge",
        output="screen",
        parameters=[{
            "mock_mode": False,
            "serial_port": "/dev/robogame_intentionally_missing",
            "baud_rate": 115200,
        }],
    )
    smoke = Node(
        package="robogame_bringup",
        executable="field_no_hardware_smoke",
        output="screen",
    )
    shutdown_on_smoke_exit = RegisterEventHandler(OnProcessExit(
        target_action=smoke,
        on_exit=[EmitEvent(event=Shutdown(reason="field no-hardware smoke finished"))],
    ))
    return LaunchDescription([bridge, smoke, shutdown_on_smoke_exit])
