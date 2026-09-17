"""里程计标定的网页接线测试（行为级：真的跑一遍定距→补录尺量→看汇总）。

为什么值得行为测试而不是只查文本：这条链路里有一个**容易搞错、后果很直接**的
语义——「中断的定距试验不能当标定样本」。中途急停/心跳超时/通信超时都会走
`zero_and_release`，如果那时不清掉候选，操作员会把上一次的结果当本次数据记录，
于是标度直接算错。

用仓库既有脚手架（`tests/test_dashboard_distance.py` 同款）：Mock ROS + 真实
`DashboardController`，不碰硬件、不发布速度。
"""

from __future__ import annotations

import asyncio
import math
import time
import unittest
from unittest.mock import AsyncMock, Mock

from tools.field_dashboard import DashboardController, SafetyStatus


class OdomCalibrationPanelTests(unittest.IsolatedAsyncioTestCase):
    def app(self) -> DashboardController:
        console = Mock()
        console.items = {}
        console.start = AsyncMock()
        console.stop = AsyncMock()
        app = DashboardController(console, Mock(), Mock(), asyncio.get_running_loop())
        app.record = Mock()
        app.ros = Mock()
        app.ros.publishers.return_value = []
        app.ros.mechanism.return_value = {"success": True}
        self.feed(app, y=3.0)
        return app

    def feed(self, app: DashboardController, *, x=2.0, y=3.0, yaw=math.pi / 2, now=None) -> None:
        app.state.safety = SafetyStatus(
            received_at=time.monotonic(), communication_ok=True, physical_start=True, boot_id=1
        )
        app.update_telemetry(
            {"distance_pose_x": x, "distance_pose_y": y, "pose_yaw": yaw, "odom_feedback_valid": True}
        )

    async def run_full_trial(self, app: DashboardController, *, start_y=3.0, distance=0.5,
                             speed=0.05, elapsed=10.4) -> float:
        """跑一次完整定距（用时间控制 pose 增量，模拟里程计反馈）。

        返回本次结束时的 y，便于下一次从那里继续（否则第二次会「原地不动」）。
        """
        self.feed(app, y=start_y)
        await app.action("/api/distance/start", {"distance_m": distance, "speed_mps": speed})
        trial = app.distance_trial
        trial.started_at = time.monotonic() - elapsed
        trial.heartbeat_at = time.monotonic()
        end_y = start_y + distance
        self.feed(app, y=end_y)
        app.distance_tick(time.monotonic())
        return end_y

    async def test_completed_trial_becomes_a_recordable_candidate(self):
        app = self.app()
        await self.run_full_trial(app)
        self.assertEqual(app.distance_result["state"], "里程计目标已到达")
        self.assertIsNotNone(app.odom_trial_candidate)
        candidate = app.odom_trial_candidate
        self.assertAlmostEqual(candidate["odom_m"], 0.5)
        self.assertAlmostEqual(candidate["commanded_speed_mps"], 0.05)
        self.assertGreater(candidate["elapsed_s"], 0)
        self.assertTrue(candidate["completed"])

    async def test_record_pairs_tape_measurement_and_reports_scale(self):
        app = self.app()
        await self.run_full_trial(app)
        result = await app.action("/api/odom/record", {"measured_m": 0.49, "note": "第一次"})
        self.assertTrue(result["ok"])
        analysis = result["analysis"]
        self.assertEqual(analysis["measured_count"], 1)
        self.assertAlmostEqual(analysis["scale"]["median"], 0.98, places=2)
        self.assertEqual(analysis["trials"][0]["note"], "第一次")
        # 记录必须进归档（原始证据）
        recorded = [call.args[0] for call in app.record.call_args_list]
        self.assertTrue(any(item.get("action") == "odom_record" for item in recorded))

    async def test_three_records_give_a_usable_scale(self):
        app = self.app()
        y = 3.0
        for measured in (0.49, 0.50, 0.51):
            y = await self.run_full_trial(app, start_y=y)
            await app.action("/api/odom/record", {"measured_m": measured})
        calibration = app.snapshot()["odom_calibration"]
        self.assertEqual(calibration["measured_count"], 3)
        self.assertTrue(calibration["scale"]["usable"])
        self.assertAlmostEqual(calibration["scale"]["median"], 1.0, places=2)

    async def test_aborted_trial_leaves_no_candidate(self):
        """中断的试验不能当样本（可能只走了一半），也不能留下旧候选冒充本次数据。"""
        app = self.app()
        self.feed(app, y=3.0)
        await app.action("/api/distance/start", {"distance_m": 0.5, "speed_mps": 0.05})
        self.feed(app, y=3.2)  # 只走了一半
        app.distance_tick(time.monotonic())
        self.assertIsNotNone(app.distance_trial, "没到目标时试验应继续")
        self.assertIsNone(app.odom_trial_candidate, "没跑完就不该产生候选")
        await app.action("/api/distance/stop", {})
        with self.assertRaises(ValueError) as caught:
            await app.action("/api/odom/record", {"measured_m": 0.2})
        self.assertIn("没有可记录的定距结果", str(caught.exception))
        self.assertEqual(len(app.odom_trials), 0)

    async def test_completed_candidate_is_kept_until_a_new_trial_starts(self):
        """跑完的候选在「开始下一次」之前一直有效（操作员尺量需要时间）。"""
        app = self.app()
        await self.run_full_trial(app)
        await app.action("/api/distance/stop", {})  # 此时没有活动试验
        self.assertIsNotNone(app.odom_trial_candidate)
        await app.action("/api/distance/start", {"distance_m": 0.5, "speed_mps": 0.05})
        self.assertIsNone(app.odom_trial_candidate)

    async def test_record_without_tape_is_allowed_for_consistency_samples(self):
        """尺量可留空：这样「里程计自洽性」（不用尺子）也能累积样本。"""
        app = self.app()
        await self.run_full_trial(app)
        result = await app.action("/api/odom/record", {})
        self.assertTrue(result["ok"])
        analysis = result["analysis"]
        self.assertEqual(analysis["count"], 1)
        self.assertEqual(analysis["measured_count"], 0)
        self.assertIsNone(analysis["scale"])
        self.assertIn("尺量", analysis["next_step"])

    async def test_invalid_tape_value_is_rejected_with_a_readable_message(self):
        app = self.app()
        await self.run_full_trial(app)
        for body in ({"measured_m": "abc"}, {"measured_m": 0}, {"measured_m": -0.3}):
            with self.assertRaises(ValueError) as caught:
                await app.action("/api/odom/record", body)
            self.assertIn("尺量位移", str(caught.exception))

    async def test_reset_clears_samples_and_archives_them(self):
        app = self.app()
        await self.run_full_trial(app)
        await app.action("/api/odom/record", {"measured_m": 0.5})
        result = await app.action("/api/odom/reset", {})
        self.assertEqual(result["cleared"], 1)
        self.assertEqual(app.odom_trials, [])
        self.assertEqual(app.snapshot()["odom_calibration"]["count"], 0)

    async def test_snapshot_exposes_both_candidate_and_analysis(self):
        app = self.app()
        snapshot = app.snapshot()
        self.assertIn("odom_calibration", snapshot)
        self.assertIn("odom_trial_candidate", snapshot)
        self.assertIn("verdict", snapshot["odom_calibration"])
        self.assertIn("next_step", snapshot["odom_calibration"])

    async def test_inconsistent_odometry_is_visible_before_any_tape_measurement(self):
        """10 倍偏差场景：里程计报 0.5 m 只花了 1.05 s → 等效速度 10 倍命令速度。

        这是**不用尺子**就能看出来的那条。记录时留空尺量即可累积该样本。
        """
        app = self.app()
        await self.run_full_trial(app, start_y=3.0, distance=0.5, speed=0.05, elapsed=1.05)
        await app.action("/api/odom/record", {})
        calibration = app.snapshot()["odom_calibration"]
        self.assertEqual(calibration["measured_count"], 0)
        self.assertTrue(calibration["consistency"]["suspicious"])
        self.assertIn("不自洽", calibration["verdict"])


