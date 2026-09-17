"""Real STM32 line telemetry to controller graph; no mock sensor or smoke shutdown."""
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(package="robot_bridge", executable="robot_bridge",
             parameters=[{"mock_mode": False}], output="screen"),
        Node(package="motion_control", executable="line_follow_controller",
             parameters=[{"active_source": "line_follow"}], output="screen"),
    ])
