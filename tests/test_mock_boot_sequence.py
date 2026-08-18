"""A0.1 忠实假固件 + A0.7 限幅拒绝：mock 必须模拟真实固件的拒绝行为。

背景（执行队列 2026-08-18 A0 节）：
- A0.1：真实 STM32 上电要先握手（HELLO->ACK）再解码首条 0x12 STATUS 才把
  communication_ok 置 True；旧 mock 一上来就 True，P0-1 路径从未被测到。
- A0.7：真实固件在限幅未就绪时拒绝 enable=1 的非零速度命令（ISSUE-021，
  rpi_protocol.c 的 rpi_limits_ready=0 拒绝），且超限幅触发协议故障锁存
  （2026-08-18 电控确认）。旧 mock 从不拒绝，限幅路径从未被测到。

本测试验证：
1. A0.1 纯函数 `mock_communication_ok` 上电窗口判定；
2. A0.7 纯函数 `mock_velocity_limits_ready` 限幅拒绝语义（非零拒绝/零速允许）；
3. robot_bridge 源码确实接入两个函数（AST 结构断言）。
"""
import ast
import math
import unittest
from pathlib import Path

from robogame_core.hardware_readiness import (
    mock_communication_ok,
    mock_velocity_limits_ready,
)

NODE_PATH = (
    Path(__file__).parents[1]
    / "ros2_ws"
    / "src"
    / "robot_bridge"
    / "robot_bridge"
    / "node.py"
)


class MockCommunicationOkTests(unittest.TestCase):
    """纯函数：上电窗口内 False，窗口后 True，边界与非法输入。"""

    def test_zero_window_is_immediately_healthy(self):
        # 兼容旧行为：默认 0.0 秒 = 上电即 True。
        self.assertTrue(mock_communication_ok(
            boot_started_s=100.0, now_s=100.0, ready_after_s=0.0
        ))

    def test_false_during_boot_window(self):
        # 真实上电时序：窗口内（握手未完成）communication_ok 必须为 False。
        self.assertFalse(mock_communication_ok(
            boot_started_s=100.0, now_s=100.5, ready_after_s=2.0
        ))

    def test_true_after_boot_window(self):
        # 窗口过后（握手完成 + 首条 STATUS 解码）才置 True。
        self.assertTrue(mock_communication_ok(
            boot_started_s=100.0, now_s=102.5, ready_after_s=2.0
        ))

    def test_exact_window_boundary_is_healthy(self):
        # 边界：恰好等于窗口时长算就绪（与 receive_timestamp_is_fresh 的
        # math.isclose 边界语义一致）。
        self.assertTrue(mock_communication_ok(
            boot_started_s=100.0, now_s=102.0, ready_after_s=2.0
        ))

    def test_just_before_boundary_is_not_healthy(self):
        self.assertFalse(mock_communication_ok(
            boot_started_s=100.0, now_s=101.999999, ready_after_s=2.0
        ))

    def test_negative_window_rejected(self):
        with self.assertRaises(ValueError):
            mock_communication_ok(
                boot_started_s=100.0, now_s=100.0, ready_after_s=-1.0
            )

    def test_nan_window_rejected(self):
        with self.assertRaises(ValueError):
            mock_communication_ok(
                boot_started_s=100.0, now_s=100.0, ready_after_s=math.nan
            )

    def test_nonfinite_timestamps_rejected(self):
        with self.assertRaises(ValueError):
            mock_communication_ok(
                boot_started_s=math.inf, now_s=100.0, ready_after_s=1.0
            )
        with self.assertRaises(ValueError):
            mock_communication_ok(
                boot_started_s=100.0, now_s=math.nan, ready_after_s=1.0
            )

    def test_now_before_boot_rejected(self):
        with self.assertRaises(ValueError):
            mock_communication_ok(
                boot_started_s=100.0, now_s=99.0, ready_after_s=1.0
            )


