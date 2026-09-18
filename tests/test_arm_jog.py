"""关节微调（jog）：判据、边界、以及"松手就停"到底靠什么。

全部离线跑：不连 ROS、不连硬件。被检查的输入（方向、步长、心跳、安全门）都由测试
直接喂进去。因此这里证明的是**判定与措辞**，不能证明真车上舵机真的动了。
"""

import asyncio
import time
import unittest
from unittest.mock import Mock, patch

from tools.arm_jog import (
    JOG_HEARTBEAT_TIMEOUT_S,
    JOG_LIMIT_DEG,
    JOG_SAFE_POSE_DEG,
    JOG_SERVICE_TIMEOUT_S,
    JogSession,
    jog_stop_reason,
    joint_label,
    validate_jog_request,
)
from tools.field_dashboard import DashboardController


def session_about_to_start(**overrides):
    kwargs = {
        "joint": 1, "direction": 1, "step_deg": 5, "period_s": 0.2,
        "start_deg": JOG_SAFE_POSE_DEG[1],
    }
    kwargs.update(overrides)
    request = validate_jog_request(**kwargs)
    return JogSession(
        request=request, token="t0", started_at=100.0, heartbeat_at=100.0,
        last_target_deg=request.start_deg,
    )


def live_session(**overrides):
    """心跳基准取"现在"的会话（循环类测试必须用它）。

    真实路径里 `_arm_jog_start` 用 `time.monotonic()` 初始化心跳。测试若拿假时间戳
    （例如 100.0），`jog_stop_reason` 会立刻判"心跳中断"——这不是代码问题，是测试没把
    时间基准对齐（写这组测试时踩过）。
    """
    kwargs = {
        "joint": 1, "direction": 1, "step_deg": 5, "period_s": 0.2,
        "start_deg": JOG_SAFE_POSE_DEG[1],
    }
    kwargs.update(overrides)
    request = validate_jog_request(**kwargs)
    now = time.monotonic()
    return JogSession(
        request=request, token="t0", started_at=now, heartbeat_at=now,
        last_target_deg=request.start_deg,
    )


def make_safety_ready(controller):
    """把安全总览置成"可以动机构"的状态（否则安全门会先拦住，测不到后面的逻辑）。"""
    safety = controller.state.safety
    safety.received_at = time.monotonic()
    safety.communication_ok = True
    safety.physical_start = True
    safety.emergency_stop = False
    safety.mechanism_fault = False
    return controller


class ValidateRequestTests(unittest.TestCase):
    def test_normal_request_is_accepted_and_labelled(self):
        request = validate_jog_request(
            joint=2, direction=-1, step_deg=5, period_s=0.5, start_deg=135
        )
        self.assertEqual(request.joint, 2)
        self.assertEqual(request.direction, -1)
        self.assertIn("肘", request.label)
        self.assertIn("角度减小", request.label)

    def test_gripper_joint_is_refused_with_the_right_way_to_do_it(self):
        with self.assertRaisesRegex(ValueError, "爪子不走 ARM_SET"):
            validate_jog_request(joint=4, direction=1, step_deg=5, period_s=0.5, start_deg=100)

    def test_unknown_joint_lists_the_allowed_ones(self):
        with self.assertRaisesRegex(ValueError, "0=腰/云盘"):
            validate_jog_request(joint=7, direction=1, step_deg=5, period_s=0.5, start_deg=100)

    def test_fractional_step_is_refused_because_the_protocol_has_no_decimals(self):
        with self.assertRaisesRegex(ValueError, "整度"):
            validate_jog_request(joint=0, direction=1, step_deg=2.5, period_s=0.5, start_deg=100)

    def test_step_and_period_bounds(self):
        for bad in (0, 11, -1):
            with self.subTest(step=bad), self.assertRaises(ValueError):
                validate_jog_request(joint=0, direction=1, step_deg=bad, period_s=0.5, start_deg=100)
        for bad in (0.05, 2.0):
            with self.subTest(period=bad), self.assertRaises(ValueError):
                validate_jog_request(joint=0, direction=1, step_deg=5, period_s=bad, start_deg=100)

    def test_direction_must_be_plus_or_minus_one(self):
        for bad in (0, 2, -2):
            with self.subTest(direction=bad), self.assertRaises(ValueError):
                validate_jog_request(joint=0, direction=bad, step_deg=5, period_s=0.5, start_deg=100)

    def test_start_angle_outside_the_firmware_travel_is_refused_not_clamped(self):
        with self.assertRaisesRegex(ValueError, "超出这个范围"):
            validate_jog_request(joint=0, direction=1, step_deg=5, period_s=0.5, start_deg=300)

    def test_booleans_are_not_accepted_as_numbers(self):
        """True 在 Python 里是 1：不挡的话 True 会变成"关节 1"，静默动错关节。"""
        with self.assertRaises(ValueError):
            validate_jog_request(joint=True, direction=1, step_deg=5, period_s=0.5, start_deg=100)


