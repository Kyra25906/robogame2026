"""A0.3 launch 参数层完整性测试（杀 P0-3 的通用版本）。

背景（执行队列 2026-08-18 A0.3）：A3 已对 cube_perception 做了单点断言
（`test_field_layer_vision_focal_overrides_common`，hardware.launch.py 必须
给 cube_perception 传 `[common, field]`）；本测试把同一类断言**推广到全部
生产 launch、全部核心节点**——任何节点漏传 common 或漏传它所需的环境层
（mock/field/single，以对应 yaml 是否有该节点段落为准）都会红。

规则（`tools/launch_graph.py::parameter_layer_issues`）：
A. 核心节点必须带 common 基座层；
B. 环境层 yaml 里有该节点段落时，节点必须带该层（P0-3 即此类的单点）；
C. 核心节点不允许完全没有参数（mock_perception 等登记例外）；
D. parameters 引用的变量名必须是本 launch 定义的配置层（防拼写）。

smoke launch（field_no_hardware_smoke / manipulator_mock_smoke）是测试脚手架，
刻意内联参数不走 common，跳过严格校验。
"""
import os
import tempfile
import unittest
from pathlib import Path

from tools.launch_graph import (
    _layer_variables,
    _node_parameter_info,
    _yaml_top_level_keys,
    parameter_layer_issues,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "ros2_ws" / "src"
LAUNCH_DIR = SRC_ROOT / "robogame_bringup" / "launch"
CONFIG_DIR = SRC_ROOT / "robogame_bringup" / "config"

COMMON_YAML = "robot_bridge:\n  ros__parameters:\n    command_timeout_s: 0.5\n"
FIELD_YAML = (
    "robot_bridge:\n  ros__parameters:\n    serial_port: /dev/ttyACM0\n"
    "manipulator_client:\n  ros__parameters:\n    runtime_mode: field\n"
    "cube_perception:\n  ros__parameters:\n    fallback_focal_px: 1275.0\n"
)
MOCK_YAML = (
    "robot_bridge:\n  ros__parameters:\n    mock_mode: true\n"
    "manipulator_client:\n  ros__parameters:\n    runtime_mode: mock\n"
)
SINGLE_YAML = "mission_manager:\n  ros__parameters:\n    orange_target: 1\n"

LAUNCH_HEADER = (
    "from launch import LaunchDescription\n"
    "from launch_ros.actions import Node\n"
    "import os\n\n"
    "def generate_launch_description():\n"
    '    share = "share"\n'
    '    common = os.path.join(share, "config", "robot.yaml")\n'
)


def _write_temp_launch(tmp: Path, name: str, body: str) -> Path:
    """在临时 src_root 布局下写一个 launch 文件。

    布局与真实仓库一致：<root>/src/robogame_bringup/{launch,config}/...，
    这样 launch_graph 的 `parents[3]` 推导出的 src_root 与真实布局一致。
    """
    src_root = tmp / "src"
    launch_dir = src_root / "robogame_bringup" / "launch"
    config_dir = src_root / "robogame_bringup" / "config"
    launch_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "robot.yaml").write_text(COMMON_YAML, encoding="utf-8")
    (config_dir / "robot_field.yaml").write_text(FIELD_YAML, encoding="utf-8")
    (config_dir / "robot_mock.yaml").write_text(MOCK_YAML, encoding="utf-8")
    (config_dir / "single_cube.yaml").write_text(SINGLE_YAML, encoding="utf-8")
    launch = launch_dir / name
    launch.write_text(body, encoding="utf-8")
    return launch


def _launch_body(nodes: str, extra: str = "") -> str:
    return (
        LAUNCH_HEADER
        + extra
        + "    return LaunchDescription([\n"
        + nodes
        + "    ])\n"
    )


# hardware 风格的负例需要与真实 hardware.launch.py 一致：同时定义 common 与 field。
EXTRA_FIELD = '    field = os.path.join(share, "config", "robot_field.yaml")\n'


