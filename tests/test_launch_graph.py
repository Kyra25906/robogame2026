"""A0.2 launch 图完整性测试（杀 P0-2）。

断言每个 launch 图里被订阅的话题都有发布者。hardware.launch.py 当前
缺少相机节点（P0-2），因此把已知缺口显式登记在案：断言缺失话题恰好等于
{/camera/image_raw, /camera/camera_info}。B1（camera.launch.py + include）
修复后缺口消失，此测试会红并强制更新登记。

背景：执行队列 2026-08-18 A0 节「A0.2 launch 图完整性测试：解析 launch
文件，断言每个被订阅的话题都有发布者 → 杀 P0-2」。
"""
import unittest
from pathlib import Path

from tools.launch_graph import (
    analyze_all_launches,
    entrypoint_modules,
    missing_publishers,
    node_topics,
    parse_launch_nodes,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "ros2_ws" / "src"
LAUNCH_DIR = SRC_ROOT / "robogame_bringup" / "launch"

# P0-2 已由 B1（camera.launch.py + hardware.launch.py include）修复：
# usb_cam 发布 /camera/image_raw 与 /camera/camera_info，缺口集合应为空。
# 若未来再出现缺口，此测试会红并强制更新登记。
KNOWN_HARDWARE_GAPS = set()


class LaunchGraphCompletenessTests(unittest.TestCase):
    """每个被订阅的话题都有发布者（除显式登记的已知缺口）。"""

    def test_mock_demo_graph_is_complete(self):
        gaps = missing_publishers(LAUNCH_DIR / "mock_demo.launch.py", SRC_ROOT)
        self.assertEqual(gaps, {}, f"mock_demo launch graph has gaps: {gaps}")

    def test_single_cube_graph_is_complete(self):
        gaps = missing_publishers(LAUNCH_DIR / "single_cube.launch.py", SRC_ROOT)
        self.assertEqual(gaps, {}, f"single_cube launch graph has gaps: {gaps}")

    def test_field_no_hardware_smoke_graph_is_complete(self):
        gaps = missing_publishers(
            LAUNCH_DIR / "field_no_hardware_smoke.launch.py", SRC_ROOT
        )
        self.assertEqual(gaps, {}, f"field smoke launch graph has gaps: {gaps}")

    def test_manipulator_mock_smoke_graph_is_complete(self):
        gaps = missing_publishers(
            LAUNCH_DIR / "manipulator_mock_smoke.launch.py", SRC_ROOT
        )
        self.assertEqual(gaps, {}, f"manipulator smoke launch graph has gaps: {gaps}")

    def test_hardware_graph_gaps_match_known_p02_registration(self):
        # P0-2 已由 B1 修复：hardware.launch.py 的相机话题现在有发布者，
        # 缺口登记应为空。若缺口非空（= B1 回退或新缺口），测试红。
        gaps = missing_publishers(LAUNCH_DIR / "hardware.launch.py", SRC_ROOT)
        self.assertEqual(
            set(gaps),
            KNOWN_HARDWARE_GAPS,
            "hardware launch graph gaps drifted from the P0-2 registration: "
            f"{sorted(gaps)} != {sorted(KNOWN_HARDWARE_GAPS)}. "
            "B1 (camera.launch.py) should have closed the P0-2 gap; "
            "a non-empty result here means the camera include regressed.",
        )

    def test_camera_launch_itself_is_complete(self):
        # camera.launch.py 单独运行：usb_cam 只发布不订阅，无缺口。
        gaps = missing_publishers(LAUNCH_DIR / "camera.launch.py", SRC_ROOT)
        self.assertEqual(gaps, {}, f"camera.launch.py has gaps: {gaps}")

    def test_all_launch_graphs_have_no_unregistered_gaps(self):
        # 除 hardware 的 P0-2 登记外，任何 launch 图都不允许出现新缺口。
        all_gaps = analyze_all_launches(SRC_ROOT)
        for launch, gaps in sorted(all_gaps.items()):
            if launch == "hardware.launch.py":
                self.assertEqual(set(gaps), KNOWN_HARDWARE_GAPS, launch)
            else:
                self.assertEqual(gaps, {}, f"{launch} has unregistered gaps: {gaps}")


class LaunchGraphParserTests(unittest.TestCase):
    """解析器本身的正确性（防解析器悄悄漏节点/漏话题）。"""

    def test_entrypoint_mapping_contains_core_nodes(self):
        mapping = entrypoint_modules(SRC_ROOT)
        for key in (
            ("robot_bridge", "robot_bridge"),
            ("localization", "localization_node"),
            ("motion_control", "motion_controller"),
            ("cube_perception", "cube_perception"),
            ("cube_perception", "mock_perception"),
            ("manipulator_client", "manipulator_client"),
            ("mission_manager", "mission_manager"),
            ("robogame_bringup", "runtime_source_guard"),
            ("robogame_bringup", "field_no_hardware_smoke"),
            ("robogame_bringup", "manipulator_mock_smoke"),
        ):
            self.assertIn(key, mapping, f"missing entry point {key}")
        self.assertEqual(mapping[("cube_perception", "mock_perception")],
                         "cube_perception.mock_node")

    def test_launch_node_lists_are_parsed(self):
        mock_nodes = parse_launch_nodes(LAUNCH_DIR / "mock_demo.launch.py")
        expected = {
            ("robot_bridge", "robot_bridge"),
            ("localization", "localization_node"),
            ("motion_control", "motion_controller"),
            ("cube_perception", "mock_perception"),
            ("manipulator_client", "manipulator_client"),
            ("mission_manager", "mission_manager"),
            ("robogame_bringup", "runtime_source_guard"),
        }
        self.assertEqual(set(mock_nodes), expected)

    def test_cube_perception_topics_include_camera_subscriptions(self):
        subscribed, published = node_topics(
            SRC_ROOT / "cube_perception" / "cube_perception" / "node.py"
        )
        self.assertIn("/camera/image_raw", subscribed)
        self.assertIn("/camera/camera_info", subscribed)
        self.assertIn("/cubes", published)

    def test_robot_bridge_topics(self):
        subscribed, published = node_topics(
            SRC_ROOT / "robot_bridge" / "robot_bridge" / "node.py"
        )
        self.assertEqual(subscribed, {"/cmd_vel"})
        self.assertEqual(
            published, {"/robot/status", "/wheel_odom", "/imu/data"}
        )

    def test_mission_manager_links_motion_and_manipulator(self):
        subscribed, published = node_topics(
            SRC_ROOT / "mission_manager" / "mission_manager" / "node.py"
        )
        self.assertEqual(
            subscribed, {"/robot/status", "/motion/result", "/manipulator/result"}
        )
        self.assertIn("/motion/goal", published)
        self.assertIn("/manipulator/command", published)


if __name__ == "__main__":
    unittest.main()
