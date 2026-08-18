"""C1 路段链模型单测。

覆盖（执行队列 C1 要求）：段链推进、异常段退化、与现有 GoToPoseController
兼容。纯算法测试，零 ROS 依赖。
"""
import unittest

from robogame_core.models import Pose2D, Velocity2D
from robogame_core.navigation import GoToPoseController, ControllerConfig
from robogame_core.route_segment import (
    ChainStatus,
    RouteChain,
    RouteSegment,
    SegmentKind,
)


def chain_with_exit_checks(exit_conditions):
    """构造一段一个退出条件的链（退出条件按顺序消费）。"""
    segments = []
    for index, exit_check in enumerate(exit_conditions):
        segments.append(RouteSegment(
            kind=SegmentKind.WAYPOINT,
            exit_check=exit_check,
            label=f"seg{index}",
        ))
    return RouteChain(segments)


class RouteChainProgressionTests(unittest.TestCase):
    def test_start_enters_first_segment(self):
        chain = RouteChain([RouteSegment(label="a")])
        self.assertEqual(chain.status, ChainStatus.NOT_STARTED)
        chain.start()
        self.assertEqual(chain.status, ChainStatus.RUNNING)
        self.assertEqual(chain.current_index, 0)
        self.assertEqual(chain.current.label, "a")

    def test_exit_check_gates_advance_to_next_segment(self):
        seg0_done = [False]
        seg1_done = [False]
        chain = chain_with_exit_checks([
            lambda: seg0_done[0],
            lambda: seg1_done[0],
        ])
        chain.start()
        self.assertFalse(chain.tick())  # seg0 未完成，不推进
        self.assertEqual(chain.current_index, 0)
        seg0_done[0] = True
        self.assertTrue(chain.tick())  # seg0 完成 -> 切到 seg1
        self.assertEqual(chain.current_index, 1)
        self.assertFalse(chain.tick())  # seg1 未完成
        seg1_done[0] = True
        self.assertTrue(chain.tick())  # seg1 完成 -> 链 COMPLETE
        self.assertEqual(chain.status, ChainStatus.COMPLETE)

    def test_advance_through_all_segments_then_complete(self):
        flags = [False, False, False]
        chain = chain_with_exit_checks([lambda: flags[0], lambda: flags[1], lambda: flags[2]])
        chain.start()
        self.assertEqual(chain.current_index, 0)
        flags[0] = True
        self.assertTrue(chain.tick())
        self.assertEqual(chain.current_index, 1)
        flags[1] = True
        self.assertTrue(chain.tick())
        self.assertEqual(chain.current_index, 2)
        flags[2] = True
        self.assertTrue(chain.tick())
        self.assertEqual(chain.status, ChainStatus.COMPLETE)
        self.assertTrue(chain.is_complete)
        self.assertIsNone(chain.current)

    def test_tick_after_complete_is_noop(self):
        chain = RouteChain([RouteSegment(exit_check=lambda: True)])
        chain.start()
        chain.tick()
        self.assertTrue(chain.is_complete)
        self.assertFalse(chain.tick())

    def test_empty_chain_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            RouteChain([])

    def test_start_twice_is_rejected(self):
        chain = RouteChain([RouteSegment()])
        chain.start()
        with self.assertRaisesRegex(ValueError, "once"):
            chain.start()


class RouteSegmentSpeedTests(unittest.TestCase):
    def test_segment_speed_overrides_default(self):
        chain = RouteChain(
            [RouteSegment(max_speed_mps=0.3, label="slow")],
            default_speed_mps=0.6,
        )
        chain.start()
        self.assertEqual(chain.current_speed, 0.3)

    def test_zero_segment_speed_uses_default(self):
        chain = RouteChain([RouteSegment()], default_speed_mps=0.6)
        chain.start()
        self.assertEqual(chain.current_speed, 0.6)

    def test_speed_limit_follows_current_segment(self):
        chain = RouteChain([
            RouteSegment(max_speed_mps=0.2, exit_check=lambda: False, label="a"),
            RouteSegment(max_speed_mps=0.5, label="b"),
        ])
        chain.start()
        self.assertEqual(chain.current_speed, 0.2)
        self.assertEqual(chain.current_yaw_limit, chain.default_yaw_radps)

    def test_negative_speed_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            RouteSegment(max_speed_mps=-0.1)


class RouteChainDegradationTests(unittest.TestCase):
    def test_degrade_keeps_chain_running_and_records_reason(self):
        chain = RouteChain([RouteSegment(label="a"), RouteSegment(label="b")])
        chain.start()
        chain.degrade("line lost")
        self.assertEqual(chain.status, ChainStatus.RUNNING)
        self.assertIn("line lost", chain.detail)
        self.assertEqual(chain.current_index, 0)

    def test_fail_marks_chain_failed(self):
        chain = RouteChain([RouteSegment(label="a")])
        chain.start()
        chain.fail("localization lost")
        self.assertEqual(chain.status, ChainStatus.FAILED)
        self.assertTrue(chain.failed)
        self.assertIn("localization lost", chain.detail)

    def test_reset_restores_not_started(self):
        chain = RouteChain([RouteSegment(label="a"), RouteSegment(label="b")])
        chain.start()
        chain.fail("boom")
        chain.reset()
        self.assertEqual(chain.status, ChainStatus.NOT_STARTED)
        self.assertEqual(chain.current_index, -1)
        self.assertEqual(chain.detail, "")
        chain.start()
        self.assertEqual(chain.current_index, 0)


class RouteChainWithGoToPoseCompatibilityTests(unittest.TestCase):
    """RouteChain 的 WAYPOINT 段与现有 GoToPoseController 协作（不替换它）。"""

    def test_waypoint_segment_feeds_controller_target(self):
        chain = RouteChain([RouteSegment(
            kind=SegmentKind.WAYPOINT, label="到材料区"
        )])
        controller = GoToPoseController(ControllerConfig())
        chain.start()
        pose = Pose2D(0.0, 0.0, 0.0)
        target = Pose2D(1.0, 0.0, 0.0)
        command = controller.command(pose, target)
        self.assertIsInstance(command, Velocity2D)
        self.assertGreater(command.vx, 0.0)
        self.assertEqual(chain.current_speed, chain.default_speed_mps)

    def test_chain_limits_can_clamp_controller_command(self):
        chain = RouteChain([
            RouteSegment(kind=SegmentKind.RAMP_UP, max_speed_mps=0.1, label="上坡")
        ])
        chain.start()
        self.assertEqual(chain.current_speed, 0.1)
        # C3 将用 current_speed 对控制器输出做二次限幅；这里验证限速值可取到。
        self.assertLessEqual(chain.current_speed, 0.1)

    def test_all_segment_kinds_are_representable(self):
        kinds = {kind for kind in SegmentKind}
        self.assertEqual(
            kinds,
            {SegmentKind.WAYPOINT, SegmentKind.LINE_FOLLOW,
             SegmentKind.RAMP_UP, SegmentKind.RAMP_DOWN},
        )


if __name__ == "__main__":
    unittest.main()
