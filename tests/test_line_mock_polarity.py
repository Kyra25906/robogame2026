"""巡线 mock 与控制器默认标定的**极性一致性**（2026-08-19 缺陷回归）。

背景（真实缺陷，不是假想）：
`line_sensor_mock` 把黑线读成高原始值（line_value=0.9 → raw≈3686），
而 `line_follow_node` 的默认标定曾是 white_ref=4095 / black_ref=0，方向相反。
后果不是"读数不准"，而是**语义翻转**：

- `lost`（出线，全部白底）被归一化成 1.0 → 8 路全"黑" → 状态 ALL_BLACK →
  **继续向前开**；而 `all_black` 反被判成 LOST → 停车。两个安全用例正好互换。
- `sine` 摆动下偏差恒为 0 → `wz` 恒为 0 → 控制器一次都不转向，而当时
  `line_follow_mock_smoke` 只断言"出现非零命令"，`vx_base` 就满足了 →
  **验收假通过**。

修复方向取自项目既有约定（三处互相印证），而不是"猜极性"：
- `docs/line_follow/CALIBRATION_AND_HARDWARE.md:53-58`：
  `(raw - white_min)/(black_max - white_min)`，归一化后 1.0=黑线、0.0=白底；
- `tools/field_dashboard_core.py:396-406`：同一公式，并要求 black_ref > white_ref，
  多数通道反相时判定"黑白接反"并自动交换；
- `docs/field/LINE_TELEMETRY_0x14_INTERFACE_ALIGNMENT_2026-08-19.md:29` 引用同一公式。

因此 mock（black>white）与面板是对的，`line_follow_node` 的默认值是唯一的离群者。
本测试把"三方同向"钉成断言：只改其中一处就会红。
"""
import ast
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "ros2_ws" / "src"
NODE_PATH = SRC_ROOT / "motion_control" / "motion_control" / "line_follow_node.py"
MOCK_PATH = SRC_ROOT / "motion_control" / "motion_control" / "line_sensor_mock.py"
SMOKE_PATH = (
    SRC_ROOT / "robogame_bringup" / "robogame_bringup" / "line_follow_mock_smoke.py"
)

# 节点里声明标定默认值的参数字典名（dict[str, float] 字面量）。
CALIBRATION_DEFAULTS = ("white_ref", "black_ref")


def node_calibration_defaults() -> dict:
    """从 line_follow_node 的 declare_parameter 循环里取出默认标定值。

    用 AST 而不是 import：节点模块需要 rclpy，本机（Windows）装不了。
    """
    tree = ast.parse(NODE_PATH.read_text(encoding="utf-8"))
    found: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value in CALIBRATION_DEFAULTS
                and isinstance(value, ast.Constant)
                and isinstance(value.value, (int, float))
            ):
                found[key.value] = float(value.value)
    return found


def mock_numeric_default(name: str) -> float:
    """取 line_sensor_mock 里 declare_parameter(name, <数字>) 的默认值。"""
    tree = ast.parse(MOCK_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "declare_parameter"):
            continue
        if len(node.args) != 2:
            continue
        first, second = node.args
        if (
            isinstance(first, ast.Constant)
            and first.value == name
            and isinstance(second, ast.Constant)
            and isinstance(second.value, (int, float))
            and not isinstance(second.value, bool)
        ):
            return float(second.value)
    raise AssertionError(f"line_sensor_mock 没有声明 {name} 的数值默认值")


class MockPolarityTests(unittest.TestCase):
    def test_line_reads_higher_than_floor(self):
        """黑线原始值必须大于白底——这是全项目的极性约定。"""
        line = mock_numeric_default("line_value")
        floor = mock_numeric_default("floor_value")
        self.assertGreater(
            line,
            floor,
            f"line_value={line} 必须大于 floor_value={floor}，"
            "否则黑线会被归一化成白底（语义整体翻转）",
        )

    def test_mock_defaults_are_within_analog_range(self):
        for name in ("line_value", "floor_value"):
            value = mock_numeric_default(name)
            with self.subTest(param=name):
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)


class ControllerCalibrationDirectionTests(unittest.TestCase):
    def test_node_calibration_defaults_exist(self):
        defaults = node_calibration_defaults()
        for name in CALIBRATION_DEFAULTS:
            self.assertIn(name, defaults, f"节点缺少默认标定参数 {name}")

    def test_black_reference_exceeds_white_reference(self):
        """归一化公式是 (raw - white)/(black - white)：black 必须更大。"""
        defaults = node_calibration_defaults()
        self.assertGreater(
            defaults["black_ref"],
            defaults["white_ref"],
            "line_follow_node 的默认 white_ref/black_ref 方向与 "
            "docs/line_follow/CALIBRATION_AND_HARDWARE.md 及现场面板约定相反",
        )

    def test_normalization_maps_mock_line_to_one_and_floor_to_zero(self):
        """端到端方向验证：用节点自身的默认值算一遍，不依赖任何 ROS。"""
        defaults = node_calibration_defaults()
        white, black = defaults["white_ref"], defaults["black_ref"]

        def normalize(raw: float) -> float:
            return min(1.0, max(0.0, (raw - white) / (black - white)))

        line_raw = mock_numeric_default("line_value") * 4095.0
        floor_raw = mock_numeric_default("floor_value") * 4095.0
        self.assertGreater(normalize(line_raw), 0.5, "黑线必须判为黑（> 阈值 0.5）")
        self.assertLess(normalize(floor_raw), 0.5, "白底必须判为白（< 阈值 0.5）")


class SmokeAssertsSteeringTests(unittest.TestCase):
    """验收脚本必须能发现"极性反了所以从不转向"这类假通过。"""

    @classmethod
    def setUpClass(cls):
        cls.source = SMOKE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_smoke_tracks_a_steering_counter(self):
        self.assertIn("steering_frames", self.source)

    def test_smoke_requires_steering_to_pass(self):
        tick = None
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_tick":
                tick = node
                break
        self.assertIsNotNone(tick, "smoke 里没有 _tick")
        segment = ast.get_source_segment(self.source, tick)
        self.assertIn(
            "steering_frames > 0",
            segment,
            "验收没有要求转向通道真的动过，只查非零命令会假通过",
        )

    def test_smoke_watches_angular_z(self):
        self.assertIn("angular.z", self.source)
