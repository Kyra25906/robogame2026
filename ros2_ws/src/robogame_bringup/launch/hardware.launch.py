from launch import LaunchDescription
from launch.actions import EmitEvent, IncludeLaunchDescription, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    share = get_package_share_directory("robogame_bringup")
    common = os.path.join(share, "config", "robot.yaml")
    field = os.path.join(share, "config", "robot_field.yaml")
    # B1: 相机驱动（usb_cam 640x480 @ 30fps MJPG + 静态 CameraInfo）。
    # 现场如需临时禁用相机，注释掉这一行即可。
    camera = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(share, "launch", "camera.launch.py")
        )
    )
    source_guard = Node(
        package="robogame_bringup",
        executable="runtime_source_guard",
        output="screen",
        parameters=[{
            "expected_mode": "field",
            "sample_duration_s": 3.0,
            "stay_alive_on_pass": True,
        }],
    )
    shutdown_on_guard_exit = RegisterEventHandler(OnProcessExit(
        target_action=source_guard,
        on_exit=[EmitEvent(event=Shutdown(reason="runtime source guard exited"))],
    ))
    return LaunchDescription([
        camera,
        Node(package="robot_bridge", executable="robot_bridge", parameters=[common, field]),
        Node(package="localization", executable="localization_node", parameters=[common]),
        Node(package="motion_control", executable="motion_controller", parameters=[common]),
        Node(package="cube_perception", executable="cube_perception", parameters=[common, field]),
        Node(package="manipulator_client", executable="manipulator_client", parameters=[common, field]),
        Node(package="mission_manager", executable="mission_manager", parameters=[common]),
        source_guard,
        shutdown_on_guard_exit,
    ])
