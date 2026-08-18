"""C6（P2-1 半场边界 + P2-2 /cmd_vel 仲裁）单测。

覆盖（执行队列 C6）：
- P2-1：目标越本方半场 → 拒绝（规则 3.2.1 S4 异常处理）
- P2-2：/cmd_vel 多来源仲裁——急停最高、非授权忽略、授权者过期零速
"""
import unittest

from robogame_core.cmd_vel_arbiter import (
    SOURCE_ALIGN,
    SOURCE_MISSION,
    SOURCE_NAVIGATE,
    ArbiterConfig,
    CmdVelArbiter,
    arbitrate_cmd_vel,
)
from robogame_core.models import Pose2D, Velocity2D
from robogame_core.navigation import pose_in_own_half


class HalfFieldBoundaryTests(unittest.TestCase):
    """P2-1：半场边界（对方半场在 +x 侧，本方 x <= own_half_x_max）。"""

    def setUp(self):
        self.own_half_x_max = 3.5
        self.min_x, self.max_x = -0.2, 7.4
        self.min_y, self.max_y = -0.2, 5.0

    def test_pose_in_own_half_is_allowed(self):
        pose = Pose2D(2.0, 1.5, 0.0)
        self.assertTrue(pose_in_own_half(
            pose,
            own_half_x_max=self.own_half_x_max,
            min_x=self.min_x, min_y=self.min_y, max_y=self.max_y,
        ))

    def test_pose_across_center_is_rejected(self):
        # 目标 x=4.0 > 本方半场 3.5 → 越线，拒绝
        pose = Pose2D(4.0, 1.5, 0.0)
        self.assertFalse(pose_in_own_half(
            pose,
            own_half_x_max=self.own_half_x_max,
            min_x=self.min_x, min_y=self.min_y, max_y=self.max_y,
        ))

    def test_pose_on_boundary_is_allowed(self):
        # 恰好在本方半场边界 3.5 → 允许（边界含端点）
        pose = Pose2D(3.5, 1.5, 0.0)
        self.assertTrue(pose_in_own_half(
            pose,
            own_half_x_max=self.own_half_x_max,
            min_x=self.min_x, min_y=self.min_y, max_y=self.max_y,
        ))

    def test_pose_outside_field_y_is_rejected(self):
        pose = Pose2D(2.0, 5.5, 0.0)  # y 超场地边界
        self.assertFalse(pose_in_own_half(
            pose,
            own_half_x_max=self.own_half_x_max,
            min_x=self.min_x, min_y=self.min_y, max_y=self.max_y,
        ))

    def test_nonfinite_pose_is_rejected(self):
        pose = Pose2D(float("nan"), 1.5, 0.0)
        self.assertFalse(pose_in_own_half(
            pose,
            own_half_x_max=self.own_half_x_max,
            min_x=self.min_x, min_y=self.min_y, max_y=self.max_y,
        ))


class CmdVelArbiterTests(unittest.TestCase):
    """P2-2：/cmd_vel 多来源仲裁。"""

    def test_active_source_command_is_used(self):
        arbiter = CmdVelArbiter()
        arbiter.update(SOURCE_NAVIGATE, Velocity2D(0.3, 0.0, 0.0), now=1.0)
        command = arbiter.output(
            active_source=SOURCE_NAVIGATE, now=1.05, emergency_stop=False
        )
        self.assertEqual(command.vx, 0.3)

    def test_non_active_source_is_ignored(self):
        # 授权者是 NAVIGATE，但 ALIGN 也发了命令 → 忽略 ALIGN
        arbiter = CmdVelArbiter()
        arbiter.update(SOURCE_NAVIGATE, Velocity2D(0.3, 0.0, 0.0), now=1.0)
        arbiter.update(SOURCE_ALIGN, Velocity2D(0.0, 0.2, 0.0), now=1.0)
        command = arbiter.output(
            active_source=SOURCE_NAVIGATE, now=1.05, emergency_stop=False
        )
        self.assertEqual(command.vx, 0.3)
        self.assertEqual(command.vy, 0.0)  # ALIGN 的横向命令被忽略

    def test_emergency_stop_overrides_everything(self):
        arbiter = CmdVelArbiter()
        arbiter.update(SOURCE_NAVIGATE, Velocity2D(0.5, 0.0, 0.0), now=1.0)
        command = arbiter.output(
            active_source=SOURCE_NAVIGATE, now=1.05, emergency_stop=True
        )
        self.assertEqual(command, Velocity2D(0.0, 0.0, 0.0))

    def test_stale_active_source_outputs_zero(self):
        arbiter = CmdVelArbiter(ArbiterConfig(stale_s=0.5))
        arbiter.update(SOURCE_NAVIGATE, Velocity2D(0.3, 0.0, 0.0), now=1.0)
        # 1.6 - 1.0 = 0.6 > 0.5 → 过期 → 零速
        command = arbiter.output(
            active_source=SOURCE_NAVIGATE, now=1.6, emergency_stop=False
        )
        self.assertEqual(command, Velocity2D(0.0, 0.0, 0.0))

    def test_no_entry_for_active_source_outputs_zero(self):
        arbiter = CmdVelArbiter()
        command = arbiter.output(
            active_source=SOURCE_MISSION, now=1.0, emergency_stop=False
        )
        self.assertEqual(command, Velocity2D(0.0, 0.0, 0.0))

    def test_unknown_source_update_is_rejected(self):
        arbiter = CmdVelArbiter()
        with self.assertRaisesRegex(ValueError, "unknown cmd_vel source"):
            arbiter.update("hacker", Velocity2D(0.1, 0.0, 0.0), now=1.0)

    def test_unknown_active_source_is_rejected(self):
        arbiter = CmdVelArbiter()
        with self.assertRaisesRegex(ValueError, "unknown active source"):
            arbiter.output(active_source="hacker", now=1.0, emergency_stop=False)

    def test_pure_function_arbitrate(self):
        sources = {
            SOURCE_NAVIGATE: (Velocity2D(0.3, 0.0, 0.0), 1.0),
            SOURCE_ALIGN: (Velocity2D(0.0, 0.2, 0.0), 1.0),
        }
        command = arbitrate_cmd_vel(
            active_source=SOURCE_NAVIGATE, sources=sources,
            now=1.05, emergency_stop=False,
        )
        self.assertEqual(command.vx, 0.3)

    def test_negative_stale_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            ArbiterConfig(stale_s=-0.1)


if __name__ == "__main__":
    unittest.main()