class LaunchParameterLayerRealLaunchesTests(unittest.TestCase):
    """真实生产 launch：每个节点都拿到它需要的全部配置层。"""

    def test_hardware_launch_has_complete_parameter_layers(self):
        issues = parameter_layer_issues(LAUNCH_DIR / "hardware.launch.py", SRC_ROOT)
        self.assertEqual(issues, [], f"hardware.launch.py issues: {issues}")

    def test_mock_demo_launch_has_complete_parameter_layers(self):
        issues = parameter_layer_issues(LAUNCH_DIR / "mock_demo.launch.py", SRC_ROOT)
        self.assertEqual(issues, [], f"mock_demo.launch.py issues: {issues}")

    def test_single_cube_launch_has_complete_parameter_layers(self):
        issues = parameter_layer_issues(LAUNCH_DIR / "single_cube.launch.py", SRC_ROOT)
        self.assertEqual(issues, [], f"single_cube.launch.py issues: {issues}")

    def test_line_follow_hardware_launch_has_complete_parameter_layers(self):
        # 回归：该 launch 原先完全不传 params 文件，两个节点退回节点默认值
        # （serial_port=/dev/ttyACM0、command_timeout_s=0.15、
        # max_mcu_sample_gap_ms=250），会静默回退 2026-08-18 真车联调修好的
        # 零速插入顿挫与 LOCALIZATION_ERROR 停车。它是巡线真车入口，必须有配置层。
        issues = parameter_layer_issues(
            LAUNCH_DIR / "line_follow_hardware.launch.py", SRC_ROOT
        )
        self.assertEqual(issues, [], f"line_follow_hardware.launch.py issues: {issues}")

    def test_line_follow_hardware_bridge_gets_common_and_field(self):
        # robot_bridge 必须同时拿到 common(robot.yaml) 与 field(robot_field.yaml)：
        # serial_port 的 by-id 路径只在 robot_field.yaml 里。
        for package, executable, layers, _inline, _names in _node_parameter_info(
            LAUNCH_DIR / "line_follow_hardware.launch.py"
        ):
            if (package, executable) == ("robot_bridge", "robot_bridge"):
                self.assertIn("common", layers)
                self.assertIn("field", layers)
                break
        else:  # pragma: no cover - 解析器漏了该节点则测试失败
            self.fail("robot_bridge not found in line_follow_hardware.launch.py")

    def test_cube_perception_in_hardware_gets_common_and_field(self):
        # A3 / P0-3 的显式回归：hardware 里 cube_perception 必须拿到
        # [common, field]（原漏传 field，实机用 700.0 焦距）。
        for package, executable, layers, _inline, _names in _node_parameter_info(
            LAUNCH_DIR / "hardware.launch.py"
        ):
            if (package, executable) == ("cube_perception", "cube_perception"):
                self.assertIn("common", layers)
                self.assertIn("field", layers)
                break
        else:  # pragma: no cover - 解析器漏了该节点则测试失败
            self.fail("cube_perception not found in hardware.launch.py")

    def test_smoke_launches_are_skipped(self):
        # 测试脚手架刻意内联参数，不走 common；跳过严格校验。
        self.assertEqual(
            parameter_layer_issues(
                LAUNCH_DIR / "field_no_hardware_smoke.launch.py", SRC_ROOT
            ),
            [],
        )
        self.assertEqual(
            parameter_layer_issues(
                LAUNCH_DIR / "manipulator_mock_smoke.launch.py", SRC_ROOT
            ),
            [],
        )


class LaunchParameterParserTests(unittest.TestCase):
    """解析器本身的正确性（防解析器悄悄漏层/漏节点）。"""

    def test_layer_variables_of_hardware_launch(self):
        layers = _layer_variables(LAUNCH_DIR / "hardware.launch.py", SRC_ROOT)
        self.assertEqual(set(layers), {"common", "field"})
        self.assertEqual(layers["common"].name, "robot.yaml")
        self.assertEqual(layers["field"].name, "robot_field.yaml")

    def test_layer_variables_of_single_cube_launch(self):
        layers = _layer_variables(LAUNCH_DIR / "single_cube.launch.py", SRC_ROOT)
        self.assertEqual(set(layers), {"common", "mock", "single"})

    def test_env_yaml_top_level_keys(self):
        self.assertEqual(
            _yaml_top_level_keys(CONFIG_DIR / "robot_field.yaml"),
            # B2：field 层新增 mission_manager（route_enabled）——因此
            # hardware.launch.py 必须给 mission_manager 传 field 层（规则 B）。
            {"robot_bridge", "manipulator_client", "cube_perception", "mission_manager"},
        )
        self.assertEqual(
            _yaml_top_level_keys(CONFIG_DIR / "robot_mock.yaml"),
            {"robot_bridge", "manipulator_client"},
        )
        self.assertEqual(
            _yaml_top_level_keys(CONFIG_DIR / "single_cube.yaml"),
            {"mission_manager"},
        )

    def test_node_parameter_info_of_mock_demo(self):
        info = {
            (pkg, exe): layers
            for pkg, exe, layers, _inline, _names in _node_parameter_info(
                LAUNCH_DIR / "mock_demo.launch.py"
            )
        }
        self.assertEqual(info[("robot_bridge", "robot_bridge")], {"common", "mock"})
        self.assertEqual(
            info[("manipulator_client", "manipulator_client")], {"common", "mock"}
        )
        self.assertEqual(info[("localization", "localization_node")], {"common"})
        # mock_perception 无参数（登记例外）。
        self.assertEqual(info[("cube_perception", "mock_perception")], set())


