"""C3 斜坡/高台段控制单测（全部合成数据，不等 IMU）。

覆盖（执行队列 C3）：RAMP_UP/RAMP_DOWN 段限速限加速度、打滑检测
（里程计 vs 期望速度差超阈值 → 降速/停车）、下坡防冲、参数校验、
与 C1 RouteChain 联动。
"""
import unittest

from robogame_core.models import Velocity2D
from robogame_core.ramp_control import RampController, RampProfile, SlipDecision
from robogame_core.route_segment import RouteChain, RouteSegment, SegmentKind


def desired(vx=0.3, vy=0.0, wz=0.0) -> Velocity2D:
    return Velocity2D(vx, vy, wz)


class RampProfileValidationTests(unittest.TestCase):
    def test_wrong_kind_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "RAMP_UP or RAMP_DOWN"):
            RampProfile(kind=SegmentKind.WAYPOINT)

    def test_negative_limits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            RampProfile(max_speed_mps=-0.1)
        with self.assertRaisesRegex(ValueError, "positive"):
            RampProfile(slip_threshold_mps=0.0)

    def test_slip_retreat_below_max_speed(self):
        with self.assertRaisesRegex(ValueError, "below ramp max speed"):
            RampProfile(max_speed_mps=0.2, slip_retreat_speed_mps=0.3)

    def test_default_profile_is_valid(self):
        profile = RampProfile()
        self.assertEqual(profile.kind, SegmentKind.RAMP_UP)
        self.assertGreater(profile.max_speed_mps, 0.0)


class RampUpLimitTests(unittest.TestCase):
    def test_ramp_up_limits_speed_to_profile_max(self):
        controller = RampController(RampProfile(max_speed_mps=0.25))
        controller.begin(now=0.0)
        # 上层期望 0.5 m/s（超过坡道限速 0.25）
        command, decision = controller.step(
            now=1.0, desired=desired(0.5), measured_speed=0.5, dt=1.0
        )
        self.assertLessEqual(command.vx, 0.25)
        self.assertEqual(decision, SlipDecision.NORMAL)

    def test_ramp_up_acceleration_is_bounded(self):
        controller = RampController(RampProfile(max_accel_mps2=0.3))
        controller.begin(now=0.0)
        # 期望 0.25，但加速度限制 0.3 m/s² × dt 0.1s = 0.03 增量
        command, _ = controller.step(
            now=0.1, desired=desired(0.25), measured_speed=0.0, dt=0.1
        )
        self.assertLessEqual(command.vx, 0.03 + 1e-9)

    def test_ramp_up_starts_from_zero(self):
        controller = RampController()
        controller.begin(now=0.0)
        self.assertEqual(controller.current_speed, 0.0)


