"""Real STM32 line telemetry to controller graph; no mock sensor or smoke shutdown."""
from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    share = get_package_share_directory("robogame_bringup")
    # 与 hardware.launch.py 同规则：common 层 + 环境层。
    # 必须传参，否则两个节点会退回节点默认值（robot_bridge 默认
    # serial_port=/dev/ttyACM0、command_timeout_s=0.15、max_mcu_sample_gap_ms=250），
    # 从而静默回退 2026-08-18 真车联调修好的两个问题（零速插入顿挫、
    # LOCALIZATION_ERROR 停车）。
    common = os.path.join(share, "config", "robot.yaml")
    field = os.path.join(share, "config", "robot_field.yaml")
    return LaunchDescription([
        Node(package="robot_bridge", executable="robot_bridge",
             parameters=[common, field, {"mock_mode": False}], output="screen"),
        Node(package="motion_control", executable="line_follow_controller",
             parameters=[common, field, {
                 "active_source": "line_follow",
                 # 本图有意不启动任务层，没有 /mission/active_source 可用。
                 # robot.yaml（共用层）已默认 false，但 robot_field.yaml（比赛层）
                 # 会打开授权；本图同样加载 field 层，因此必须显式覆盖回 false，
                 # 否则独立巡线联调时节点永远等不到授权、一动不动。
                 # 见 tests/test_launch_graph.py 的
                 # test_standalone_line_launch_justifies_its_active_source_exemption。
                 "require_authorization": False,
             }],
             output="screen"),
    ])
