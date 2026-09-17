import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.field_dashboard import DashboardController, RosFacade
from tools.field_dashboard_core import (
    LINE_CALIB_MIN_FRAMES,
    LINE_CALIB_MIN_SPAN,
    LINE_TICK_GAP_MS,
    LineCalibration,
    line_chain_health,
)


def feed(calib, target, value, *, start, frames=60, dt=0.02, step=0):
    """把一组基准喂给 LineCalibration，返回最后一帧的时刻。"""
    calib.begin_capture(target)
    now = start
    for index in range(frames):
        now = start + index * dt
        calib.capture(target, [value + step * i for i in range(8)], now=now)
    calib.end_capture()
    return now


class LineCalibrationTests(unittest.TestCase):
    """黑白标定：把"默认假设 0/4095"换成实测基准，并在网页上给出失败原因。"""

    def test_incomplete_calibration_explains_which_group_is_missing(self):
        calib = LineCalibration()
        verdict = calib.verdict(now=0.0)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["code"], "INCOMPLETE")
        self.assertIn("白底", verdict["detail"])
        self.assertIn("黑线", verdict["detail"])

    def test_short_press_is_rejected_by_dwell_time(self):
        calib = LineCalibration()
        feed(calib, "white", 210, start=0.0, frames=15)  # 0.28 s
        feed(calib, "black", 820, start=1.0, frames=60)
        verdict = calib.verdict(now=2.2)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["code"], "INCOMPLETE")
        self.assertIn("白底采样时间不足", verdict["detail"])

    def test_dwell_time_uses_sample_span_not_time_since_press(self):
        """按住 0.3 秒、然后等 10 秒，仍然是采样不足——不能靠等待蒙混过关。"""
        calib = LineCalibration()
        feed(calib, "white", 210, start=0.0, frames=15)
        feed(calib, "black", 820, start=1.0, frames=60)
        self.assertFalse(calib.verdict(now=999.0)["ok"])

    def test_low_contrast_is_rejected_with_hardware_advice(self):
        calib = LineCalibration()
        feed(calib, "white", 200, start=0.0)
        feed(calib, "black", 200 + LINE_CALIB_MIN_SPAN - 50, start=1.5)
        verdict = calib.verdict(now=3.0)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["code"], "NO_CONTRAST")
        self.assertIn("探头高度", verdict["problems"][0])

    def test_too_few_frames_is_rejected(self):
        calib = LineCalibration()
        calib.begin_capture("white")
        # 时长够（1.2s）但只有 4 帧：单帧噪声当基准不可接受。
        for index in range(LINE_CALIB_MIN_FRAMES - 1):
            calib.capture("white", [200] * 8, now=index * 0.4)
        calib.end_capture()
        feed(calib, "black", 900, start=2.0)
        verdict = calib.verdict(now=3.5)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["code"], "FEW_FRAMES")

    def test_success_returns_per_channel_references(self):
        calib = LineCalibration()
        feed(calib, "white", 210, start=0.0, step=3)
        feed(calib, "black", 820, start=1.5, step=3)
        verdict = calib.verdict(now=3.0)
        self.assertTrue(verdict["ok"], verdict["detail"])
        self.assertEqual(len(verdict["white_ref"]), 8)
        self.assertEqual(len(verdict["black_ref"]), 8)
        self.assertAlmostEqual(verdict["white_ref"][0], 210.0, places=1)
        self.assertAlmostEqual(verdict["black_ref"][0], 820.0, places=1)
        self.assertGreaterEqual(verdict["span"], LINE_CALIB_MIN_SPAN)

    def test_swapped_groups_are_detected_and_corrected(self):
        """两组按反了必须自动交换——否则归一化会把整条算法反向。"""
        calib = LineCalibration()
        feed(calib, "white", 3200, start=0.0)   # 用户把黑线当白底标了
        feed(calib, "black", 250, start=1.5)
        verdict = calib.verdict(now=3.0)
        self.assertTrue(verdict["ok"])
        self.assertIn("接反", verdict["note"])
        # 修正后约定不变：white_ref < black_ref
        for white, black in zip(verdict["white_ref"], verdict["black_ref"]):
            self.assertLess(white, black)

    def test_begin_apply_refuses_to_commit_a_failed_calibration(self):
        calib = LineCalibration()
        with self.assertRaises(ValueError):
            calib.begin_apply(now=0.0)