class NextTargetTests(unittest.TestCase):
    def test_steps_accumulate_in_the_requested_direction(self):
        session = live_session()
        session.note_step(158, {"success": True})
        self.assertEqual(session.next_target_deg(), 163)
        session.note_step(163, {"success": True})
        self.assertEqual(session.next_target_deg(), 168)

    def test_failed_step_does_not_move_our_belief_about_where_it_is(self):
        """失败的步不能推进"我们相信它在哪"——否则后面每一步都基于假前提。"""
        session = live_session()
        session.note_step(163, {"success": False, "error_code": 3020, "detail": "timeout"})
        self.assertEqual(session.last_target_deg, 158)
        self.assertEqual(session.steps_done, 1)
        self.assertEqual(session.last_result["error_code"], 3020)

    def test_boundary_stops_without_a_half_step(self):
        session = live_session()
        session.note_step(JOG_LIMIT_DEG[1] - 3, {"success": True})
        self.assertIsNone(session.next_target_deg())

    def test_lower_boundary_also_stops(self):
        session = session_about_to_start(direction=-1, start_deg=JOG_LIMIT_DEG[0] + 3)
        session.note_step(JOG_LIMIT_DEG[0] + 2, {"success": True})
        session.note_step(JOG_LIMIT_DEG[0], {"success": True})
        self.assertIsNone(session.next_target_deg())

    def test_session_dict_is_json_ready_and_carries_the_evidence(self):
        session = live_session()
        session.note_step(163, {"success": True, "duration_s": 0.3, "detail": "ok"})
        payload = session.as_dict(now=session.heartbeat_at + 1.0)
        self.assertTrue(payload["active"])
        self.assertEqual(payload["last_target_deg"], 163)
        self.assertEqual(payload["steps_done"], 1)
        self.assertEqual(payload["heartbeat_age_s"], 1.0)
        self.assertEqual(payload["steps"][0]["angle_deg"], 163)


class StopReasonTests(unittest.TestCase):
    def test_fresh_heartbeat_and_no_blockers_means_continue(self):
        session = live_session()
        self.assertIsNone(jog_stop_reason(session, 100.1, blockers=[]))

    def test_missing_heartbeat_stops_with_a_reason_that_says_why(self):
        session = live_session()
        now = session.heartbeat_at + JOG_HEARTBEAT_TIMEOUT_S + 0.01
        reason = jog_stop_reason(session, now, blockers=[])
        self.assertIn("心跳中断", reason)

    def test_safety_blocker_is_forwarded_with_its_evidence(self):
        session = live_session()
        blockers = [{"code": "EMERGENCY_STOP", "message": "急停已触发", "evidence": "emergency_stop=true"}]
        reason = jog_stop_reason(session, 100.1, blockers=blockers)
        self.assertIn("急停已触发", reason)
        self.assertIn("emergency_stop=true", reason)

    def test_existing_stop_reason_is_never_overwritten(self):
        session = live_session()
        session.stop_reason = "第一个原因"
        self.assertEqual(jog_stop_reason(session, 999.0, blockers=[]), "第一个原因")

    def test_joint_labels_cover_the_four_joints(self):
        self.assertEqual(joint_label(1), "肩")
        self.assertIn("不走 ARM_SET", joint_label(4))

    def test_unknown_joint_label_is_honest(self):
        self.assertIn("未知", joint_label(9))


