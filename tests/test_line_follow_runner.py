"""LineFollowRunner 行为测试（本轮 A 巡线接线的核心逻辑层）。

覆盖：居中/左偏/右偏/出线/LOST/交叉口/全黑/失联停车/输入拒绝/参数校验/
reset/PD 符号与微分项。全部纯逻辑，零 ROS 依赖。
"""
import unittest

from robogame_core.line_follow import LineSensorReading, LineSensorState
from motion_control.line_follow_runner import (
    LineFollowOutput,
    LineFollowParams,
    LineFollowRunner,
)


def reading(*active_indices: int, raw: list[float] | None = None) -> LineSensorReading:
    channels = [i in active_indices for i in range(8)]
    return LineSensorReading(channels=channels, raw_values=raw)


def run_once(runner: LineFollowRunner, values: list[float], now: float = 1.0) -> LineFollowOutput:
    return runner.update(reading(raw=values), now=now)


class LineFollowRunnerBasicTests(unittest.TestCase):
    """正常路径：状态与偏差方向。"""

    def setUp(self):
        self.params = LineFollowParams(
            kp=1.0, kd=0.0, vx_base=0.2, threshold=0.5,
            dt_s=0.02, max_reading_gap_s=0.25,
        )
        self.runner = LineFollowRunner(self.params)

    def test_centered_is_on_line_with_zero_deviation(self):
        # 通道 3/4 黑（居中）→ ON_LINE，偏差接近 0
        out = run_once(self.runner, [0.1]*3 + [0.9, 0.9] + [0.1]*3)
        self.assertEqual(out.state, LineSensorState.ON_LINE)
        self.assertLess(abs(out.deviation), 0.1)
        self.assertEqual(out.vx, self.params.vx_base)

    def test_left_edge_negative_deviation(self):
        # 线偏左（车偏右）→ LEFT_EDGE，偏差 < 0
        out = run_once(self.runner, [0.9, 0.9] + [0.1]*6)
        self.assertEqual(out.state, LineSensorState.LEFT_EDGE)
        self.assertLess(out.deviation, -0.3)

    def test_right_edge_positive_deviation(self):
        # 线偏右（车偏左）→ RIGHT_EDGE，偏差 > 0
        out = run_once(self.runner, [0.1]*6 + [0.9, 0.9])
        self.assertEqual(out.state, LineSensorState.RIGHT_EDGE)
        self.assertGreater(out.deviation, 0.3)

    def test_pd_steering_sign(self):
        # 线在右（dev>0）→ wz<0 右转；线在左 → wz>0 左转
        right = run_once(self.runner, [0.1]*6 + [0.9, 0.9])
        self.assertLess(right.wz, 0.0)
        left = run_once(self.runner, [0.9, 0.9] + [0.1]*6)
        self.assertGreater(left.wz, 0.0)

    def test_deviation_uses_weighted_raw_values(self):
        # 加权法：越靠近线心的通道权重越大，方向由质心决定
        out = run_once(self.runner, [0.9] + [0.1]*7)
        self.assertLess(out.deviation, -0.5)

    def test_output_is_frozen_dataclass_with_all_fields(self):
        out = run_once(self.runner, [0.1]*3 + [0.9, 0.9] + [0.1]*3)
        self.assertIsInstance(out, LineFollowOutput)
        self.assertFalse(out.reading_stale)
        self.assertIsInstance(out.lost_count, int)


class LineFollowRunnerLostTests(unittest.TestCase):
    """出线与 LOST 停车。"""

    def setUp(self):
        self.params = LineFollowParams(
            kp=1.0, kd=0.0, vx_base=0.2, threshold=0.5,
            lost_threshold=5, dt_s=0.02, max_reading_gap_s=0.25,
        )
        self.runner = LineFollowRunner(self.params)

    def test_lost_count_increments_until_threshold(self):
        # 先在线（ON_LINE），再出线：lost_count 递增、输出停车，
        # 达到阈值才把状态标成 LOST（原 line_follow.py 语义：出线即停，
        # 状态机延迟 lost_threshold 帧确认）。
        run_once(self.runner, [0.1]*3 + [0.9, 0.9] + [0.1]*3, now=1.0)
        lost_values = [0.1]*8
        for i in range(self.params.lost_threshold - 1):
            out = run_once(self.runner, lost_values, now=1.0 + 0.02*(i+1))
            self.assertEqual(out.lost_count, i + 1)
            # 未达阈值：状态保持前一状态（ON_LINE），偏差 NaN → 停车
            self.assertEqual(out.state, LineSensorState.ON_LINE)
            self.assertEqual(out.vx, 0.0)
        out = run_once(
            self.runner, lost_values,
            now=1.0 + 0.02*self.params.lost_threshold,
        )
        self.assertEqual(out.state, LineSensorState.LOST)
        self.assertEqual(out.vx, 0.0)
        self.assertEqual(out.wz, 0.0)

    def test_recovery_after_lost_returns_to_on_line(self):
        for i in range(self.params.lost_threshold):
            run_once(self.runner, [0.1]*8, now=1.0 + 0.02*i)
        out = run_once(self.runner, [0.1]*3 + [0.9, 0.9] + [0.1]*3,
                       now=1.0 + 0.02*(self.params.lost_threshold + 1))
        self.assertEqual(out.state, LineSensorState.ON_LINE)
        self.assertEqual(out.lost_count, 0)


