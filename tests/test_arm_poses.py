"""姿态示教与回放：记录什么、按什么顺序复现、什么时候停下。

全部离线跑：不连 ROS、不连硬件。被检查的输入（命令历史、安全门、固件回执）都由
测试直接喂进去。因此这里证明的是**判定、顺序与措辞**，不能证明舵机真到了那个角度。
"""

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from robogame_core.arm_poses import (
    POSE_JOINTS,
    POSE_LIMIT_DEG,
    SAFE_POSE_NAME,
    ArmPoseRecord,
    PoseTable,
    builtin_pose_table,
    load_pose_table,
    record_from_command_history,
    replay_plan,
    safe_pose_angles,
    save_pose_table,
    validate_pose_angles,
    validate_pose_name,
)
from tools.field_dashboard import DashboardController


def make_safety_ready(controller):
    safety = controller.state.safety
    safety.received_at = time.monotonic()
    safety.communication_ok = True
    safety.physical_start = True
    safety.emergency_stop = False
    safety.mechanism_fault = False
    return controller


class SafePoseTests(unittest.TestCase):
    def test_safe_pose_angles_come_from_the_firmware_pulse_constants(self):
        angles = safe_pose_angles()
        self.assertEqual(sorted(angles), list(POSE_JOINTS))
        # 这四个数字是用 arm.py 的脉宽→角度换算算出来的，不是手抄的；
        # 固件常量变了它们就该变（test_arm_firmware_sync 保证常量本身与 arm.c 一致）。
        self.assertEqual(angles, {0: 131, 1: 158, 2: 135, 3: 25})

    def test_builtin_table_has_the_safe_pose_and_says_it_is_not_measured(self):
        table = builtin_pose_table()
        self.assertEqual(table.names(), (SAFE_POSE_NAME,))
        record = table.get(SAFE_POSE_NAME)
        self.assertIn("非实测", record.note)
        self.assertEqual(record.angles, safe_pose_angles())


class ValidatePoseTests(unittest.TestCase):
    def test_names_are_trimmed_and_bounded(self):
        self.assertEqual(validate_pose_name("  抓取位 "), "抓取位")
        for bad in ("", "   ", "x" * 25, 123):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                validate_pose_name(bad)

    def test_angles_must_be_whole_degrees_inside_the_firmware_travel(self):
        self.assertEqual(validate_pose_angles({1: 158}), {1: 158})
        for bad in ({1: 158.5}, {1: 300}, {1: -5}):
            with self.subTest(angles=bad), self.assertRaises(ValueError):
                validate_pose_angles(bad)

    def test_gripper_is_refused_with_the_right_way_to_do_it(self):
        with self.assertRaisesRegex(ValueError, "爪子不进姿态表"):
            validate_pose_angles({4: 100})

    def test_unknown_joint_is_refused(self):
        with self.assertRaisesRegex(ValueError, "关节编号只能是"):
            validate_pose_angles({9: 100})

    def test_empty_pose_is_refused(self):
        with self.assertRaisesRegex(ValueError, "至少要有一个关节"):
            validate_pose_angles({})


class RecordFromHistoryTests(unittest.TestCase):
    def test_last_command_per_joint_wins_and_order_is_the_touch_order(self):
        record = record_from_command_history(
            name="右侧抓取位",
            history=[(1, 158), (0, 131), (1, 150), (2, 120)],
            note="示教",
        )
        self.assertEqual(record.angles, {1: 150, 0: 131, 2: 120})
        self.assertEqual(record.order, (0, 1, 2))   # 1 又被动过，挪到 0 之后

    def test_replay_steps_follow_the_recorded_order(self):
        record = record_from_command_history(name="p", history=[(2, 100), (1, 120)])
        self.assertEqual([step["joint"] for step in record.replay_steps()], [2, 1])
        plan = replay_plan(record)
        self.assertEqual([step["index"] for step in plan], [0, 1])
        self.assertEqual([step["angle_deg"] for step in plan], [100, 120])

    def test_a_joint_missing_from_the_declared_order_is_appended_not_dropped(self):
        record = ArmPoseRecord(name="p", angles={0: 131, 1: 158}, order=(1,))
        self.assertEqual(record.order, (1, 0))

    def test_order_mentioning_an_unknown_joint_is_refused(self):
        with self.assertRaisesRegex(ValueError, "姿态中没有的关节"):
            ArmPoseRecord(name="p", angles={0: 131}, order=(0, 3))

    def test_negative_settle_is_refused(self):
        record = record_from_command_history(name="p", history=[(1, 158)])
        with self.assertRaises(ValueError):
            replay_plan(record, settle_s=-1.0)


