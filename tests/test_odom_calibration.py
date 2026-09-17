"""里程计标定的纯逻辑测试（零 ROS、零硬件）。

重点是三个问题不能混为一谈：

1. **开环**：命令速度×时间 ≈ 实际位移吗（用户已初步验证的那条）；
2. **自洽**：里程计等效速度 = 里程计报的位移 ÷ 耗时，和命令速度对得上吗
   —— 这条**不用尺子**就能看出里程计被放大/缩小了多少倍；
3. **标度**：尺量位移 ÷ 里程计位移。

结论的诚实等级也要钉住：样本不足不说结论、波动大不给标度、整数比先怀疑固件常量。
"""

from __future__ import annotations

import math
import unittest

from tools.odom_calibration import (
    INCONSISTENCY_RATIO,
    MAX_RELATIVE_SPREAD,
    MIN_SAMPLES_FOR_SCALE,
    OdomTrial,
    analyze_trials,
    trial_from_result,
)


def _trial(
    odom_m=0.5, elapsed_s=10.5, speed=0.05, measured=None, target=0.5, completed=True, note=""
) -> OdomTrial:
    return OdomTrial(
        target_m=target,
        odom_m=odom_m,
        elapsed_s=elapsed_s,
        commanded_speed_mps=speed,
        measured_m=measured,
        completed=completed,
        note=note,
    )


class DerivedQuantityTests(unittest.TestCase):
    def test_open_loop_expectation_and_ratios(self):
        trial = _trial(odom_m=0.5, elapsed_s=10.0, speed=0.05, measured=0.49)
        self.assertAlmostEqual(trial.open_loop_expected_m, 0.5)
        self.assertAlmostEqual(trial.odom_speed_mps, 0.05)
        self.assertAlmostEqual(trial.odom_over_command, 1.0)
        self.assertAlmostEqual(trial.scale_from_tape, 0.98)
        self.assertAlmostEqual(trial.open_loop_ratio, 0.98)

    def test_ratio_exposes_a_ten_times_odometry_error_without_a_tape(self):
        """里程计被放大 10 倍：闭环会在实际只走 1/10 时停车，耗时也变 1/10。"""
        trial = _trial(odom_m=0.5, elapsed_s=1.05, speed=0.05)
        self.assertAlmostEqual(trial.odom_over_command, 9.52, places=2)
        self.assertGreater(abs(trial.odom_over_command - 1.0), INCONSISTENCY_RATIO)

    def test_scale_needs_both_tape_and_positive_odometry(self):
        self.assertIsNone(_trial().scale_from_tape, "没尺量就不该给标度")
        self.assertIsNone(_trial(odom_m=0.0, measured=0.5).scale_from_tape, "里程计为 0 不能求标度")
        self.assertIsNone(_trial().open_loop_ratio, "没尺量就没有开环比值")

    def test_values_are_validated(self):
        for kwargs in (
            {"target": 0.0},
            {"target": -1.0},
            {"odom_m": -0.1},
            {"elapsed_s": 0.0},
            {"elapsed_s": -2.0},
            {"speed": 0.0},
            {"speed": -0.05},
            {"measured": 0.0},
            {"measured": -0.5},
            {"odom_m": float("nan")},
            {"elapsed_s": float("inf")},
        ):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                _trial(**kwargs)

    def test_as_dict_has_every_field_the_panel_needs(self):
        data = _trial(measured=0.48).as_dict()
        self.assertEqual(
            set(data),
            {
                "target_m", "odom_m", "elapsed_s", "commanded_speed_mps", "measured_m",
                "note", "completed", "odom_speed_mps", "odom_over_command",
                "open_loop_expected_m", "scale_from_tape", "open_loop_ratio",
            },
        )
        self.assertAlmostEqual(data["scale_from_tape"], 0.96)
        self.assertIsNone(_trial().as_dict()["scale_from_tape"])


class TrialFromResultTests(unittest.TestCase):
    def _result(self, **overrides):
        base = {
            "target_m": 0.5, "odom_m": 0.5, "elapsed_s": 10.4,
            "commanded_speed_mps": 0.05, "completed": True,
        }
        base.update(overrides)
        return base

    def test_builds_a_trial_from_the_dashboard_result(self):
        trial = trial_from_result(self._result(), measured_m=0.49, note="第一次")
        self.assertAlmostEqual(trial.measured_m, 0.49)
        self.assertEqual(trial.note, "第一次")
        self.assertAlmostEqual(trial.scale_from_tape, 0.98)

    def test_missing_fields_say_exactly_what_is_missing(self):
        with self.assertRaises(ValueError) as caught:
            trial_from_result({"target_m": 0.5})
        message = str(caught.exception)
        for key in ("odom_m", "elapsed_s", "commanded_speed_mps"):
            self.assertIn(key, message)
        self.assertIn("定距", message)

    def test_non_mapping_is_rejected(self):
        with self.assertRaises(ValueError):
            trial_from_result(["not", "a", "dict"])

    def test_incomplete_trial_keeps_its_flag(self):
        trial = trial_from_result(self._result(completed=False))
        self.assertFalse(trial.completed)


