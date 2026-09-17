"""B3 路口转弯器的单元测试（纯逻辑，零 ROS、零硬件）。

转弯是「无人干预自主完赛」里最容易悄悄出错的一步：转早了冲出赛道、转晚了
在路口打转、转一半看到垂直的线就以为转完了。这些测试把每条判据单独钉住：

1. **不需要里程计**：主判据是巡线阵列（路口签名 → 线重新居中），
   航向确认默认关闭；
2. **最短转向时间**挡住「刚见路口就判完成」；
3. **稳定拍数**挡住单帧抖动；
4. **过期读数**一律停车，且过期不推进任何计数，久则响亮失败；
5. 参数与 B1 路线登记表一致（方向、稳定拍数同源）。
"""

from __future__ import annotations

import math
import unittest

from robogame_core.junction_turn import (
    JunctionTurner,
    TurnParams,
    TurnPhase,
    expected_turn_duration_s,
    turn_params_for,
    turn_params_for_direction,
)
from robogame_core.line_follow import LineSensorState
from robogame_core.mission_route import TurnDirection, build_route_plan
from robogame_core.route_loader import load_survey

ON = LineSensorState.ON_LINE
JUNCTION = LineSensorState.INTERSECTION


def _plan():
    return build_route_plan(load_survey())


def _params(direction=TurnDirection.RIGHT, **overrides) -> TurnParams:
    base = dict(
        turn_rate_radps=0.6,
        min_turn_s=0.2,
        max_turn_s=8.0,
        approach_timeout_s=10.0,
        junction_samples=2,
        reacquire_samples=3,
        center_tolerance=0.35,
    )
    base.update(overrides)
    return TurnParams(direction=direction, **base)


class ApproachingTests(unittest.TestCase):
    def test_crawls_forward_while_waiting_for_junction(self):
        turner = JunctionTurner(_params())
        out = turner.update(line_state=ON, deviation=0.0, now=0.0)
        self.assertIs(out.phase, TurnPhase.APPROACH)
        self.assertGreater(out.vx, 0.0)
        self.assertEqual(out.wz, 0.0)
        self.assertFalse(out.done)

    def test_single_junction_frame_is_not_enough(self):
        """单帧路口签名可能只是噪声：必须连续 junction_samples 拍。"""
        turner = JunctionTurner(_params(junction_samples=2))
        first = turner.update(line_state=JUNCTION, now=0.0)
        self.assertIs(first.phase, TurnPhase.APPROACH)
        self.assertEqual(first.wz, 0.0)
        second = turner.update(line_state=JUNCTION, now=0.02)
        self.assertIs(second.phase, TurnPhase.TURNING)
        self.assertNotEqual(second.wz, 0.0)

    def test_junction_counter_resets_on_normal_frame(self):
        turner = JunctionTurner(_params(junction_samples=2))
        turner.update(line_state=JUNCTION, now=0.0)
        turner.update(line_state=ON, deviation=0.0, now=0.02)
        out = turner.update(line_state=JUNCTION, now=0.04)
        self.assertIs(out.phase, TurnPhase.APPROACH, "中途夹了一帧正常线，计数必须清零")

    def test_stops_turning_never_happens_before_junction(self):
        turner = JunctionTurner(_params())
        for step in range(20):
            out = turner.update(line_state=LineSensorState.RIGHT_EDGE, deviation=0.9, now=step * 0.02)
            self.assertEqual(out.wz, 0.0)
            self.assertGreater(out.vx, 0.0)

    def test_approach_timeout_fails_loudly(self):
        turner = JunctionTurner(_params(approach_timeout_s=1.0))
        out = turner.update(line_state=ON, deviation=0.0, now=0.0)
        self.assertFalse(out.failed)
        out = turner.update(line_state=ON, deviation=0.0, now=1.5)
        self.assertTrue(out.failed)
        self.assertEqual((out.vx, out.wz), (0.0, 0.0))
        self.assertIn("路口", out.reason)