class LineCalibrationSnapshotTests(unittest.TestCase):
    """面板状态：进度、下一步提示、以及"通过才能应用"。"""

    def _controller(self):
        return DashboardController(Mock(snapshot=lambda: []), Mock(), Mock(), Mock())

    def test_snapshot_starts_empty_and_says_what_to_do(self):
        snap = self._controller().line_calibration_snapshot()
        self.assertFalse(snap["ready"])
        self.assertIsNone(snap["capturing"])
        self.assertIn("白底", snap["next_step"])

    def test_snapshot_reports_progress_and_ready_flag(self):
        controller = self._controller()
        feed(controller.line_calibration, "white", 210, start=0.0)
        feed(controller.line_calibration, "black", 820, start=1.5)
        snap = controller.line_calibration_snapshot()
        self.assertTrue(snap["ready"])
        self.assertGreaterEqual(snap["white_frames"], LINE_CALIB_MIN_FRAMES)
        self.assertIn("应用标定", snap["next_step"])

    def test_capture_state_is_explicit_not_inferred(self):
        """采集中必须靠显式标志；不能靠"某组有数据"推断，否则会一直采样。"""
        calib = LineCalibration()
        self.assertIsNone(calib.active_target())
        calib.begin_capture("white")
        self.assertEqual(calib.active_target(), "white")
        calib.end_capture()
        self.assertIsNone(calib.active_target())
        # 已有白底数据也不再自动算作"采集中"
        calib.capture("white", [1] * 8, now=0.0)
        self.assertIsNone(calib.active_target())


class LineCalibrationFreshnessTests(unittest.TestCase):
    """标定前必须确认读数新鲜，否则采到的是旧值。"""

    def test_missing_or_stale_reading_blocks_calibration(self):
        facade = RosFacade.__new__(RosFacade)
        self.assertIn("尚未收到", facade.line_reading_is_fresh(10.0)["reason"])

    def test_fresh_reading_passes(self):
        facade = RosFacade.__new__(RosFacade)
        facade._ensure_line_state()
        facade._line_last_rx = 9.9
        result = facade.line_reading_is_fresh(10.0)
        self.assertTrue(result["ok"])
        self.assertLess(result["age_s"], 0.25)