class AnalysisTests(unittest.TestCase):
    def test_no_trials_tells_you_what_to_run(self):
        analysis = analyze_trials([])
        self.assertEqual(analysis["count"], 0)
        self.assertIn("还没有", analysis["verdict"])
        self.assertIn("0.50", analysis["next_step"])

    def test_consistent_but_unmeasured_points_at_the_tape_measurement(self):
        analysis = analyze_trials([_trial(), _trial()])
        self.assertFalse(analysis["consistency"]["suspicious"])
        self.assertEqual(analysis["measured_count"], 0)
        self.assertIsNone(analysis["scale"])
        self.assertIn("自洽", analysis["verdict"])
        self.assertIn("尺量", analysis["next_step"])

    def test_inconsistent_odometry_is_caught_without_any_measurement(self):
        analysis = analyze_trials([_trial(odom_m=0.5, elapsed_s=1.05, speed=0.05)])
        self.assertTrue(analysis["consistency"]["suspicious"])
        self.assertIn("不自洽", analysis["verdict"])
        self.assertIn("常量", analysis["next_step"])

    def test_single_sample_is_not_enough_for_a_scale(self):
        analysis = analyze_trials([_trial(measured=0.49)])
        self.assertEqual(analysis["scale"]["n"], 1)
        self.assertFalse(analysis["scale"]["usable"])
        self.assertIn("样本不足", analysis["verdict"])
        self.assertIn(str(MIN_SAMPLES_FOR_SCALE), analysis["next_step"])

    def test_three_consistent_samples_give_a_usable_scale_close_to_one(self):
        """尺量 0.49/0.50/0.51 对应里程计 0.5 → 标度 0.98/1.00/1.02，中位数 1.00。"""
        trials = [_trial(measured=0.49), _trial(measured=0.50), _trial(measured=0.51)]
        analysis = analyze_trials(trials)
        self.assertTrue(analysis["scale"]["usable"])
        self.assertAlmostEqual(analysis["scale"]["median"], 1.00, places=2)
        self.assertIn("一致", analysis["verdict"])
        self.assertIn("measured", analysis["next_step"])

    def test_scale_far_from_one_reports_the_factor_and_suspects_firmware(self):
        trials = [_trial(measured=0.05), _trial(measured=0.05), _trial(measured=0.05)]
        analysis = analyze_trials(trials)
        self.assertAlmostEqual(analysis["scale"]["median"], 0.1, places=2)
        self.assertIn("乘以", analysis["verdict"])
        self.assertIn("固件", analysis["next_step"])
        self.assertIn("整数比", analysis["next_step"])

    def test_high_spread_refuses_to_give_a_scale(self):
        trials = [_trial(measured=0.40), _trial(measured=0.50), _trial(measured=0.62)]
        analysis = analyze_trials(trials)
        self.assertGreater(analysis["scale"]["relative_spread"], MAX_RELATIVE_SPREAD)
        self.assertFalse(analysis["scale"]["usable"])
        self.assertIn("波动", analysis["verdict"])
        self.assertIn("打滑", analysis["next_step"])

    def test_aborted_trials_are_listed_but_not_counted(self):
        trials = [_trial(measured=0.49), _trial(measured=0.50), _trial(measured=0.99, completed=False)]
        analysis = analyze_trials(trials)
        self.assertEqual(analysis["count"], 3)
        self.assertEqual(analysis["completed_count"], 2)
        self.assertEqual(analysis["measured_count"], 2)
        self.assertEqual([row["completed"] for row in analysis["trials"]], [True, True, False])

    def test_median_resists_one_bad_sample(self):
        """中位数而非均值：一条明显跑歪的记录不该把标度拖走（1.0 vs 0.4 → 仍取 1.0）。"""
        trials = [_trial(measured=0.50), _trial(measured=0.50), _trial(measured=0.50), _trial(measured=0.20)]
        analysis = analyze_trials(trials)
        self.assertAlmostEqual(analysis["scale"]["median"], 1.00, places=2)
        self.assertLess(analysis["scale"]["mean"], analysis["scale"]["median"])

    def test_open_loop_verdict_is_reported_when_speed_command_is_off(self):
        # 命令速度 0.05、耗时 10s → 开环预期 0.5 m；实际只走 0.3 m（偏差 40%）
        trials = [_trial(measured=0.30), _trial(measured=0.30), _trial(measured=0.30)]
        analysis = analyze_trials(trials)
        self.assertIsNotNone(analysis["open_loop"])
        self.assertFalse(analysis["open_loop"]["trustworthy"])
        self.assertIn("开环", analysis["verdict"])

    def test_open_loop_is_marked_trustworthy_when_speed_matches(self):
        trials = [_trial(measured=0.49), _trial(measured=0.50), _trial(measured=0.51)]
        analysis = analyze_trials(trials)
        self.assertTrue(analysis["open_loop"]["trustworthy"])
        self.assertNotIn("开环", analysis["verdict"])

    def test_non_trial_objects_are_ignored_not_crashed(self):
        analysis = analyze_trials([_trial(measured=0.5), "garbage", None])
        self.assertEqual(analysis["count"], 1)

    def test_consistency_median_is_reported_for_the_panel(self):
        # 0.5/10.5/0.05 = 0.952 与 0.5/10.0/0.05 = 1.0 → 中位数 0.976
        analysis = analyze_trials([_trial(), _trial(elapsed_s=10.0)])
        self.assertAlmostEqual(analysis["consistency"]["median"], 0.976, places=3)
        self.assertEqual(len(analysis["consistency"]["ratios"]), 2)


if __name__ == "__main__":
    unittest.main()