class TurningTests(unittest.TestCase):
    def _turn_to_turning(self, direction=TurnDirection.RIGHT, **overrides) -> JunctionTurner:
        turner = JunctionTurner(_params(direction, **overrides))
        turner.update(line_state=JUNCTION, now=0.0)
        turner.update(line_state=JUNCTION, now=0.02)
        self.assertIs(turner.phase, TurnPhase.TURNING)
        return turner

    def test_turn_signs_match_direction(self):
        right = self._turn_to_turning(TurnDirection.RIGHT)
        self.assertLess(right.update(line_state=ON, deviation=0.9, now=0.04).wz, 0.0)
        left = self._turn_to_turning(TurnDirection.LEFT)
        self.assertGreater(left.update(line_state=ON, deviation=0.9, now=0.04).wz, 0.0)

    def test_around_uses_the_configured_sign(self):
        left_around = self._turn_to_turning(TurnDirection.AROUND, around_sign=1)
        self.assertGreater(left_around.update(line_state=ON, deviation=0.9, now=0.04).wz, 0.0)
        right_around = self._turn_to_turning(TurnDirection.AROUND, around_sign=-1)
        self.assertLess(right_around.update(line_state=ON, deviation=0.9, now=0.04).wz, 0.0)

    def test_no_forward_motion_while_turning(self):
        turner = self._turn_to_turning()
        out = turner.update(line_state=ON, deviation=0.0, now=0.04)
        self.assertEqual(out.vx, 0.0, "原地转向不允许带前进速度")

    def test_min_turn_time_blocks_premature_completion(self):
        """T 形路口转向途中会短暂看到垂直的线——最短转向时间必须挡住它。"""
        turner = self._turn_to_turning(min_turn_s=0.5)
        out = turner.update(line_state=ON, deviation=0.0, now=0.10)
        self.assertIs(out.phase, TurnPhase.TURNING, "还没转够最短时间就判完成")
        self.assertNotEqual(out.wz, 0.0)

    def test_off_center_line_does_not_finish_the_turn(self):
        turner = self._turn_to_turning(min_turn_s=0.1, center_tolerance=0.35)
        out = turner.update(line_state=ON, deviation=0.9, now=1.0)
        self.assertIs(out.phase, TurnPhase.TURNING, "线还在边上，说明还没转到位")

    def test_completion_requires_settle_samples(self):
        turner = self._turn_to_turning(min_turn_s=0.1, reacquire_samples=3)
        first = turner.update(line_state=ON, deviation=0.05, now=1.0)
        self.assertIs(first.phase, TurnPhase.SETTLE)
        self.assertFalse(first.done)
        second = turner.update(line_state=ON, deviation=0.05, now=1.02)
        self.assertFalse(second.done)
        third = turner.update(line_state=ON, deviation=0.05, now=1.04)
        self.assertTrue(third.done)
        self.assertIs(third.phase, TurnPhase.DONE)
        self.assertEqual((third.vx, third.wz), (0.0, 0.0))

    def test_settle_samples_is_exactly_the_number_of_frames(self):
        """「稳定 N 拍」必须是恰好 N 拍（曾经 off-by-one 成 N+1）。"""
        for samples in (1, 2, 5):
            turner = self._turn_to_turning(min_turn_s=0.1, reacquire_samples=samples)
            clock = 1.0
            frames = 0
            done = False
            for _ in range(samples + 3):
                out = turner.update(line_state=ON, deviation=0.05, now=clock)
                clock += 0.02
                frames += 1
                if out.done:
                    done = True
                    break
            self.assertTrue(done, f"reacquire_samples={samples} 未在合理拍数内完成")
            self.assertEqual(frames, samples, f"reacquire_samples={samples} 实际用了 {frames} 拍")

    def test_settle_falls_back_to_turning_when_line_drifts(self):
        turner = self._turn_to_turning(min_turn_s=0.1, reacquire_samples=3)
        turner.update(line_state=ON, deviation=0.05, now=1.0)
        drifted = turner.update(line_state=LineSensorState.RIGHT_EDGE, deviation=0.9, now=1.02)
        self.assertIs(drifted.phase, TurnPhase.TURNING)
        self.assertNotEqual(drifted.wz, 0.0)
        self.assertEqual(turner.reacquire_count, 0)

    def test_turn_timeout_fails_and_stops(self):
        turner = self._turn_to_turning(max_turn_s=0.5)
        out = turner.update(line_state=LineSensorState.LOST, now=1.0)
        self.assertTrue(out.failed)
        self.assertIs(out.phase, TurnPhase.FAILED)
        self.assertEqual((out.vx, out.wz), (0.0, 0.0))
        self.assertIn("超时", out.reason)

    def test_finished_turner_keeps_commanding_zero(self):
        turner = self._turn_to_turning(min_turn_s=0.1, reacquire_samples=1)
        done = turner.update(line_state=ON, deviation=0.0, now=1.0)
        self.assertTrue(done.done)
        again = turner.update(line_state=ON, deviation=0.0, now=1.02)
        self.assertTrue(again.done)
        self.assertEqual((again.vx, again.wz), (0.0, 0.0))

    def test_reset_starts_over(self):
        turner = self._turn_to_turning()
        turner.reset()
        self.assertIs(turner.phase, TurnPhase.APPROACH)
        self.assertEqual(turner.junction_count, 0)
        out = turner.update(line_state=ON, deviation=0.0, now=5.0)
        self.assertIs(out.phase, TurnPhase.APPROACH)

    def test_turn_elapsed_is_reported(self):
        turner = self._turn_to_turning()
        turner.update(line_state=ON, deviation=0.9, now=0.52)
        self.assertAlmostEqual(turner.turn_elapsed_s, 0.5, places=6)