class LineChainHealthTests(unittest.TestCase):
    """整条巡线链的断点判定：每条断言对应一个真实故障位置。"""

    def test_no_frame_points_at_bridge_or_firmware(self):
        health = line_chain_health(now=100.0, last_frame_at=None, telemetry={})
        self.assertEqual(health["state"], "未开始")
        self.assertEqual(health["blockers"][0]["code"], "NO_FRAME")
        self.assertIn("0x14", health["blockers"][0]["fix"])

    def test_stale_frames_report_rate_and_age(self):
        health = line_chain_health(
            now=100.0, last_frame_at=99.0,
            telemetry={"line_analog_valid": True}, line_hz=3.0,
        )
        self.assertEqual(health["state"], "断点(0x14/进程)")
        blocker = health["blockers"][0]
        self.assertEqual(blocker["code"], "FRAME_STALE")
        self.assertIn("3.0Hz", blocker["evidence"])

    def test_analog_invalid_is_a_distinct_break_point(self):
        """帧在到达但模拟量无效 —— 这是探头/固件问题，不是进程问题。"""
        health = line_chain_health(
            now=100.0, last_frame_at=99.95,
            telemetry={"line_analog_valid": False, "line_invalid_frames": 120},
        )
        self.assertEqual(health["state"], "断点(模拟量)")
        self.assertEqual(health["blockers"][0]["code"], "ANALOG_INVALID")
        self.assertIn("120", health["blockers"][0]["evidence"])

    def test_tick_anomaly_is_a_distinct_break_point(self):
        health = line_chain_health(
            now=100.0, last_frame_at=99.95,
            telemetry={"line_analog_valid": True}, tick_anomalies=4,
        )
        self.assertEqual(health["state"], "断点(mcu tick)")
        self.assertEqual(health["blockers"][0]["code"], "TICK_ANOMALY")

    def test_controller_gate_reasons_are_forwarded_verbatim(self):
        health = line_chain_health(
            now=100.0, last_frame_at=99.95,
            telemetry={
                "line_analog_valid": True,
                "line_diag": {"blocked": True, "reasons": ["STM32 通信不可用 (communication_ok=false)"]},
            },
        )
        self.assertEqual(health["state"], "断点(算法出口)")
        blocker = health["blockers"][0]
        self.assertEqual(blocker["code"], "CONTROLLER_BLOCKED")
        self.assertIn("communication_ok=false", blocker["evidence"])

    def test_zero_published_command_is_called_out(self):
        health = line_chain_health(
            now=100.0, last_frame_at=99.95,
            telemetry={
                "line_analog_valid": True,
                "line_diag": {"blocked": False},
                "line_cmd": {"vx": 0.2, "vy": 0.0, "wz": 0.0},
                "published_cmd": {"vx": 0.0, "vy": 0.0, "wz": 0.0},
            },
        )
        self.assertIn("ZERO_PUBLISHED", [b["code"] for b in health["blockers"]])

    def test_healthy_chain_reports_normal(self):
        health = line_chain_health(
            now=100.0, last_frame_at=99.95,
            telemetry={"line_analog_valid": True, "line_diag": {"blocked": False}},
            line_hz=50.0,
        )
        self.assertEqual(health["state"], "正常")
        self.assertEqual([b["code"] for b in health["blockers"]], [])

    def test_small_channel_span_flags_calibration_before_tuning(self):
        health = line_chain_health(
            now=100.0, last_frame_at=99.95,
            telemetry={
                "line_analog_valid": True,
                "line_diag": {"blocked": False},
                "line_sensor": [2000, 2010, 2020, 2030, 2040, 2050, 2060, 2070],
            },
        )
        self.assertEqual(health["calibration"]["status"], "suspect")
        self.assertIn("分离度", health["calibration"]["detail"])

    def test_low_module_sample_rate_is_flagged(self):
        health = line_chain_health(
            now=100.0, last_frame_at=99.95,
            telemetry={"line_analog_valid": True, "line_sensor": [0, 3000] * 4},
            sample_times=[0.0, 0.5, 1.0],
        )
        self.assertEqual(health["calibration"]["status"], "suspect")
        self.assertIn("采样率", health["calibration"]["detail"])