class JogLoopTests(unittest.TestCase):
    """接线：后台步进循环真的会走步、并在该停的时候停。"""

    def _controller(self, results):
        controller = DashboardController(Mock(), Mock(), Mock(), Mock())
        controller.ros = Mock()
        controller.ros.arm_set_joint = Mock(side_effect=results)
        controller.console.snapshot.return_value = []
        controller.hub = Mock()
        controller.archive = Mock()
        controller.archive.append.side_effect = lambda kind, event: event
        return make_safety_ready(controller)

    def _run(self, controller, session):
        # 这一组测的是**步进循环本身**：安全状态会随真实时间过期，所以把 action_blockers
        # 固定成"无阻拦"。安全门本身另有专门测试（stop_reason 转发 + 启动被拒 + 机构抢占）。
        with patch("tools.field_dashboard.action_blockers", return_value=[]):
            asyncio.run(asyncio.wait_for(controller._arm_jog_loop(session), timeout=5))

    def test_steps_until_boundary_then_stops(self):
        ok = {"success": True, "error_code": 0, "detail": "ok"}
        controller = self._controller([ok, ok, ok])
        session = live_session(start_deg=JOG_LIMIT_DEG[1] - 10)
        self._run(controller, session)
        self.assertEqual(session.steps_done, 2)          # 260→265→270，再到边界就停
        self.assertEqual(session.last_target_deg, 270)
        self.assertIn("边界", session.stop_reason)

    def test_service_failure_stops_immediately_and_keeps_the_error(self):
        bad = {"success": False, "error_code": 3020, "detail": "arm timeout"}
        controller = self._controller([bad])
        session = live_session()
        self._run(controller, session)
        self.assertEqual(session.steps_done, 1)
        self.assertIn("3020", session.stop_reason)
        self.assertEqual(session.last_target_deg, 158)   # 失败不推进

    def test_ros_exception_is_reported_not_swallowed(self):
        controller = self._controller([RuntimeError("serial gone")])
        session = live_session()
        self._run(controller, session)
        self.assertIn("serial gone", session.stop_reason)

    def test_mechanism_busy_from_another_action_stops_the_loop(self):
        """别的动作抢了机构通道：停，并把原因写清（不是静默卡死）。"""
        ok = {"success": True, "error_code": 0, "detail": "ok"}
        controller = self._controller([ok, ok])
        controller.mechanism_busy = True          # 模拟循环外部有人占了机构
        session = live_session()
        self._run(controller, session)
        self.assertEqual(session.steps_done, 0)
        self.assertIn("机构动作在执行", session.stop_reason)