class PersistenceTests(unittest.TestCase):
    def test_round_trip_keeps_poses_and_adds_the_builtin_safe_pose(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "poses.json"
            table = PoseTable()
            table.upsert(record_from_command_history(
                name="抓取位", history=[(1, 150), (0, 131)], note="x",
            ))
            save_pose_table(path, table)
            loaded = load_pose_table(path)
            self.assertIn(SAFE_POSE_NAME, loaded.names())
            self.assertIn("抓取位", loaded.names())
            self.assertEqual(loaded.get("抓取位").angles, {1: 150, 0: 131})
            self.assertEqual(loaded.get("抓取位").order, (1, 0))

    def test_missing_file_returns_the_builtin_table_without_error(self):
        with tempfile.TemporaryDirectory() as directory:
            loaded = load_pose_table(Path(directory) / "nope.json")
            self.assertEqual(loaded.names(), (SAFE_POSE_NAME,))

    def test_corrupt_file_raises_instead_of_silently_returning_builtins(self):
        """静默退回出厂表会让人以为"我记的姿态还在"——那比报错更糟。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "poses.json"
            path.write_text("[1,2,3]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_pose_table(path)

    def test_save_is_atomic_leftover_tmp_is_not_a_half_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "poses.json"
            save_pose_table(path, builtin_pose_table())
            self.assertTrue(path.exists())
            self.assertFalse((Path(directory) / "poses.json.tmp").exists())
            json.loads(path.read_text(encoding="utf-8"))


class ControllerPoseTests(unittest.TestCase):
    def _controller(self, directory):
        controller = DashboardController(Mock(), Mock(), Mock(), Mock())
        controller.ros = Mock()
        controller.ros.arm_set_joint = Mock(
            return_value={"success": True, "error_code": 0, "detail": "ok"}
        )
        controller.console.snapshot.return_value = []
        controller.archive = Mock()
        controller.archive.append.side_effect = lambda kind, event: event
        controller.hub = Mock()
        controller.pose_file = str(Path(directory) / "poses.json")
        controller.poses = load_pose_table(controller.pose_file)
        return make_safety_ready(controller)

    def test_commanded_angles_start_at_the_safe_pose(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(directory)
            self.assertEqual(controller.arm_commanded_deg, safe_pose_angles())

    def test_record_uses_the_command_history_and_writes_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(directory)
            controller._note_arm_command(0, 131)
            controller._note_arm_command(1, 150)
            result = asyncio.run(controller.action(
                "/api/arm/pose/record", {"name": "抓取位", "note": "第一次示教"}
            ))
            self.assertTrue(result["ok"])
            self.assertEqual(result["pose"]["angles"], {"0": 131, "1": 150})
            self.assertTrue(Path(result["file"]).exists())
            again = load_pose_table(controller.pose_file)
            self.assertIn("抓取位", again.names())

    def test_record_refuses_an_empty_name(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(directory)
            with self.assertRaises(ValueError):
                asyncio.run(controller.action("/api/arm/pose/record", {"name": "  "}))

    def test_home_success_resets_the_belief_to_the_safe_pose(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(directory)
            controller._note_arm_command(1, 200)
            controller.ros.mechanism = Mock(return_value={"success": True, "detail": "ok"})
            with patch("tools.field_dashboard.action_blockers", return_value=[]):
                asyncio.run(controller.action("/api/mechanism/home", {}))
            self.assertEqual(controller.arm_commanded_deg, safe_pose_angles())

    def test_replay_of_an_unknown_pose_lists_what_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(directory)
            with self.assertRaisesRegex(ValueError, "安全姿态"):
                asyncio.run(controller.action("/api/arm/pose/replay", {"name": "没有这个"}))

    def test_replay_is_refused_while_jogging(self):
        with tempfile.TemporaryDirectory() as directory:
            from tools.arm_jog import JogSession, validate_jog_request
            controller = self._controller(directory)
            request = validate_jog_request(
                joint=1, direction=1, step_deg=5, period_s=0.5, start_deg=158
            )
            controller.arm_jog = JogSession(
                request=request, token="t", started_at=0.0, heartbeat_at=time.monotonic(),
                last_target_deg=158,
            )
            with self.assertRaisesRegex(ValueError, "关节微调正在进行"):
                asyncio.run(controller.action("/api/arm/pose/replay", {"name": SAFE_POSE_NAME}))

    def test_snapshot_exposes_poses_limits_and_the_honest_note(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(directory)
            payload = controller.snapshot()["arm_poses"]
            self.assertEqual(payload["limits_deg"], list(POSE_LIMIT_DEG))
            self.assertIn(SAFE_POSE_NAME, payload["names"])
            self.assertIn("不是测量值", payload["note"])
            self.assertEqual(payload["replay"], None)


class ReplayLoopTests(unittest.TestCase):
    """回放循环：串行、失败即停、安全门拦住就停、成功才推进"我们相信它在哪"。"""

    def _controller(self, directory, results):
        controller = DashboardController(Mock(), Mock(), Mock(), Mock())
        controller.ros = Mock()
        controller.ros.arm_set_joint = Mock(side_effect=results)
        controller.console.snapshot.return_value = []
        controller.archive = Mock()
        controller.archive.append.side_effect = lambda kind, event: event
        controller.hub = Mock()
        controller.pose_file = str(Path(directory) / "poses.json")
        controller.poses = load_pose_table(controller.pose_file)
        controller.poses.upsert(record_from_command_history(
            name="两点位", history=[(1, 150), (2, 120)]
        ))
        return make_safety_ready(controller)

    def _replay_directly(self, controller, name="两点位"):
        """直接驱动回放循环（不经路由）。

        为什么不先调 `/api/arm/pose/replay` 再手动跑循环：那条路由会**自己起一个后台任务**，
        于是同一个计划被两条循环同时执行（命令翻倍），而且断言拿到的是字典副本——
        第一次写这组测试就踩了这个坑。
        """
        record = controller.poses.get(name)
        state = {
            "name": name, "active": True, "started_at": time.monotonic(),
            "finished_at": None, "steps_done": 0, "plan": replay_plan(record),
            "steps": [], "last_result": None, "stop_reason": None,
        }
        controller.arm_replay = state
        with patch("tools.field_dashboard.action_blockers", return_value=[]):
            asyncio.run(controller._arm_replay_loop(name, record, state["plan"]))
        return state

    def test_steps_run_in_recorded_order_and_update_the_belief(self):
        ok = {"success": True, "error_code": 0, "detail": "ok"}
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(directory, [ok, ok])
            state = self._replay_directly(controller)
            self.assertEqual(state["steps_done"], 2)
            self.assertEqual(state["stop_reason"], "回放完成")
            self.assertFalse(state["active"])
            self.assertEqual(controller.arm_commanded_deg[1], 150)
            self.assertEqual(controller.arm_commanded_deg[2], 120)
            called = [call.args[0] for call in controller.ros.arm_set_joint.call_args_list]
            self.assertEqual(called, [1, 2])          # 按示教顺序，不是按关节编号

    def test_first_failure_stops_and_the_rest_is_not_sent(self):
        ok = {"success": True, "error_code": 0, "detail": "ok"}
        bad = {"success": False, "error_code": 3020, "detail": "arm timeout"}
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(directory, [bad, ok])
            state = self._replay_directly(controller)
            self.assertEqual(state["steps_done"], 1)
            self.assertIn("3020", state["stop_reason"])
            self.assertIn("后面的关节没有下发", state["stop_reason"])
            self.assertEqual(controller.ros.arm_set_joint.call_count, 1)
            # 失败的那一步不推进"我们相信它在哪"
            self.assertEqual(controller.arm_commanded_deg[1], safe_pose_angles()[1])

    def test_replay_route_creates_the_state_and_the_plan(self):
        """路由只负责"建状态 + 起计划"；它是否已经跑完取决于调度，断言那些会飘。

        （第一次写这条时断言了 `active is True`：mock 下两步瞬间跑完，断言必红。）
        """
        ok = {"success": True, "error_code": 0, "detail": "ok"}
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(directory, [ok, ok])
            with patch("tools.field_dashboard.action_blockers", return_value=[]):
                started = asyncio.run(controller.action("/api/arm/pose/replay", {"name": "两点位"}))
            self.assertEqual(started["steps"], 2)
            self.assertEqual(started["name"], "两点位")
            self.assertEqual(
                [step["joint"] for step in controller.arm_replay["plan"]], [1, 2]
            )
            self.assertEqual(controller.arm_replay["name"], "两点位")
            self.assertIn("两点位", started["description"])
            asyncio.run(controller.action("/api/arm/pose/stop", {}))

    def test_safety_blocker_stops_before_any_step(self):
        ok = {"success": True, "error_code": 0, "detail": "ok"}
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(directory, [ok])
            controller.state.safety.emergency_stop = True

            async def run():
                record = controller.poses.get("两点位")
                controller.arm_replay = {
                    "name": "两点位", "active": True, "started_at": time.monotonic(),
                    "steps_done": 0, "plan": replay_plan(record), "steps": [],
                    "last_result": None, "stop_reason": None, "finished_at": None,
                }
                await controller._arm_replay_loop("两点位", record, replay_plan(record))

            asyncio.run(run())
            self.assertEqual(controller.ros.arm_set_joint.call_count, 0)
            self.assertIn("安全门拦截", controller.arm_replay["stop_reason"])

    def test_stop_cancels_a_running_replay(self):
        ok = {"success": True, "error_code": 0, "detail": "ok"}
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(directory, [ok, ok])
            with patch("tools.field_dashboard.action_blockers", return_value=[]):
                asyncio.run(controller.action("/api/arm/pose/replay", {"name": "两点位"}))
            result = asyncio.run(controller.action("/api/arm/pose/stop", {}))
            self.assertTrue(result["ok"])


class PanelWiringTests(unittest.TestCase):
    WEB = Path(__file__).resolve().parents[1] / "tools" / "field_dashboard_web"

    def test_index_html_loads_panel_before_app(self):
        page = (self.WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="/arm_pose_panel.js"', page)
        self.assertLess(
            page.index('src="/arm_pose_panel.js"'), page.index('src="/app.js"'),
            "arm_pose_panel.js 必须在 app.js 之前加载，否则 renderArmPoses 未定义",
        )

    def test_app_js_renders_the_panel(self):
        script = (self.WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("renderArmPoses(s)", script)

    def test_panel_talks_only_to_the_pose_and_home_routes(self):
        script = (self.WEB / "arm_pose_panel.js").read_text(encoding="utf-8")
        for route in ("/api/arm/pose/record", "/api/arm/pose/replay",
                      "/api/arm/pose/stop", "/api/arm/pose/delete", "/api/mechanism/home"):
            self.assertIn(route, script)
        self.assertNotIn("/api/arm/set_joint", script)

    def test_panel_says_the_recorded_angle_is_not_measured(self):
        script = (self.WEB / "arm_pose_panel.js").read_text(encoding="utf-8")
        self.assertIn("不是测量值", script)

    def test_server_serves_the_panel_file(self):
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
            with urlopen(f"http://127.0.0.1:{server.server_port}/arm_pose_panel.js", timeout=3) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
            self.assertIn("poseView", body)
        finally:
            server.shutdown()
            server.server_close()
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