class StaleReadingTests(unittest.TestCase):
    def test_stale_readings_hold_and_do_not_advance(self):
        turner = JunctionTurner(_params(junction_samples=2, max_stale_s=1.0))
        out = turner.update(line_state=JUNCTION, now=0.0, stale=True)
        self.assertEqual((out.vx, out.wz), (0.0, 0.0))
        self.assertFalse(out.done)
        self.assertEqual(turner.junction_count, 0, "过期读数不得推进任何计数")

    def test_long_stale_reading_fails(self):
        turner = JunctionTurner(_params(max_stale_s=0.5))
        turner.update(line_state=ON, now=0.0, stale=True)
        out = turner.update(line_state=ON, now=1.0, stale=True)
        self.assertTrue(out.failed)
        self.assertIn("过期", out.reason)


class YawCheckTests(unittest.TestCase):
    def _turner(self, start_yaw=None, **overrides) -> JunctionTurner:
        turner = JunctionTurner(_params(min_turn_s=0.1, reacquire_samples=1, **overrides))
        turner.update(line_state=JUNCTION, now=0.0)
        # 进入转向的那一拍给的航向会被当作基线
        turner.update(line_state=JUNCTION, now=0.02, yaw=start_yaw)
        return turner

    def test_yaw_check_is_off_by_default(self):
        """默认不依赖航向：标定完成前它不可信。"""
        turner = self._turner()
        out = turner.update(line_state=ON, deviation=0.0, now=1.0)
        self.assertTrue(out.done, "默认不该因为缺航向而拒绝完成")

    def test_yaw_check_blocks_until_enough_rotation(self):
        turner = self._turner(start_yaw=math.pi / 2, use_yaw_check=True, yaw_fraction=0.8)
        # 只转了 20°（需要 ≥72°）
        out = turner.update(line_state=ON, deviation=0.0, now=1.0, yaw=math.pi / 2 - math.radians(20))
        self.assertIs(out.phase, TurnPhase.TURNING)
        # 转了 85°
        out = turner.update(line_state=ON, deviation=0.0, now=1.02, yaw=math.pi / 2 - math.radians(85))
        self.assertTrue(out.done)

    def test_late_yaw_baseline_is_adopted_instead_of_failing_forever(self):
        """转向开始时没有航向（IMU 刚有效）：必须补采基线，不能白转到超时。"""
        turner = self._turner(use_yaw_check=True, yaw_fraction=0.8)  # 基线为 None
        turner.update(line_state=LineSensorState.LOST, now=0.5)  # 途中仍无航向
        out = turner.update(line_state=ON, deviation=0.0, now=1.0, yaw=2.0)
        self.assertFalse(out.done, "补采基线的这一拍 Δ=0，还不该完成")
        out = turner.update(line_state=ON, deviation=0.0, now=1.02, yaw=2.0 - math.radians(85))
        self.assertTrue(out.done)

    def test_yaw_check_without_any_yaw_never_completes(self):
        """开了航向确认但整段都没有航向 → 不会误判完成（会走到超时失败）。"""
        turner = self._turner(use_yaw_check=True, max_turn_s=1.0)
        out = turner.update(line_state=ON, deviation=0.0, now=2.0)
        self.assertFalse(out.done)
        self.assertTrue(out.failed)
        self.assertIn("超时", out.reason)

    def test_yaw_check_handles_wraparound(self):
        turner = self._turner(start_yaw=3.0, use_yaw_check=True, yaw_fraction=0.8)
        # 从 3.0 rad 起多转 1.4 rad（约 80°），跨过 ±π：归一化后是 -1.883 rad
        end_yaw = 3.0 + 1.4 - 2.0 * math.pi
        out = turner.update(line_state=ON, deviation=0.0, now=1.0, yaw=end_yaw)
        self.assertTrue(out.done, "±π 环绕时的转向角计算错了")


