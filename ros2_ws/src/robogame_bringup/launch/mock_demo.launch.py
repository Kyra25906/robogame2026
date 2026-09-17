from launch import LaunchDescription
from launch.actions import EmitEvent, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    share = get_package_share_directory("robogame_bringup")
    common = os.path.join(share, "config", "robot.yaml")
    mock = os.path.join(share, "config", "robot_mock.yaml")
    source_guard = Node(
        package="robogame_bringup",
        executable="runtime_source_guard",
        output="screen",
        parameters=[{
            "expected_mode": "mock",
            "sample_duration_s": 3.0,
            "stay_alive_on_pass": True,
        }],
    )
    shutdown_on_guard_exit = RegisterEventHandler(OnProcessExit(
        target_action=source_guard,
        on_exit=[EmitEvent(event=Shutdown(reason="runtime source guard exited"))],
    ))
    return LaunchDescription([
        Node(package="robot_bridge", executable="robot_bridge", parameters=[common, mock]),
        Node(package="localization", executable="localization_node", parameters=[common]),
        Node(package="motion_control", executable="motion_controller", parameters=[common]),
        # B2：mission_manager 现在订阅 /line_follow/status 与 /pose（路线模式用），
        # 所以图里必须有巡线节点提供它；未授权时它保持沉默，不会和 motion_controller 抢 /cmd_vel。
        Node(package="motion_control", executable="line_follow_controller", parameters=[common]),
        Node(package="cube_perception", executable="mock_perception"),
        Node(package="manipulator_client", executable="manipulator_client", parameters=[common, mock]),
        Node(package="mission_manager", executable="mission_manager", parameters=[common]),
        source_guard,
        shutdown_on_guard_exit,
    ])
