"""真伪与实例核查：判据来自哪儿、拦住什么、不拦住什么。

这些测试都在**离线**跑（没有 rclpy、没有树莓派、没有第二个进程）：
被检查的三样观测——话题发布者、`/robot/status.detail` 样本、`ps` 输出——
都由测试直接喂进去。因此这里证明的是**判定与措辞**，
不能证明真车上真的会出现这些观测。
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import unittest

from tools.field_dashboard import EXPECTED_RUNTIME_SOURCE, DashboardController
from tools.field_runtime_guard import (
    classify_command,
    console_name,
    guard_report,
    parse_process_listing,
    source_state,
    split_instances,
    start_block_reason,
    topic_rows,
)

#: 一份「整栈在 SSH 里跑着」的 ps 输出（Ubuntu `ps -eo pid=,pgid=,args=` 的形状）。
STACK_PS = """
  1234  1234 /usr/bin/python3 /opt/ros/jazzy/bin/ros2 launch robogame_bringup hardware.launch.py
  1240  1234 /usr/bin/python3 /home/rg26/robogame/install/robot_bridge/lib/robot_bridge/robot_bridge --ros-args
  1241  1234 /usr/bin/python3 /home/rg26/robogame/install/mission_manager/lib/mission_manager/mission_manager
  1300  1300 /usr/bin/python3 /home/rg26/robogame/tools/field_dashboard.py
"""

#: 一份「网页自己起过 bridge」的 ps 输出：控制台记的是 shell 的 PID（2000），
#: 真正的节点是它的子进程（2001），**PID 不同、进程组相同**。
OWN_CONSOLE_PS = """
  2000  2000 /bin/sh -c ros2 run robot_bridge robot_bridge --ros-args --params-file robot.yaml
  2001  2000 /usr/bin/python3 /home/rg26/robogame/install/robot_bridge/lib/robot_bridge/robot_bridge --ros-args