class LaunchParameterNegativeTests(unittest.TestCase):
    """反向证明：P0-3 类缺陷在任意节点上都会以测试红暴露。"""

    def test_cube_perception_missing_field_is_detected(self):
        # 模拟修复前的 P0-3：cube_perception 只带 common、漏 field。
        with tempfile.TemporaryDirectory() as td:
            launch = _write_temp_launch(
                Path(td),
                "hardware.launch.py",
                _launch_body(
                    '        Node(package="cube_perception", '
                    'executable="cube_perception", parameters=[common]),\n',
                    extra=EXTRA_FIELD,
                ),
            )
            issues = parameter_layer_issues(launch, Path(td) / "src")
            self.assertTrue(
                any("missing field layer" in i.message for i in issues),
                f"expected missing-field issue, got: {issues}",
            )

    def test_node_missing_common_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            launch = _write_temp_launch(
                Path(td),
                "hardware.launch.py",
                _launch_body(
                    '        Node(package="cube_perception", '
                    'executable="cube_perception", parameters=[field]),\n',
                    extra=EXTRA_FIELD,
                ),
            )
            issues = parameter_layer_issues(launch, Path(td) / "src")
            self.assertTrue(
                any("missing common layer" in i.message for i in issues),
                f"expected missing-common issue, got: {issues}",
            )

    def test_node_without_parameters_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            launch = _write_temp_launch(
                Path(td),
                "mock_demo.launch.py",
                _launch_body(
                    '        Node(package="robot_bridge", executable="robot_bridge"),\n'
                ),
            )
            issues = parameter_layer_issues(launch, Path(td) / "src")
            self.assertTrue(
                any("no parameters at all" in i.message for i in issues),
                f"expected no-parameters issue, got: {issues}",
            )

    def test_undefined_layer_name_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            launch = _write_temp_launch(
                Path(td),
                "hardware.launch.py",
                _launch_body(
                    '        Node(package="robot_bridge", executable="robot_bridge", '
                    'parameters=[common, flied]),\n',
                    extra=EXTRA_FIELD,
                ),
            )
            issues = parameter_layer_issues(launch, Path(td) / "src")
            self.assertTrue(
                any("undefined parameter layer 'flied'" in i.message for i in issues),
                f"expected undefined-layer issue, got: {issues}",
            )

    def test_parameter_free_mock_perception_is_exempt(self):
        with tempfile.TemporaryDirectory() as td:
            launch = _write_temp_launch(
                Path(td),
                "mock_demo.launch.py",
                _launch_body(
                    '        Node(package="cube_perception", '
                    'executable="mock_perception"),\n'
                ),
            )
            self.assertEqual(parameter_layer_issues(launch, Path(td) / "src"), [])

    def test_complete_temp_launch_has_no_issues(self):
        # 正确配置的临时 launch 应零问题（证明分析器不误报）。
        with tempfile.TemporaryDirectory() as td:
            launch = _write_temp_launch(
                Path(td),
                "hardware.launch.py",
                _launch_body(
                    '        Node(package="robot_bridge", executable="robot_bridge", '
                    'parameters=[common, field]),\n'
                    '        Node(package="cube_perception", executable="cube_perception", '
                    'parameters=[common, field]),\n'
                    '        Node(package="manipulator_client", executable="manipulator_client", '
                    'parameters=[common, field]),\n'
                    '        Node(package="localization", executable="localization_node", '
                    'parameters=[common]),\n',
                    extra=EXTRA_FIELD,
                ),
            )
            self.assertEqual(parameter_layer_issues(launch, Path(td) / "src"), [])


if __name__ == "__main__":
    unittest.main()