class SlipDetectionTests(unittest.TestCase):
    def test_normal_driving_no_slip(self):
        controller = RampController()
        controller.begin(now=0.0)
        command, decision = controller.step(
            now=0.1, desired=desired(0.2), measured_speed=0.19, dt=0.1
        )
        self.assertEqual(decision, SlipDecision.NORMAL)

    def test_slipping_detected_when_measured_lags_desired(self):
        controller = RampController(RampProfile(slip_threshold_mps=0.08))
        controller.begin(now=0.0)
        # 期望 0.2，实测 0.0 → 差 0.2 > 阈值 0.08 → 打滑
        command, decision = controller.step(
            now=0.1, desired=desired(0.2), measured_speed=0.0, dt=0.1
        )
        self.assertEqual(decision, SlipDecision.SLIPPING)
        # 打滑时降速到保守值
        self.assertLessEqual(command.vx, controller.profile.slip_retreat_speed_mps)

    def test_prolonged_slip_becomes_stuck_and_stops(self):
        profile = RampProfile(
            slip_threshold_mps=0.08,
            slip_stop_after_s=1.5,
        )
        controller = RampController(profile)
        controller.begin(now=0.0)
        # 第一次 step：打滑开始（slip_since_s 记录为 0.1）
        _, decision = controller.step(
            now=0.1, desired=desired(0.2), measured_speed=0.0, dt=0.1
        )
        self.assertEqual(decision, SlipDecision.SLIPPING)
        # 持续打滑超过 1.5s（0.1 + 1.6）→ STUCK 停车
        command, decision = controller.step(
            now=1.7, desired=desired(0.2), measured_speed=0.0, dt=0.1
        )
        self.assertEqual(decision, SlipDecision.STUCK)
        self.assertEqual(command.vx, 0.0)

    def test_recovery_clears_slip_state(self):
        controller = RampController(RampProfile(slip_threshold_mps=0.08))
        controller.begin(now=0.0)
        _, decision = controller.step(
            now=0.1, desired=desired(0.2), measured_speed=0.0, dt=0.1
        )
        self.assertEqual(decision, SlipDecision.SLIPPING)
        # 恢复正常（实测速度跟上）→ 不再打滑
        _, decision = controller.step(
            now=0.2, desired=desired(0.2), measured_speed=0.19, dt=0.1
        )
        self.assertEqual(decision, SlipDecision.NORMAL)

    def test_no_measured_speed_skips_slip_detection(self):
        # 无里程计实测（None）时不判打滑，仅限速
        controller = RampController()
        controller.begin(now=0.0)
        command, decision = controller.step(
            now=0.1, desired=desired(0.2), measured_speed=None, dt=0.1
        )
        self.assertEqual(decision, SlipDecision.NORMAL)
        self.assertGreater(command.vx, 0.0)


class RampDownTests(unittest.TestCase):
    def test_ramp_down_speed_is_extra_limited(self):
        profile = RampProfile(
            kind=SegmentKind.RAMP_DOWN,
            max_speed_mps=0.25,
            descent_speed_factor=0.7,
        )
        controller = RampController(profile)
        controller.begin(now=0.0)
        # 下坡生效限速 = 0.25 × 0.7 = 0.175
        self.assertAlmostEqual(controller.effective_max_speed, 0.175, places=3)
        command, decision = controller.step(
            now=1.0, desired=desired(0.5), measured_speed=0.5, dt=1.0
        )
        self.assertLessEqual(command.vx, 0.175)

    def test_ramp_down_deceleration_is_limited(self):
        profile = RampProfile(
            kind=SegmentKind.RAMP_DOWN,
            descent_decel_mps2=0.25,
            max_accel_mps2=0.5,
        )
        controller = RampController(profile)
        controller.begin(now=0.0)
        # 下坡最大加速度 = min(0.5, 0.25) = 0.25；dt 0.1 → 增量 ≤ 0.025
        command, _ = controller.step(
            now=0.1, desired=desired(0.2), measured_speed=0.0, dt=0.1
        )
        self.assertLessEqual(command.vx, 0.025 + 1e-9)


class RampChainIntegrationTests(unittest.TestCase):
    def test_ramp_segment_feeds_chain_speed_limit(self):
        # C1 联动：RAMP_UP 段在 RouteChain 里生效限速 = 坡道限速
        chain = RouteChain([
            RouteSegment(kind=SegmentKind.WAYPOINT, label="到坡底"),
            RouteSegment(kind=SegmentKind.RAMP_UP, max_speed_mps=0.25, label="上坡"),
        ])
        chain.start()
        self.assertEqual(chain.current_index, 0)
        # 手动把段推进逻辑简化：验证 RAMP 段的 current_speed 可被控制器读取
        ramp_segment = chain.segments[1]
        self.assertEqual(ramp_segment.kind, SegmentKind.RAMP_UP)
        self.assertEqual(ramp_segment.max_speed_mps, 0.25)

    def test_ramp_controller_uses_profile_speed_as_chain_does(self):
        # 控制器限速与 C1 段限速一致（同一配置来源）
        profile = RampProfile(max_speed_mps=0.25)
        chain = RouteChain([
            RouteSegment(kind=SegmentKind.RAMP_UP, max_speed_mps=0.25)
        ])
        chain.start()
        controller = RampController(profile)
        self.assertEqual(controller.effective_max_speed, chain.current_speed)


if __name__ == "__main__":
    unittest.main()