class ParameterTests(unittest.TestCase):
    def test_target_angle_per_direction(self):
        self.assertAlmostEqual(_params(TurnDirection.RIGHT).target_angle_rad, math.pi / 2)
        self.assertAlmostEqual(_params(TurnDirection.LEFT).target_angle_rad, math.pi / 2)
        self.assertAlmostEqual(_params(TurnDirection.AROUND).target_angle_rad, math.pi)

    def test_straight_direction_is_rejected(self):
        with self.assertRaises(ValueError):
            _params(TurnDirection.STRAIGHT)

    def test_invalid_values_are_rejected(self):
        with self.assertRaises(ValueError):
            _params(turn_rate_radps=-1.0)
        with self.assertRaises(ValueError):
            _params(min_turn_s=5.0, max_turn_s=1.0)
        with self.assertRaises(ValueError):
            _params(center_tolerance=0.0)
        with self.assertRaises(ValueError):
            _params(junction_samples=0)
        with self.assertRaises(ValueError):
            _params(around_sign=0)
        with self.assertRaises(ValueError):
            _params(yaw_fraction=1.5)
        with self.assertRaises(ValueError):
            _params(expect_states=())

    def test_expected_duration_is_a_usable_estimate(self):
        self.assertAlmostEqual(expected_turn_duration_s(_params(turn_rate_radps=1.0)), math.pi / 2)
        around = _params(TurnDirection.AROUND, turn_rate_radps=1.0)
        self.assertAlmostEqual(expected_turn_duration_s(around), math.pi)

    def test_around_needs_longer_minimum_turn_time_than_right_angle(self):
        right = turn_params_for_direction(TurnDirection.RIGHT, turn_rate_radps=0.6)
        around = turn_params_for_direction(TurnDirection.AROUND, turn_rate_radps=0.6)
        self.assertLess(right.min_turn_s, around.min_turn_s)

    def test_overrides_win(self):
        params = turn_params_for_direction(
            TurnDirection.RIGHT, reacquire_samples=5, turn_rate_radps=1.2, min_turn_s=0.9
        )
        self.assertEqual(params.reacquire_samples, 5)
        self.assertAlmostEqual(params.turn_rate_radps, 1.2)
        self.assertAlmostEqual(params.min_turn_s, 0.9)


class RouteBindingTests(unittest.TestCase):
    """转弯参数必须与 B1 路线登记表同源（改路线，转弯跟着变）。"""

    def test_parameters_follow_the_route_registry(self):
        plan = _plan()
        turns = plan.turn_registry()
        self.assertEqual(len(turns), 2)
        for spec in turns:
            params = turn_params_for(spec)
            self.assertIs(params.direction, spec.direction)
            self.assertEqual(
                params.reacquire_samples, spec.settle_samples,
                f"{spec.segment_id}: 稳定拍数必须取自路线登记表",
            )
            self.assertEqual(params.expect_states, tuple(plan.segment(spec.segment_id).exit.expected_line_states))

    def test_registered_turn_directions_are_left_and_right_not_around(self):
        """路线登记表里的两个路口是左转/右转；掉头（W02）由 TURN 段单独表达。"""
        directions = {spec.direction for spec in _plan().turn_registry()}
        self.assertEqual(directions, {TurnDirection.LEFT, TurnDirection.RIGHT})

    def test_around_parameters_are_available_for_the_turn_segment(self):
        plan = _plan()
        turn_segment = plan.segment("S07_TURN_AROUND")
        self.assertEqual(turn_segment.exit.kind.value, "YAW_TARGET")
        params = turn_params_for_direction(TurnDirection.AROUND, turn_rate_radps=0.6)
        self.assertAlmostEqual(params.target_angle_rad, math.pi)
        # 掉头的理论耗时 ≈ π/0.6 ≈ 5.2s，必须小于转弯超时上限才可能成功
        self.assertLess(expected_turn_duration_s(params), params.max_turn_s)

    def test_simulated_right_turn_at_n02_completes(self):
        """按路线登记表的 N02 右转，走一遍完整时序应能完成。"""
        spec = next(s for s in _plan().turn_registry() if s.ref == "N02")
        params = turn_params_for(spec, turn_rate_radps=1.0)
        turner = JunctionTurner(params)
        clock = 0.0
        # 沿 E01 爬行到路口
        for _ in range(5):
            out = turner.update(line_state=ON, deviation=0.0, now=clock)
            clock += 0.02
        self.assertGreater(out.vx, 0.0)
        # 路口签名（连续 2 拍）→ 进入原地转向
        turner.update(line_state=JUNCTION, now=clock); clock += 0.02
        out = turner.update(line_state=JUNCTION, now=clock); clock += 0.02
        self.assertIs(out.phase, TurnPhase.TURNING)
        # 转够最短转向时间（用 LOST 模拟转向中看不到线）
        while clock < params.min_turn_s + 0.10:
            turner.update(line_state=LineSensorState.LOST, now=clock); clock += 0.02
        self.assertIs(turner.phase, TurnPhase.TURNING, "最短转向时间内不许判完成")
        # 线回到正下方并稳定 spec.settle_samples 拍 → 完成
        done = False
        for _ in range(40):
            out = turner.update(line_state=ON, deviation=0.1, now=clock); clock += 0.02
            if out.done:
                done = True
                break
        self.assertTrue(done, f"右转未完成：{out.reason}")
        self.assertLessEqual(clock, params.max_turn_s, "不应靠超时结束")


if __name__ == "__main__":
    unittest.main()