class JogRouteTests(unittest.TestCase):
    def _controller(self):
        controller = DashboardController(Mock(), Mock(), Mock(), Mock())
        controller.ros = Mock()
        controller.ros.arm_set_joint = Mock(return_value={"success": True, "error_code": 0, "detail": "ok"})
        controller.console.snapshot.return_value = []
        controller.archive = Mock()
        controller.archive.append.side_effect = lambda kind, event: event
        controller.hub = Mock()
        return controller

    def test_start_refuses_when_a_safety_blocker_is_present(self):
        controller = self._controller()
        with self.assertRaisesRegex(ValueError, "物理启动未授权|尚未收到"):
            asyncio.run(controller.action("/api/arm/jog/start", {
                "joint": 1, "direction": 1, "step_deg": 5, "period_s": 0.5, "start_deg": 158,
            }))
        self.assertIsNone(controller.arm_jog)

    def test_start_with_ready_safety_creates_a_session(self):
        controller = make_safety_ready(self._controller())
        with patch("tools.field_dashboard.action_blockers", return_value=[]):
            result = asyncio.run(controller.action("/api/arm/jog/start", {
                "joint": 1, "direction": 1, "step_deg": 5, "period_s": 0.5, "start_deg": 158,
            }))
        self.assertTrue(result["ok"])
        self.assertIsNotNone(controller.arm_jog)
        self.assertEqual(controller.arm_jog.request.start_deg, 158)
        self.assertEqual(controller.arm_jog.request.joint, 1)
        self.assertIn("肩", result["plan"])
        # 循环是"按下就动"：任务立刻开始走步，所以这里**不断言**当前目标角度
        # （它取决于事件循环调度，断言它会把测试变成时序竞态）。

    def test_start_refuses_while_chassis_is_moving(self):
        controller = self._controller()
        controller.state.mode = "MANUAL"
        with self.assertRaisesRegex(ValueError, "底盘运动模式"):
            asyncio.run(controller.action("/api/arm/jog/start", {
                "joint": 1, "direction": 1, "step_deg": 5, "period_s": 0.5, "start_deg": 158,
            }))

    def test_heartbeat_and_stop_reject_a_foreign_token(self):
        controller = self._controller()
        session = live_session()
        controller.arm_jog = session
        with self.assertRaisesRegex(ValueError, "不属于当前页面"):
            asyncio.run(controller.action("/api/arm/jog/heartbeat", {"token": "other"}))
        with self.assertRaisesRegex(ValueError, "令牌不匹配"):
            asyncio.run(controller.action("/api/arm/jog/stop", {"token": "other"}))

    def test_snapshot_exposes_limits_joints_and_safe_pose(self):
        controller = self._controller()
        payload = controller.snapshot()["arm_jog"]
        self.assertEqual(payload["limits_deg"], list(JOG_LIMIT_DEG))
        self.assertEqual([item["id"] for item in payload["joints"]], [0, 1, 2, 3])
        self.assertEqual(payload["joints"][1]["safe_pose_deg"], JOG_SAFE_POSE_DEG[1])
        self.assertIn("无位置反馈", payload["note"])

    def test_service_timeout_is_generous_enough_for_one_step(self):
        """10° 约 0.22s @45°/s；超时给太紧会把正常的慢动作误判成失败。"""
        self.assertGreaterEqual(JOG_SERVICE_TIMEOUT_S, 3.0)


class PanelWiringTests(unittest.TestCase):
    from pathlib import Path as _Path

    WEB = _Path(__file__).resolve().parents[1] / "tools" / "field_dashboard_web"

    def test_index_html_loads_panel_before_app(self):
        page = (self.WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="/arm_jog_panel.js"', page)
        self.assertLess(
            page.index('src="/arm_jog_panel.js"'), page.index('src="/app.js"'),
            "arm_jog_panel.js 必须在 app.js 之前加载，否则 renderArmJog 未定义",
        )

    def test_app_js_renders_the_panel(self):
        script = (self.WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("renderArmJog(s)", script)

    def test_panel_posts_only_the_three_jog_routes(self):
        script = (self.WEB / "arm_jog_panel.js").read_text(encoding="utf-8")
        for route in ("/api/arm/jog/start", "/api/arm/jog/heartbeat", "/api/arm/jog/stop"):
            self.assertIn(route, script)
        # 面板不许自己拼 ARM_SET：角度累加与安全门都在后端，前端只发意图。
        self.assertNotIn("/api/arm/set_joint", script)

    def test_panel_says_the_angle_is_not_measured(self):
        script = (self.WEB / "arm_jog_panel.js").read_text(encoding="utf-8")
        self.assertIn("没有位置反馈", script)

    def test_server_actually_serves_the_panel_file(self):
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.request import urlopen

        from tools.field_dashboard import DashboardHandler

        class _App:
            def snapshot(self):
                return {"mode": "OBSERVE"}

            def record(self, event, **_kwargs):
                pass

        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()
        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        server.app = _App()
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/arm_jog_panel.js", timeout=3) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
            self.assertIn("jogView", body)
        finally:
            server.shutdown()
            server.server_close()
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