class OdomPanelWiringTests(unittest.TestCase):
    def test_panel_is_loaded_before_app_js(self):
        from pathlib import Path

        page = (
            Path(__file__).resolve().parents[1] / "tools/field_dashboard_web/index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('src="/odom_panel.js"', page)
        self.assertLess(
            page.index('src="/odom_panel.js"'),
            page.index('src="/app.js"'),
            "odom_panel.js 必须在 app.js 之前加载",
        )
        for element in (
            "odomLive", "odomConsistency", "odomScale", "odomOpenLoop",
            "odomVerdict", "odomNextStep", "odomTrials", "odomMeasured",
            "odomNote", "odomRecord", "odomReset",
        ):
            self.assertIn(f'id="{element}"', page, element)

    def test_app_js_wires_record_and_reset_buttons(self):
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[1] / "tools/field_dashboard_web/app.js"
        ).read_text(encoding="utf-8")
        self.assertIn("renderOdom(s)", script)
        self.assertIn("/api/odom/record", script)
        self.assertIn("/api/odom/reset", script)
        self.assertIn("odomMeasured", script)

    def test_panel_js_never_drives_the_chassis_directly(self):
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[1] / "tools/field_dashboard_web/odom_panel.js"
        ).read_text(encoding="utf-8")
        for forbidden in ("fetch(", "api(", "XMLHttpRequest", "/api/"):
            self.assertNotIn(forbidden, script, f"渲染模块不该直接请求/驱动：{forbidden}")


if __name__ == "__main__":
    unittest.main()
