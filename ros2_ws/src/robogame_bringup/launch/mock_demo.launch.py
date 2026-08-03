from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    config = os.path.join(get_package_share_directory("robogame_bringup"), "config", "robot.yaml")
    return LaunchDescription([
        Node(package="robot_bridge", executable="robot_bridge", parameters=[config]),
        Node(package="localization", executable="localization_node", parameters=[config]),
        Node(package="motion_control", executable="motion_controller", parameters=[config]),
        Node(package="cube_perception", executable="mock_perception"),
        Node(package="manipulator_client", executable="manipulator_client", parameters=[config]),
        Node(package="mission_manager", executable="mission_manager", parameters=[config]),
    ])

