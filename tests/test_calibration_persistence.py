"""B4：标定落盘的端到端接线测试（行为级，用真实控制器 + Mock ROS）。

为什么必须行为测试：这一段的错误模式是「看起来成功」——标定推给了运行中的节点、
页面显示「已生效」，但**没落盘**；于是断电重启后节点回到只有方向意义的默认基准，
上电自主直接失效，而现场没有任何提示。所以这里真的跑一遍
「采集 → 应用 → 检查文件」，并且专门验证**写盘失败必须报错**。

另外还测了节点侧的启动加载路径（AST）：`calibration_file` 参数、加载失败大声报警、
状态串里带标定来源（网页可见）。
"""

from __future__ import annotations

import ast
import asyncio
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from tools.field_dashboard import DashboardController, SafetyStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LINE_NODE = PROJECT_ROOT / "ros2_ws/src/motion_control/motion_control/line_follow_node.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


class CalibrationPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def app(self, calibration_file: str = "") -> DashboardController:
        console = Mock()
        console.items = {}
        console.start = AsyncMock()
        console.stop = AsyncMock()
        app = DashboardController(
            console, Mock(), Mock(), asyncio.get_running_loop(),
            calibration_file=calibration_file,
        )
        app.record = Mock()
        app.ros = Mock()
        app.ros.publishers.return_value = []
        app.ros.mechanism.return_value = {"success": True}
        app.state.safety = SafetyStatus(
            received_at=time.monotonic(), communication_ok=True, physical_start=True, boot_id=1
        )
        return app

    def _feed_samples(self, app: DashboardController, *, white=600.0, black=2800.0,
                      frames: int = 30) -> None:
        """喂两组基准（白底/黑线各若干帧）。

        帧数要够：面板要求每组采集 ≥1.0 s（这里 30 帧 × 0.05 s = 1.5 s），
        否则 `begin_apply` 会以「采集时间不足」拒绝——这也说明阈值真的在起作用。
        """
        now = time.monotonic()
        app.line_calibration.begin_capture("white")
        for frame in range(frames):
            app.line_calibration.capture(
                "white", [int(white + index) for index in range(8)], now=now + 0.05 * frame
            )
        app.line_calibration.end_capture()
        app.line_calibration.begin_capture("black")
        for frame in range(frames):
            app.line_calibration.capture(
                "black", [int(black + index) for index in range(8)], now=now + 0.05 * frame
            )
        app.line_calibration.end_capture()

    async def test_short_capture_is_rejected(self):
        """采集时间不足时不许写入（否则可能标到一半的噪声上）。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cal.json"
            app = self.app(str(target))
            self._feed_samples(app, frames=6)  # 0.3 s
            with self.assertRaises(ValueError) as caught:
                await app._calibrate_apply({})
            self.assertIn("时间不足", str(caught.exception))
            self.assertFalse(target.exists())

    async def test_apply_writes_the_calibration_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cal.json"
            app = self.app(str(target))
            self._feed_samples(app)
            app.ros.push_line_calibration.return_value = {
                "ok": True, "detail": "已推送到巡线节点",
                "white_ref": [600.0 + i for i in range(8)],
                "black_ref": [2800.0 + i for i in range(8)],
            }
            result = await app._calibrate_apply({})
            self.assertTrue(result["ok"])
            self.assertEqual(result["saved_to"], str(target))
            self.assertTrue(target.is_file(), "标定必须真的落盘")
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(len(data["white_ref"]), 8)
            self.assertEqual(len(data["black_ref"]), 8)
            self.assertEqual(data["source"], "网页面板标定")
            # 落盘的标定必须能被节点侧加载并判定为可用
            from robogame_core.line_calibration import load_calibration, startup_verdict

            usable, reason = startup_verdict(load_calibration(target))
            self.assertTrue(usable, reason)

    async def test_apply_reports_failure_when_the_disk_write_fails(self):
        """写盘失败绝不能报成功——否则现场以为标定保住了，重启才发现丢了。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 把路径指到一个目录上：写文件必然失败
            app = self.app(tmp)
            self._feed_samples(app)
            app.ros.push_line_calibration.return_value = {
                "ok": True, "detail": "已推送到巡线节点",
                "white_ref": [600.0 + i for i in range(8)],
                "black_ref": [2800.0 + i for i in range(8)],
            }
            with self.assertRaises(ValueError) as caught:
                await app._calibrate_apply({})
            message = str(caught.exception)
            self.assertIn("写盘失败", message)
            self.assertIn("上电自主", message)
            self.assertEqual(app.line_calibration_result["state"], "已推送未落盘")

    async def test_apply_still_rejects_a_failed_calibration(self):
        """黑白不达标时既不该推送、也不该落盘。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cal.json"
            app = self.app(str(target))
            # 白底与黑线几乎相同 → 校验必然不过
            self._feed_samples(app, white=1000.0, black=1005.0)
            with self.assertRaises(ValueError):
                await app._calibrate_apply({})
            self.assertFalse(target.exists(), "校验没过就不该写盘")

    async def test_push_failure_does_not_write_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cal.json"
            app = self.app(str(target))
            self._feed_samples(app)
            app.ros.push_line_calibration.return_value = {
                "ok": False, "detail": "巡线节点拒绝了标定参数", "error_code": 4,
            }
            with self.assertRaises(ValueError):
                await app._calibrate_apply({})
            self.assertFalse(target.exists())

    async def test_saved_path_is_exposed_to_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cal.json"
            app = self.app(str(target))
            self._feed_samples(app)
            app.ros.push_line_calibration.return_value = {
                "ok": True, "detail": "已推送",
                "white_ref": [600.0 + i for i in range(8)],
                "black_ref": [2800.0 + i for i in range(8)],
            }
            await app._calibrate_apply({})
            snapshot = app.snapshot()
            self.assertEqual(snapshot["line_calibration"]["applied"]["saved_to"], str(target))


class LineNodeCalibrationLoadTests(unittest.TestCase):
    """节点侧：上电时加载标定，失败要大声、状态要可见。"""

    @classmethod
    def setUpClass(cls):
        cls.tree = _tree(LINE_NODE)
        cls.text = LINE_NODE.read_text(encoding="utf-8")

    def test_node_declares_a_calibration_file_parameter(self):
        self.assertIn('"calibration_file"', self.text)
        loader = ast.unparse(_function(self.tree, "_load_persisted_calibration"))
        self.assertIn("load_calibration", loader)
        self.assertIn("startup_verdict", loader)

    def test_missing_calibration_is_loud_not_silent(self):
        """没有标定时必须 error/warn 说明「基准只有方向意义」，不能静默跑默认值。"""
        loader = ast.unparse(_function(self.tree, "_load_persisted_calibration"))
        self.assertIn("get_logger().warn", loader)
        self.assertIn("get_logger().error", loader)

    def test_load_failure_keeps_the_defaults_but_reports_why(self):
        loader = ast.unparse(_function(self.tree, "_load_persisted_calibration"))
        # 失败路径必须 return（不覆盖 self.calibration），并记录状态
        self.assertIn("self.calibration_status", loader)
        self.assertIn("return", loader)

    def test_status_reports_which_calibration_is_in_use(self):
        publish = ast.unparse(_function(self.tree, "_publish"))
        for key in ("calibration_file", "calibration_status"):
            self.assertIn(key, publish, f"状态串必须上报 {key}（网页看到节点用的是哪份标定）")

    def test_status_reports_readiness_for_the_start_gate(self):
        """B4/R13：任务层的开赛门读的是这个字段，缺了它就等于门永远拦人（未知=拒绝）。"""
        publish = ast.unparse(_function(self.tree, "_publish"))
        self.assertIn("calibration_ready", publish)

    def test_readiness_is_true_on_both_success_paths(self):
        """两条「真的拿到可用标定」的路径（启动加载 / 面板推送）都必须置位。"""
        loader = ast.unparse(_function(self.tree, "_load_persisted_calibration"))
        self.assertIn("self.calibration_ready = True", loader)
        callback = ast.unparse(_function(self.tree, "_on_set_parameters"))
        self.assertIn("self.calibration_ready = True", callback)

    def test_readiness_starts_false_so_it_cannot_default_to_ready(self):
        """必须显式从 False 起步：默认值是「不可用」，不是「可用」。"""
        init = ast.unparse(_function(self.tree, "__init__"))
        self.assertIn("self.calibration_ready = False", init)


class CalibrationConfigBindingTests(unittest.TestCase):
    """面板写哪里、节点读哪里，必须是同一个路径——否则「落盘了却没人用」。"""

    CONFIG = PROJECT_ROOT / "ros2_ws/src/robogame_bringup/config/robot_field.yaml"

    def _field_value(self, param: str) -> str | None:
        text = self.CONFIG.read_text(encoding="utf-8")
        section = text.split("line_follow_controller:", 1)[1]
        for line in section.splitlines():
            stripped = line.split("#", 1)[0].strip()
            if stripped.startswith(param + ":"):
                return stripped.split(":", 1)[1].strip()
        return None

    def test_field_config_points_the_node_at_the_calibration_file(self):
        value = self._field_value("calibration_file")
        self.assertIsNotNone(value, "robot_field.yaml 必须给巡线节点设置 calibration_file")
        self.assertTrue(value)

    def test_panel_default_path_matches_the_node_path(self):
        from robogame_core.line_calibration import DEFAULT_CALIBRATION_FILE

        self.assertEqual(
            self._field_value("calibration_file"),
            DEFAULT_CALIBRATION_FILE,
            "网页面板默认落盘路径必须与节点加载路径一致（否则标定白标了）",
        )

    def test_node_parameter_default_is_empty_so_missing_config_is_loud(self):
        """节点参数默认空串 → 未配置时走「大声警告」分支，而不是悄悄用默认基准。"""
        text = LINE_NODE.read_text(encoding="utf-8")
        self.assertIn('"calibration_file": ""', text)


if __name__ == "__main__":
    unittest.main()