class LineFollowRunnerIntersectionTests(unittest.TestCase):
    """交叉口/全黑：默认直行通过（B 阶段由路段层决策覆盖）。"""

    def setUp(self):
        self.params = LineFollowParams(
            kp=1.0, kd=0.0, vx_base=0.2, threshold=0.5,
            intersection_threshold=6, dt_s=0.02, max_reading_gap_s=0.25,
        )
        self.runner = LineFollowRunner(self.params)

    def test_intersection_goes_straight(self):
        # 前 6 路激活 → INTERSECTION → vx=vx_base, wz=0
        out = run_once(self.runner, [0.9]*6 + [0.1]*2)
        self.assertEqual(out.state, LineSensorState.INTERSECTION)
        self.assertEqual(out.vx, self.params.vx_base)
        self.assertEqual(out.wz, 0.0)

    def test_all_black_goes_straight(self):
        out = run_once(self.runner, [0.9]*8)
        self.assertEqual(out.state, LineSensorState.ALL_BLACK)
        self.assertEqual(out.vx, self.params.vx_base)


class LineFollowRunnerSafetyTests(unittest.TestCase):
    """失联停车与输入拒绝。"""

    def setUp(self):
        self.params = LineFollowParams(
            kp=1.0, kd=0.0, vx_base=0.2, threshold=0.5,
            dt_s=0.02, max_reading_gap_s=0.25,
        )
        self.runner = LineFollowRunner(self.params)

    def test_stale_reading_outputs_zero(self):
        run_once(self.runner, [0.1]*3 + [0.9, 0.9] + [0.1]*3, now=1.0)
        # 间隔 0.5s > 0.25s → 失联停车
        out = run_once(self.runner, [0.1]*3 + [0.9, 0.9] + [0.1]*3, now=1.5)
        self.assertTrue(out.reading_stale)
        self.assertEqual(out.state, LineSensorState.LOST)
        self.assertEqual(out.vx, 0.0)
        self.assertEqual(out.wz, 0.0)

    def test_wrong_channel_count_rejected(self):
        with self.assertRaisesRegex(ValueError, "8 channels"):
            self.runner.update(
                LineSensorReading(channels=[True]*7, raw_values=[0.1]*7),
                now=1.0,
            )

    def test_wrong_raw_count_rejected(self):
        with self.assertRaisesRegex(ValueError, "8 raw values"):
            self.runner.update(
                reading(raw=[0.1]*7),
                now=1.0,
            )

    def test_nonfinite_raw_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            self.runner.update(
                reading(raw=[0.1]*7 + [float("nan")]),
                now=1.0,
            )

    def test_nonfinite_now_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            self.runner.update(reading(raw=[0.1]*8), now=float("inf"))

    def test_channels_only_path_uses_centroid(self):
        # 无 raw_values 时走质心法（兼容纯 bool 输入）
        out = self.runner.update(reading(2, 3, 4), now=1.0)
        self.assertEqual(out.state, LineSensorState.ON_LINE)


class LineFollowParamsValidationTests(unittest.TestCase):
    """参数校验。"""

    def test_negative_kp_rejected(self):
        with self.assertRaisesRegex(ValueError, "kp"):
            LineFollowParams(kp=-0.1)

    def test_threshold_out_of_range_rejected(self):
        with self.assertRaisesRegex(ValueError, "threshold"):
            LineFollowParams(threshold=0.0)
        with self.assertRaisesRegex(ValueError, "threshold"):
            LineFollowParams(threshold=1.0)

    def test_zero_dt_rejected(self):
        with self.assertRaisesRegex(ValueError, "dt_s"):
            LineFollowParams(dt_s=0.0)

    def test_zero_max_gap_rejected(self):
        with self.assertRaisesRegex(ValueError, "max_reading_gap_s"):
            LineFollowParams(max_reading_gap_s=0.0)

    def test_lost_threshold_zero_rejected(self):
        with self.assertRaisesRegex(ValueError, "lost"):
            LineFollowParams(lost_threshold=0)


class LineFollowRunnerResetTests(unittest.TestCase):
    """reset：清空状态机与 PD 历史。"""

    def test_reset_clears_history(self):
        params = LineFollowParams(
            kp=1.0, kd=0.0, vx_base=0.2, threshold=0.5,
            dt_s=0.02, max_reading_gap_s=0.25,
        )
        runner = LineFollowRunner(params)
        for i in range(6):
            run_once(runner, [0.1]*8, now=1.0 + 0.02*i)
        self.assertEqual(runner.lost_count, 6)
        runner.reset()
        self.assertEqual(runner.lost_count, 0)
        self.assertIsNone(runner.last_reading_time)
        self.assertEqual(runner.prev_state, LineSensorState.LOST)


if __name__ == "__main__":
    unittest.main()