"""

REAL_DETAIL = "decoded MCU V1 STATUS"
MOCK_DETAIL = "mock hardware"


def healthy_topics():
    return {
        "/robot/status": ["robot_bridge"],
        "/wheel_odom": ["robot_bridge"],
        "/imu/data": ["robot_bridge"],
        "/line_sensor": ["robot_bridge"],
        "/cmd_vel": ["field_dashboard", "line_follow_controller"],
    }


class ProcessListingTests(unittest.TestCase):
    def test_pid_and_pgid_are_both_read(self):
        processes = parse_process_listing(STACK_PS, with_pgid=True)
        self.assertEqual([item["pid"] for item in processes], [1234, 1240, 1241, 1300])
        self.assertEqual({item["pgid"] for item in processes}, {1234, 1300})

    def test_unparsable_lines_are_dropped_not_guessed(self):
        text = "  12  12 /bin/ok\nnot a ps line\n  77  /bin/no-pgid\n\n"
        processes = parse_process_listing(text, with_pgid=True)
        self.assertEqual([item["pid"] for item in processes], [12])

    def test_command_classification_matches_each_known_node(self):
        self.assertEqual(classify_command("/x/line_follow_controller --ros-args"), "line_follow_controller")
        self.assertEqual(classify_command("/x/line_sensor_mock"), "line_sensor_mock")
        self.assertEqual(classify_command("/x/motion_controller"), "motion_controller")
        self.assertEqual(classify_command("/bin/sh -c ls"), None)
        self.assertEqual(console_name("line_follow_controller"), "line")
        self.assertEqual(console_name("hardware.launch.py"), None)

    def test_ownership_uses_process_group_not_pid(self):
        """控制台记 shell 的 PID，节点是子进程：只比 PID 会误报成外部实例。"""
        processes = parse_process_listing(OWN_CONSOLE_PS, with_pgid=True)
        split = split_instances(processes, managed_pids=[2000], managed_pgids=[2000])
        self.assertEqual([item["pid"] for item in split["managed"]], [2000, 2001])
        self.assertEqual(split["external"], [])

    def test_unknown_processes_are_in_neither_bucket(self):
        processes = parse_process_listing(STACK_PS, with_pgid=True)
        split = split_instances(processes, managed_pids=[], managed_pgids=[])
        self.assertNotIn(1300, [item["pid"] for item in split["managed"] + split["external"]])


class SourceStateTests(unittest.TestCase):
    def test_real_mock_unknown_and_empty_are_distinct(self):
        self.assertEqual(source_state([REAL_DETAIL])["kind"], "real")
        self.assertEqual(source_state([MOCK_DETAIL])["kind"], "mock")
        self.assertEqual(source_state(["something new"])["kind"], "unknown")
        self.assertEqual(source_state([])["kind"], "none")

    def test_both_sources_together_is_a_conflict_not_a_majority_vote(self):
        state = source_state([MOCK_DETAIL, REAL_DETAIL, REAL_DETAIL])
        self.assertEqual(state["kind"], "mixed")
        self.assertEqual(state["observed"], ["mock", "field"])

    def test_serial_port_alive_but_no_status_is_its_own_case(self):
        """串口在、没解码到状态：既不是 mock，也不是「还没收到」，必须分开说。"""
        detail = "MCU transport inactive; decoded RobotStatus unavailable"
        state = source_state([detail])
        self.assertEqual(state["kind"], "real")
        self.assertTrue(state["transport_without_status"])


class TopicRowTests(unittest.TestCase):
    def test_dashboard_itself_is_not_counted_on_cmd_vel(self):
        rows = {row["topic"]: row for row in topic_rows(healthy_topics())}
        self.assertEqual(rows["/cmd_vel"]["publishers"], ["line_follow_controller"])
        self.assertEqual(rows["/cmd_vel"]["level"], "ok")

    def test_two_publishers_on_a_critical_topic_is_a_block(self):
        topics = healthy_topics() | {"/robot/status": ["robot_bridge", "robot_bridge_2"]}
        rows = {row["topic"]: row for row in topic_rows(topics)}
        self.assertEqual(rows["/robot/status"]["level"], "block")
        self.assertEqual(rows["/robot/status"]["count"], 2)

    def test_non_bridge_publisher_on_critical_topic_is_a_block(self):
        topics = healthy_topics() | {"/imu/data": ["imu_fake"]}
        rows = {row["topic"]: row for row in topic_rows(topics)}
        self.assertEqual(rows["/imu/data"]["level"], "block")

    def test_missing_observation_is_info_not_a_silent_zero(self):
        rows = {row["topic"]: row for row in topic_rows({})}
        self.assertEqual(rows["/robot/status"]["level"], "info")
        self.assertEqual(rows["/robot/status"]["count"], 0)

    def test_junk_observation_does_not_crash_the_page(self):
        rows = topic_rows({"/robot/status": Mock()})
        self.assertEqual(rows[0]["publishers"], [])


class GuardReportTests(unittest.TestCase):
    def test_healthy_real_stack_has_no_blockers(self):
        report = guard_report(
            topic_publishers=healthy_topics(), status_details=[REAL_DETAIL],
            processes=parse_process_listing(OWN_CONSOLE_PS, with_pgid=True),
            managed_pids=[2000], managed_pgids=[2000],
        )
        self.assertEqual(report["verdict"], "ok")
        self.assertEqual(report["blocked_starts"], [])
        self.assertEqual(report["source"]["kind"], "real")

    def test_mock_is_shown_as_mock_and_never_as_healthy_hardware(self):
        report = guard_report(
            topic_publishers=healthy_topics(), status_details=[MOCK_DETAIL],
        )
        codes = [item["code"] for item in report["findings"]]
        self.assertIn("MOCK_OBSERVED", codes)
        self.assertEqual(report["source"]["kind"], "mock")
        self.assertIn("模拟", report["headline"])
        # 期望来源就是 field 的时候，mock 只能算警告：现场要能手动联调。
        self.assertEqual(report["verdict"], "warn")
        self.assertEqual(report["blocked_starts"], [])

    def test_duplicate_publisher_blocks_starting_bridge(self):
        report = guard_report(
            topic_publishers=healthy_topics() | {"/robot/status": ["robot_bridge", "robot_bridge_2"]},
            status_details=[REAL_DETAIL],
        )
        self.assertEqual(report["verdict"], "block")
        self.assertIn("bridge", report["blocked_starts"])
        reason = start_block_reason(report, "bridge")
        self.assertIn("2 个发布者", reason)
        self.assertIn("robot_bridge_2", reason)

    def test_external_instance_blocks_only_its_own_name(self):
        """SSH 里起了整栈：网页不许再起同名节点，但其它节点照常可用。"""
        report = guard_report(
            topic_publishers=healthy_topics(), status_details=[REAL_DETAIL],
            processes=parse_process_listing(STACK_PS, with_pgid=True),
            managed_pids=[], managed_pgids=[],
        )
        self.assertIn("bridge", report["blocked_starts"])
        self.assertIn("mission", report["blocked_starts"])
        self.assertIsNone(start_block_reason(report, "arm"))
        self.assertIsNone(start_block_reason(report, "line"))
        codes = [item["code"] for item in report["findings"]]
        self.assertIn("STACK_RUNNING", codes)
        self.assertIn("EXTERNAL_INSTANCE", codes)
        reason = start_block_reason(report, "mission")
        self.assertIn("pid=1241", reason)

    def test_managed_nodes_are_not_reported_as_external(self):
        report = guard_report(
            topic_publishers=healthy_topics(), status_details=[REAL_DETAIL],
            processes=parse_process_listing(OWN_CONSOLE_PS, with_pgid=True),
            managed_pids=[2000], managed_pgids=[2000],
        )
        self.assertEqual([item["code"] for item in report["findings"]], [])

    def test_line_is_blocked_when_two_line_sources_publish(self):
        report = guard_report(
            topic_publishers=healthy_topics() | {"/line_sensor": ["robot_bridge", "line_sensor_mock"]},
            status_details=[REAL_DETAIL],
        )
        self.assertIn("line", report["blocked_starts"])
        self.assertIn("DUP_OPTIONAL_PUBLISHER", [item["code"] for item in report["findings"]])

    def test_no_status_yet_is_a_warning_that_says_the_green_lights_mean_nothing(self):
        report = guard_report(topic_publishers={}, status_details=[])
        self.assertIn("NO_STATUS", [item["code"] for item in report["findings"]])
        self.assertEqual(report["verdict"], "warn")

    def test_missing_process_scan_is_reported_as_ignorance(self):
        report = guard_report(
            topic_publishers=healthy_topics(), status_details=[REAL_DETAIL],
            processes=[], process_scan_available=False,
        )
        note = next(item for item in report["findings"] if item["code"] == "NO_PROCESS_SCAN")
        self.assertIn("看不到", note["message"])
        self.assertEqual(report["blocked_starts"], [])


class ControllerWiringTests(unittest.TestCase):
    """接线：网页快照要带上核查结论，启动前真的会被拒绝。"""

    def _controller(self, *, processes, managed, topics, details, available=True):
        """只喂观测、不碰 ROS 与真实进程表的控制器。

        `owned_process_groups` 被替换掉是**有意**的：它内部要调 `os.getpgid`，
        而测试里的 PID 是编的——真去查会随运行环境漂移（甚至查到别人的进程组），
        于是测试结果取决于跑测试那台机器上恰好有哪些进程。
        """
        controller = DashboardController(Mock(), Mock(), Mock(), Mock())
        controller.ros = Mock()
        controller.ros.publisher_map.return_value = topics
        controller.ros.status_details.return_value = details
        controller.console.snapshot.return_value = managed
        controller.owned_process_groups = lambda: ([], [])
        return controller, controller._build_runtime_check(processes, available, "")

    def test_snapshot_carries_the_check(self):
        controller = DashboardController(Mock(), Mock(), Mock(), Mock())
        controller.ros = Mock()
        controller.ros.publisher_map.return_value = healthy_topics()
        controller.ros.status_details.return_value = [REAL_DETAIL]
        controller.console.snapshot.return_value = []
        self.assertIn("runtime_check", controller.snapshot())

    def test_expected_source_is_field_because_the_web_starts_the_field_layer(self):
        self.assertEqual(EXPECTED_RUNTIME_SOURCE, "field")

    def test_external_bridge_is_reported_for_the_bridge_button(self):
        _controller, report = self._controller(
            processes=parse_process_listing(STACK_PS, with_pgid=True),
            managed=[], topics=healthy_topics(), details=[REAL_DETAIL],
        )
        self.assertIn("bridge", report["blocked_starts"])
        self.assertIsNotNone(start_block_reason(report, "bridge"))


class ActionGateTests(unittest.TestCase):
    """防御的落点：`/api/process/<name>/start` 在双实例时**必须**发不出去。

    只证明判据函数会给出结论是不够的——要证明那个结论真的接在了启动路径上。
    """

    def _controller(self, report):
        controller = DashboardController(Mock(), Mock(), Mock(), Mock())
        controller.console.snapshot.return_value = []
        controller.console.start = AsyncMock()   # 没被调用 = 真的没起进程
        async def fixed_check(*, force_refresh=True):
            return report
        controller.runtime_check_async = fixed_check
        return controller

    def _report(self, *, blocked):
        return guard_report(
            topic_publishers=healthy_topics() if not blocked
            else healthy_topics() | {"/robot/status": ["robot_bridge", "robot_bridge_2"]},
            status_details=[REAL_DETAIL],
        )

    def test_start_is_refused_and_no_process_is_spawned(self):
        controller = self._controller(self._report(blocked=True))
        with self.assertRaises(ValueError) as caught:
            asyncio.run(controller.action("/api/process/bridge/start", {}))
        self.assertIn("拒绝启动 bridge", str(caught.exception))
        self.assertIn("2 个发布者", str(caught.exception))
        controller.console.start.assert_not_awaited()

    def test_normal_start_still_works(self):
        """防御不能把正常联调一起拦掉——现场需要能手动起节点。"""
        controller = self._controller(self._report(blocked=False))
        asyncio.run(controller.action("/api/process/bridge/start", {}))
        controller.console.start.assert_awaited_once_with("bridge")


class PanelWiringTests(unittest.TestCase):
    """接线层：卡片在页面上真的会加载、真的会渲染、真的会点不动按钮。

    功能正确但没接上 = 现场看不到，所以这几条与判据本身同等重要。
    """

    WEB = Path(__file__).resolve().parents[1] / "tools" / "field_dashboard_web"

    def test_index_html_loads_panel_before_app(self):
        page = (self.WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="/runtime_guard.js"', page)
        self.assertLess(
            page.index('src="/runtime_guard.js"'), page.index('src="/app.js"'),
            "runtime_guard.js 必须在 app.js 之前加载，否则 renderRuntimeGuard 未定义",
        )

    def test_app_js_calls_render_after_the_process_list_is_rebuilt(self):
        """顺序有语义：app.js 每次刷新都重建「进程」按钮，禁用必须发生在重建之后。"""
        script = (self.WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("renderRuntimeGuard(s)", script)
        self.assertLess(
            script.index("renderOdom(s)"), script.index("renderRuntimeGuard(s)"),
            "renderRuntimeGuard 要挂在 refresh 渲染链的最后",
        )

    def test_panel_is_read_only(self):
        """核查卡片只显示与禁用按钮：它自己不许发命令（证据面板不能变成操作面板）。"""
        script = (self.WEB / "runtime_guard.js").read_text(encoding="utf-8")
        for forbidden in ("fetch(", "XMLHttpRequest", "POST"):
            self.assertNotIn(forbidden, script, f"核查卡片不应出现 {forbidden}")

    def test_server_actually_serves_the_panel_file(self):
        import asyncio
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
            with urlopen(
                f"http://127.0.0.1:{server.server_port}/runtime_guard.js", timeout=3
            ) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
            self.assertIn("runtimeGuardView", body)
        finally:
            server.shutdown()
            server.server_close()
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
