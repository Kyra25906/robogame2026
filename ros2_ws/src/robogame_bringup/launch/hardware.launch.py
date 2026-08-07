from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    share = get_package_share_directory("robogame_bringup")
    common = os.path.join(share, "config", "robot.yaml")
    field = os.path.join(share, "config", "robot_field.yaml")
    return LaunchDescription([
        Node(package="robot_bridge", executable="robot_bridge", parameters=[common, field]),
        Node(package="localization", executable="localization_node", parameters=[common]),
        Node(package="motion_control", executable="motion_controller", parameters=[common]),
        Node(package="cube_perception", executable="cube_perception", parameters=[common]),
        Node(package="manipulator_client", executable="manipulator_client", parameters=[common, field]),
        Node(package="mission_manager", executable="mission_manager", parameters=[common]),
    ])