class MockFirmwareStructureTests(unittest.TestCase):
    """AST 结构断言：mock 分支必须走忠实上电时序，boot_id 必须可配置。"""

    @classmethod
    def setUpClass(cls):
        cls.source = NODE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_mock_comm_ok_uses_faithful_boot_sequence(self):
        # mock 分支必须调用 mock_communication_ok（不能无条件 True）。
        self.assertIn("mock_communication_ok(", self.source)
        # 且必须在 mock 分支内（紧随 mock_mode 判断）。
        self.assertIn("if self.mock_mode:", self.source)
        tick = self._method_source("_tick")
        self.assertIn("mock_communication_ok(", tick)

    def test_mock_handshake_waits_for_boot_window(self):
        # mock 不再无条件 READY：窗口 > 0 时先 HANDSHAKING。
        self.assertIn("_HANDSHAKE_READY\n            if self.mock_mode and self.mock_comm_ok_after_s <= 0.0", self.source)
        self.assertIn(
            "if communication_ok and self._handshake_state != _HANDSHAKE_READY:",
            self._method_source("_tick"),
        )

    def test_mock_boot_id_is_configurable(self):
        # mock 的 boot_id 由参数提供，不再是硬编码 0。
        self.assertIn('self.declare_parameter("mock_boot_id", 0)', self.source)
        self.assertIn("self.mock_boot_id", self.source)
        self.assertIn("status.boot_id = self.mock_boot_id", self.source)

    def test_legacy_lenient_default_is_preserved(self):
        # 默认窗口 0.0：旧 mock（立即 READY、立即 communication_ok=True）不变。
        self.assertIn('self.declare_parameter("mock_comm_ok_after_s", 0.0)', self.source)

    def _method_source(self, name):
        for node in ast.walk(self.tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                return ast.get_source_segment(self.source, node)
        self.fail(f"method {name} not found")


class MockVelocityLimitsTests(unittest.TestCase):
    """A0.7：纯函数 `mock_velocity_limits_ready` 的限幅拒绝语义。"""

    def test_limits_ready_accepts_nonzero(self):
        # 限幅就绪：非零速度允许（正常行驶）。
        self.assertTrue(mock_velocity_limits_ready(
            limits_ready=True, vx=0.3, vy=0.0, wz=0.0
        ))

    def test_limits_not_ready_rejects_nonzero(self):
        # 限幅未就绪（ISSUE-021 场景）：非零速度必须拒绝。
        self.assertFalse(mock_velocity_limits_ready(
            limits_ready=False, vx=0.3, vy=0.0, wz=0.0
        ))
        self.assertFalse(mock_velocity_limits_ready(
            limits_ready=False, vx=0.0, vy=0.2, wz=0.0
        ))
        self.assertFalse(mock_velocity_limits_ready(
            limits_ready=False, vx=0.0, vy=0.0, wz=0.5
        ))

    def test_limits_not_ready_accepts_zero(self):
        # 限幅未就绪但命令是零速（停车）：永远允许（对应真实 enable=0）。
        self.assertTrue(mock_velocity_limits_ready(
            limits_ready=False, vx=0.0, vy=0.0, wz=0.0
        ))

    def test_default_is_lenient(self):
        # 兼容旧行为：默认 limits_ready=True（现有测试/mock_demo 不变）。
        self.assertTrue(mock_velocity_limits_ready(
            limits_ready=True, vx=0.5, vy=0.5, wz=1.0
        ))

    def test_nonfinite_velocity_rejected(self):
        with self.assertRaises(ValueError):
            mock_velocity_limits_ready(
                limits_ready=True, vx=math.nan, vy=0.0, wz=0.0
            )


class MockVelocityLimitsStructureTests(unittest.TestCase):
    """A0.7：AST 结构断言——mock 分支必须接入限幅拒绝。"""

    @classmethod
    def setUpClass(cls):
        cls.source = NODE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_mock_cmd_vel_gates_on_limits_ready(self):
        # mock 分支必须在 _on_cmd_vel 里调用 mock_velocity_limits_ready。
        self.assertIn("mock_velocity_limits_ready(", self.source)
        on_cmd_vel = self._method_source("_on_cmd_vel")
        self.assertIn("mock_velocity_limits_ready(", on_cmd_vel)

    def test_mock_limits_param_is_configurable(self):
        self.assertIn(
            'self.declare_parameter("mock_velocity_limits_ready", True)',
            self.source,
        )
        self.assertIn("self.mock_velocity_limits_ready", self.source)

    def test_real_mode_does_not_gate_on_mock_limits(self):
        # 限幅门控只应在 mock 分支（真实模式由固件管）。
        on_cmd_vel = self._method_source("_on_cmd_vel")
        self.assertIn("if self.mock_mode and not mock_velocity_limits_ready(", on_cmd_vel)

    def _method_source(self, name):
        for node in ast.walk(self.tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                return ast.get_source_segment(self.source, node)
        self.fail(f"method {name} not found")


if __name__ == "__main__":
    unittest.main()