class LineTelemetryTests(unittest.TestCase):
    def test_sensor_metadata_and_velocity_reach_snapshot_and_age(self):
        controller = DashboardController(Mock(snapshot=lambda: []), Mock(), Mock(), Mock())
        facade = RosFacade.__new__(RosFacade)
        facade.controller = controller
        with patch('tools.field_dashboard.time.monotonic', return_value=10):
            facade._line(SimpleNamespace(channels=[2048]*8, analog_valid=False, mcu_tick_ms=123))
            facade._velocity('line_cmd', SimpleNamespace(
                linear=SimpleNamespace(x=.2, y=0), angular=SimpleNamespace(z=-.1)))
        with patch('tools.field_dashboard.time.monotonic', return_value=11):
            telemetry = controller.snapshot()['telemetry']
        self.assertEqual(telemetry['line_sensor'], [2048]*8)
        self.assertFalse(telemetry['line_analog_valid'])
        self.assertEqual(telemetry['line_mcu_tick_ms'], 123)
        self.assertEqual(telemetry['line_sensor_age_s'], 1)
        self.assertEqual(telemetry['line_cmd_age_s'], 1)
        self.assertEqual(telemetry['line_cmd']['wz'], -.1)

    def test_graph_sources_are_kept_for_both_topics(self):
        facade = RosFacade.__new__(RosFacade)
        controller = Mock()
        controller.state = SimpleNamespace(telemetry={})
        facade.controller = controller
        facade.node = Mock()
        facade.node.get_publishers_info_by_topic.side_effect = [
            [SimpleNamespace(node_name='line_sensor_mock')],
            [SimpleNamespace(node_name='field_dashboard'), SimpleNamespace(node_name='line_follow_controller')],
        ]
        facade._line_sources()
        # 1Hz 心跳里还会顺带刷新巡线链路结论，因此取包含 line_sources 的那一次调用。
        values = next(
            call.args[0]
            for call in facade.controller.update_telemetry.call_args_list
            if 'line_sources' in call.args[0]
        )
        self.assertEqual(values['line_sources'], ['line_sensor_mock'])
        self.assertEqual(len(values['cmd_sources']), 2)


class LineTickContinuityTests(unittest.TestCase):
    def test_frames_arriving_in_the_same_instant_do_not_divide_by_zero(self):
        """缓冲积压后两帧可能在同一时刻派发，帧率窗口为 0。

        这是端到端冒烟里真实踩到的崩溃点（ZeroDivisionError），
        会让整个 ROS 回调抛异常、面板再也不更新巡线数据。
        """
        facade = RosFacade.__new__(RosFacade)
        controller = Mock()
        controller.state = SimpleNamespace(telemetry={})
        facade.controller = controller
        msg = SimpleNamespace(channels=[0] * 8, analog_valid=True, mcu_tick_ms=10)
        with patch('tools.field_dashboard.time.monotonic', return_value=5.0):
            facade._line(msg)
            facade._line(SimpleNamespace(channels=[0] * 8, analog_valid=True, mcu_tick_ms=20))
        published = [
            call.args[0] for call in controller.update_telemetry.call_args_list
            if 'line_hz' in call.args[0]
        ]
        self.assertTrue(published, "帧率必须被上报")
        self.assertEqual(published[-1]['line_hz'], 0.0)



    def test_first_frame_and_normal_progress_are_not_anomalies(self):
        self.assertFalse(RosFacade.tick_is_anomalous(None, 100))
        self.assertFalse(RosFacade.tick_is_anomalous(100, 120))
        self.assertFalse(RosFacade.tick_is_anomalous(100, 100 + LINE_TICK_GAP_MS))

    def test_gap_beyond_tolerance_means_frames_were_dropped(self):
        self.assertTrue(RosFacade.tick_is_anomalous(100, 100 + LINE_TICK_GAP_MS + 1))

    def test_backwards_tick_is_anomalous(self):
        self.assertTrue(RosFacade.tick_is_anomalous(500, 100))


class LineDiagParsingTests(unittest.TestCase):
    """面板从 /line_follow/status 取出控制器发布的逐层门控证据。"""

    def test_new_format_is_parsed(self):
        parsed = RosFacade.parse_line_diag(
            'state=ON_LINE dev=0.100 stale=False #diag#{"blocked":true,"reasons":["X"]}'
        )
        self.assertEqual(parsed, {"blocked": True, "reasons": ["X"]})

    def test_legacy_status_without_marker_returns_none(self):
        self.assertIsNone(RosFacade.parse_line_diag('state=ON_LINE stale=True'))

    def test_malformed_payload_returns_none_instead_of_raising(self):
        self.assertIsNone(RosFacade.parse_line_diag('state=X #diag#{not json'))
        self.assertIsNone(RosFacade.parse_line_diag(None))
