"""AST 结构断言 + launch 图完整性：巡线 mock 链路（本轮 A）。

防回归目标：
- line_follow_node 必须订阅 /line_sensor 与 /robot/status、发布
  /cmd_vel + /line_follow/*，并调用 LineFollowRunner（薄壳不重写算法）；
- line_sensor_mock 必须发布 /line_sensor；
- line_follow_mock.launch.py 图必须完整（订阅话题都有发布者），
  并被 launch_graph 的 entrypoint 映射覆盖。
"""
import ast
import unittest
from pathlib import Path

from tools.launch_graph import entrypoint_modules, missing_publishers

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "ros2_ws" / "src"
NODE_PATH = SRC_ROOT / "motion_control" / "motion_control" / "line_follow_node.py"
MOCK_PATH = SRC_ROOT / "motion_control" / "motion_control" / "line_sensor_mock.py"
LAUNCH_DIR = SRC_ROOT / "robogame_bringup" / "launch"
SMOKE_PATH = SRC_ROOT / "robogame_bringup" / "robogame_bringup" / "line_follow_mock_smoke.py"


def method_source(tree: ast.AST, source: str, name: str) -> str:
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return ast.get_source_segment(source, node)
    raise AssertionError(f"method {name} not found")


class LineFollowNodeStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = NODE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_declares_core_parameters(self):
        for param in (
            "kp", "kd", "vx_base", "threshold", "edge_threshold",
            "lost_threshold", "intersection_threshold", "max_reading_gap_s",
            "active_source",
        ):
            self.assertIn(
                f'"{param}"', self.source, f"missing parameter {param}"
            )

    def test_subscribes_line_sensor_and_robot_status(self):
        self.assertIn('"/line_sensor"', self.source)
        self.assertIn('"/robot/status"', self.source)

    def test_publishes_cmd_vel_and_observation_topics(self):
        self.assertIn('"/cmd_vel"', self.source)
        self.assertIn('"/line_follow/cmd"', self.source)
        self.assertIn('"/line_follow/status"', self.source)

    def test_calls_runner_instead_of_reimplementing_algorithm(self):
        # 节点是薄壳：必须调用 LineFollowRunner/runner.update，
        # 不得在节点里出现 compute_correction（算法留在纯逻辑层，为 B 备料）。
        self.assertIn("LineFollowRunner(", self.source)
        self.assertIn("runner.update", self.source)
        self.assertNotIn("compute_correction(", self.source)
        self.assertNotIn("update_sensor_state(", self.source)

    def test_tick_publishes_every_control_cycle(self):
        tick = method_source(self.tree, self.source, "_tick")
        self.assertIn("create_timer(0.02", self.source)
        self.assertIn("_publish", tick)

    def test_status_carries_machine_readable_gate_evidence(self):
        """现场面板要逐层显示"为什么没走"，所以状态里必须带结构化门控。

        只用 #diag# 之后的一段 JSON，前面仍是人可读文本——照旧看终端，
        但网页能逐层渲染断点。
        """
        publish = method_source(self.tree, self.source, "_publish")
        self.assertIn("#diag#", publish)
        self.assertIn("json.dumps", publish)
        for field in ("state", "dev", "lost", "stale", "valid_frames",
                      "invalid_frames", "out_vx", "out_wz"):
            self.assertIn(field, publish, f"missing gate evidence field {field}")
        # 门控报告整体并入 JSON：blocked / reasons 等由 _gate_report 提供，
        # 这里断言"确实被并入"，而不是逐个字段硬编码。
        self.assertIn("**gates", publish)

    def test_gate_report_lists_each_veto_point(self):
        gates = method_source(self.tree, self.source, "_gate_report")
        for key in ("status_ready", "communication_ok", "mechanism_fault",
                    "reading_stale", "active_source", "reasons"):
            self.assertIn(key, gates, f"missing gate {key}")
        # 人可读原因必须真的写进 reasons，而不是只置一个布尔
        self.assertIn("reasons.append", gates)

    def test_invalid_analog_frames_are_counted_not_silently_dropped(self):
        handler = method_source(self.tree, self.source, "_on_line_sensor")
        self.assertIn("invalid_frames += 1", handler)
        self.assertIn("analog_valid", handler)
        self.assertIn("return", handler)
        init = method_source(self.tree, self.source, "__init__")
        self.assertIn("invalid_frames = 0", init)
        self.assertIn("valid_frames = 0", init)

    def test_declares_calibration_parameters(self):
        """标定基准必须是参数，现场才能不重启进程就改。"""
        for param in ("white_ref", "black_ref", "white_offset", "black_offset"):
            self.assertIn(f'"{param}"', self.source, f"missing parameter {param}")

    def test_accepts_runtime_parameter_updates(self):
        init = method_source(self.tree, self.source, "__init__")
        self.assertIn("add_on_set_parameters_callback", init)
        self.assertIn("_on_set_parameters", init)
        callback = method_source(self.tree, self.source, "_on_set_parameters")
        self.assertIn("SetParametersResult", callback)
        # 非法组合必须整体拒绝，不能留下半套标定
        self.assertIn("successful=False", callback)
        self.assertIn("_calibration_is_usable", callback)

    def test_normalization_uses_per_channel_references(self):
        """逐路归一化：否则各路差异会让某些通道永远判不出黑线。"""
        normalize = method_source(self.tree, self.source, "_normalize")
        self.assertIn("white_ref", normalize)
        self.assertIn("black_ref", normalize)
        # 必须钳位，避免标定后漂移把偏差算飞
        self.assertIn("min(1.0", normalize)
        self.assertIn("max(0.0", normalize)
        handler = method_source(self.tree, self.source, "_on_line_sensor")
        self.assertIn("self._normalize(", handler)
        # 不再用写死的 4095 归一化
        self.assertNotIn("/ 4095.0", handler)

    def test_calibration_restores_per_channel_from_offsets(self):
        read = method_source(self.tree, self.source, "_read_calibration")
        self.assertIn("white_offset", read)
        self.assertIn("black_offset", read)
        self.assertIn("white_base + offset", read)


class LineSensorMockStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MOCK_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_publishes_line_sensor_and_runs_on_timer(self):
        self.assertIn('"/line_sensor"', self.source)
        self.assertIn("create_timer(", self.source)

    def test_has_pattern_parameter_and_known_patterns(self):
        self.assertIn('"pattern"', self.source)
        for pattern in ("centered", "left", "right", "sine", "lost",
                        "intersection", "all_black"):
            self.assertIn(f'"{pattern}"', self.source)


class LineFollowLaunchGraphTests(unittest.TestCase):
    def test_entrypoints_are_registered(self):
        mapping = entrypoint_modules(SRC_ROOT)
        self.assertIn(("motion_control", "line_follow_controller"), mapping)
        self.assertIn(("motion_control", "line_sensor_mock"), mapping)
        self.assertIn(("robogame_bringup", "line_follow_mock_smoke"), mapping)

    def test_line_follow_mock_graph_is_complete(self):
        # B2：巡线节点新增订阅 /mission/active_source（授权门控）；
        # B3：又新增 /mission/turn（转弯命令）与 /mission/line（每段限速/坡道参数）。
        # 本图刻意不启动任务层（独立联调：授权默认关闭、也没有转弯/坡道指令）——
        # 所以只允许这三条已解释缺口；其他缺口仍然红。
        # 豁免成立条件见 tests/test_launch_graph.py
        # ::test_standalone_line_launch_justifies_its_active_source_exemption
        gaps = missing_publishers(
            LAUNCH_DIR / "line_follow_mock.launch.py", SRC_ROOT
        )
        self.assertEqual(
            set(gaps),
            {"/mission/active_source", "/mission/turn", "/mission/line"},
            f"line_follow_mock launch graph gaps changed: {gaps}",
        )

    def test_launch_contains_all_four_nodes(self):
        source = (
            LAUNCH_DIR / "line_follow_mock.launch.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"line_sensor_mock"', source)
        self.assertIn('"line_follow_controller"', source)
        self.assertIn('"robot_bridge"', source)
        self.assertIn('"line_follow_mock_smoke"', source)


if __name__ == "__main__":
    unittest.main()
