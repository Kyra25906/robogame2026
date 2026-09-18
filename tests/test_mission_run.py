"""任务运行循环的行为测试（纯逻辑，零 ROS）。

`MissionRun` 是从 ROS 节点里抽出来的运行逻辑（段切换、重试重发、作业步骤、授权、
目标、限速/坡道/转弯命令、网页载荷）。抽出来的价值就在这里：这些行为原来只能用
**AST 结构断言**守着，现在可以**真的跑一遍**。

覆盖的关键行为（每条都对应一种现场会真实发生的失败）：

1. 顺序：先给目标再授权（否则位姿控制器会在没有目标时被授权）；
2. 同一步的机构命令只发一次（否则会重复抓/重复放）；
3. 本段重试必须重发命令（巡线节点打滑卡住后是「零速+等命令」，不重发叫不醒它）；
4. 坡道 STUCK 连续上报 → 按失败重试本段；
5. 巡线状态断流 → 段不推进（不许在无观测时前进）；
6. 结果来源必须匹配当前步骤（侧移成功不能被记成抓了一块）；
7. 终态 → 释放授权 + 清除残留命令；
8. 载荷字段齐全（网页读得到）。
"""

from __future__ import annotations

import json
import unittest

from robogame_core.authorization import SOURCE_NONE
from robogame_core.line_follow import LineSensorState
from robogame_core.mission import MissionConfig, MissionMachine, MissionState
from robogame_core.mission_dispatch import LineStatus
from robogame_core.mission_route import build_route_plan
from robogame_core.mission_run import CommandKind, MissionRun
from robogame_core.models import MissionResult, Pose2D
from robogame_core.ramp_control import SlipDecision
from robogame_core.route_loader import load_survey
from robogame_core.work_sequence import WorkStepKind


def _plan(rounds: int = 1):
    return build_route_plan(load_survey(), rounds=rounds)


def _run(rounds: int = 1, **config):
    plan = _plan(rounds)
    machine = MissionMachine(MissionConfig(**config), route=plan)
    run = MissionRun(machine=machine, plan=plan, cargo_plan=plan.cargo_plan)
    return run


def _start(run: MissionRun, now: float = 0.0) -> list:
    """把状态机推到 ROUTE_RUNNING 并返回那一拍的命令。"""
    commands = []
    for _ in range(4):
        commands = run.tick(now, communication_ok=True, emergency_stop=False,
                            physical_start=True, mechanism_fault=False)
        now += 0.05
        if run.machine.state is MissionState.ROUTE_RUNNING and commands:
            break
    assert run.machine.state is MissionState.ROUTE_RUNNING, run.machine.state
    return commands


def _kinds(commands) -> list[str]:
    return [command.kind.value for command in commands]


def _text_of(commands, kind: CommandKind):
    for command in commands:
        if command.kind is kind:
            return command.text
    return None


class StartupTests(unittest.TestCase):
    def test_route_starts_only_after_the_physical_start_signal(self):
        """规则 4.2.4 S2：开赛信号之前不许动。"""
        run = _run()
        for _ in range(4):
            run.tick(0.0, communication_ok=True, physical_start=False)
        self.assertIs(run.machine.state, MissionState.WAIT_FOR_PHYSICAL_START)
        _start(run)
        self.assertIs(run.machine.state, MissionState.ROUTE_RUNNING)

    def test_communication_loss_is_fatal_after_startup(self):
        run = _run()
        _start(run)
        run.tick(1.0, communication_ok=False)
        self.assertIs(run.machine.state, MissionState.FAILED)
        self.assertIs(run.machine.result, MissionResult.COMMUNICATION_ERROR)

    def test_emergency_stop_releases_everything(self):
        run = _run()
        _start(run)
        commands = run.tick(1.0, emergency_stop=True)
        self.assertIs(run.machine.state, MissionState.SAFE_STOP)
        self.assertIn(CommandKind.AUTHORITY.value, _kinds(commands))
        self.assertEqual(_text_of(commands, CommandKind.AUTHORITY), SOURCE_NONE)


class DispatchOrderTests(unittest.TestCase):
    def test_first_segment_dispatches_line_and_turn_commands(self):
        """S01 是「沿线走到 N02 再右转」，所以进段时同时下发巡线参数与转弯命令。"""
        run = _run()
        commands = _start(run)
        line = json.loads(_text_of(commands, CommandKind.LINE))
        self.assertEqual(line["segment_id"], "S01_LINE_START")
        turn = json.loads(_text_of(commands, CommandKind.TURN))
        self.assertEqual(turn["direction"], "RIGHT")
        self.assertEqual(turn["ref"], "N02")
        self.assertEqual(
            _text_of(commands, CommandKind.AUTHORITY), "line_follow",
            "巡线段授权给巡线节点",
        )

    def test_non_turn_segment_clears_the_turn_command(self):
        """进入不需要转弯的段必须下发空串清除，否则残留指令会在错误的位置执行。"""
        run = _run()
        _start(run)
        run.machine.route_runner.skip_to("S03_RAMP_APPROACH")
        run.machine._sync_route()
        commands = run.tick(0.2)
        self.assertEqual(_text_of(commands, CommandKind.TURN), "")

    def test_shift_segment_goes_goal_then_authority(self):
        """先目标后授权：否则位姿控制器会在没有目标时被授权。"""
        run = _run()
        _start(run)
        run.machine.route_runner.skip_to("S11_SHIFT_TO_BUILD")
        run.machine._sync_route()
        commands = run.tick(0.1)
        kinds = _kinds(commands)
        self.assertIn(CommandKind.GOAL.value, kinds)
        self.assertIn(CommandKind.AUTHORITY.value, kinds)
        self.assertLess(
            kinds.index(CommandKind.GOAL.value), kinds.index(CommandKind.AUTHORITY.value)
        )
        self.assertEqual(_text_of(commands, CommandKind.AUTHORITY), "motion_control")

    def test_ramp_segment_dispatches_a_ramp_profile(self):
        run = _run()
        _start(run)
        run.machine.route_runner.skip_to("S04_RAMP_UP")
        run.machine._sync_route()
        commands = run.tick(0.1)
        payload = json.loads(_text_of(commands, CommandKind.LINE))
        self.assertEqual(payload["segment_id"], "S04_RAMP_UP")
        self.assertEqual(payload["ramp"]["kind"], "RAMP_UP")


class WorkStepDispatchTests(unittest.TestCase):
    def _at_pick_segment(self):
        run = _run()
        _start(run)
        run.machine.route_runner.skip_to("S06_PICK3")
        run.machine._sync_route()
        commands = run.tick(0.1)
        return run, commands

    def test_first_pick_is_dispatched_once(self):
        run, commands = self._at_pick_segment()
        self.assertEqual(_text_of(commands, CommandKind.MANIPULATOR), "PICK_ORANGE")
        self.assertEqual(_text_of(commands, CommandKind.AUTHORITY), "manipulator_client")
        again = run.tick(0.15)
        self.assertIsNone(
            _text_of(again, CommandKind.MANIPULATOR), "同一步的机构命令不许重复发"
        )

    def test_shift_step_is_dispatched_after_the_pick_succeeds(self):
        run, _ = self._at_pick_segment()
        run.handle_result("manipulator", "SUCCESS", now=1.0)
        commands = run.tick(0.2)
        self.assertIn(CommandKind.GOAL.value, _kinds(commands))
        self.assertEqual(_text_of(commands, CommandKind.AUTHORITY), "motion_control")
        self.assertIs(run.work_plan.current.kind, WorkStepKind.SHIFT)

    def test_shift_success_does_not_count_as_a_work(self):
        """侧移成功不能被记成「抓了一块」（同一个回调入口，必须区分来源）。"""
        run, _ = self._at_pick_segment()
        run.handle_result("manipulator", "SUCCESS", now=1.0)
        run.tick(0.2)
        work_before = run.machine.route_runner.observations.work_count
        run.handle_result("motion", "SUCCESS", now=1.0)
        self.assertEqual(run.machine.route_runner.observations.work_count, work_before)
        self.assertIs(run.work_plan.current.kind, WorkStepKind.PICK)

    def test_mismatched_result_source_is_ignored(self):
        run, _ = self._at_pick_segment()
        run.handle_result("motion", "SUCCESS", now=1.0)  # 抓取步却收到运动结果
        self.assertIs(run.work_plan.current.kind, WorkStepKind.PICK)
        self.assertEqual(run.work_plan.index, 0)
        self.assertTrue(any("忽略" in note for note in run.notes))

    def test_work_count_advances_only_on_manipulator_success(self):
        run, _ = self._at_pick_segment()
        run.handle_result("manipulator", "SUCCESS", now=1.0)
        self.assertEqual(run.machine.route_runner.observations.work_count, 1)

    def test_two_layer_build_sequence_is_dispatched(self):
        run = _run()
        _start(run)
        run.machine.route_runner.skip_to("S12_BUILD_2LAYER")
        run.machine._sync_route()
        commands = run.tick(0.1)
        self.assertEqual(_text_of(commands, CommandKind.MANIPULATOR), "PLACE_ORANGE")
        self.assertEqual(run.work_plan.total, 5, "底 2 顶 1 = 放/侧移/放/退回/放")


class RetryAndStuckTests(unittest.TestCase):
    def test_retry_republishes_the_segment_commands(self):
        """打滑卡住后巡线节点保持零速：只重置计时叫不醒它，必须重发命令。"""
        run = _run(max_retries=2)
        _start(run)
        run.machine.route_runner.skip_to("S04_RAMP_UP")
        run.machine._sync_route()
        first = run.tick(0.1)
        self.assertIsNotNone(_text_of(first, CommandKind.LINE))

        # 段超时 → 第 1 次重试
        run.machine.entered_at = 0.0
        commands = run.tick(100.0)
        self.assertEqual(run.machine.retries, 1)
        line = json.loads(_text_of(commands, CommandKind.LINE))
        self.assertEqual(line["segment_id"], "S04_RAMP_UP", "重试必须重发本段巡线参数")

    def test_stuck_ramp_triggers_a_segment_retry(self):
        run = _run(max_retries=2)
        _start(run)
        run.machine.route_runner.skip_to("S04_RAMP_UP")
        run.machine._sync_route()
        run.tick(0.1)
        stuck = LineStatus(
            state=LineSensorState.ON_LINE, stale=False, blocked=False,
            ramp_decision=SlipDecision.STUCK.value,
        )
        for step in range(run.ramp_stuck_retry_reports):
            run.observe_line_status(stuck, 0.2 + 0.01 * step)
        self.assertEqual(run.machine.retries, 1, "连续 STUCK 上报应触发本段重试")
        self.assertTrue(any("打滑" in note for note in run.notes))

    def test_single_stuck_report_does_not_retry(self):
        """单帧 STUCK 可能只是抖动：要连续几拍才重试。"""
        run = _run()
        _start(run)
        run.observe_line_status(
            LineStatus(state=LineSensorState.ON_LINE, stale=False, blocked=False,
                       ramp_decision=SlipDecision.STUCK.value), 0.2,
        )
        self.assertEqual(run.machine.retries, 0)

    def test_stale_line_status_blocks_progress(self):
        run = _run()
        _start(run)
        # 让巡线状态过期（只喂一次，然后时间跳过去）
        run.observe_line_status(
            LineStatus(state=LineSensorState.ON_LINE, stale=False, blocked=False), 0.0
        )
        run.tick(10.0)
        self.assertTrue(
            run.machine.route_runner.observations.stale,
            "巡线状态断流后段必须停止推进",
        )

    def test_failure_without_cargo_degrades_to_retreat(self):
        run = _run(max_retries=1)
        _start(run)
        run.machine.route_runner.skip_to("S06_PICK3")
        run.machine._sync_route()
        run.tick(0.1)
        for _ in range(2):
            run.handle_result("manipulator", "MECHANISM_ERROR: boom", now=1.0)
        self.assertEqual(run.machine.segment_id, "S13_RETREAT")
        self.assertEqual(run.machine.degradations, 1)


class TerminalAndPayloadTests(unittest.TestCase):
    def test_terminal_state_releases_authority_and_clears_commands(self):
        run = _run()
        _start(run)
        run.tick(1.0, emergency_stop=True)
        commands = run.tick(1.05)
        self.assertEqual(_text_of(commands, CommandKind.AUTHORITY), SOURCE_NONE)
        self.assertEqual(_text_of(commands, CommandKind.TURN), "")
        self.assertEqual(_text_of(commands, CommandKind.LINE), "")

    def test_payload_has_every_key_the_panel_reads(self):
        run = _run()
        _start(run)
        payload = run.payload(0.1)
        for key in (
            "state", "phase", "segment_index", "segments_completed", "segment_id",
            "segment_label", "segment_count", "retries", "degradations", "detail",
            "match_remaining_s", "route_version", "rounds", "next_segment_id",
            "work_count", "work_required", "active_source", "route_error",
            "line_limit_mps", "ramp_decision", "turn_phase", "work_step",
            "work_step_kind", "work_step_label",
            # B4 开赛门（网页必须能显示「现在能不能开赛」）
            "line_calibration_ready", "readiness_blockers",
            # B3/B4 载货（网页显示「车上有几块」；R15 起路线模式才真的记账）
            "cargo_onboard", "cargo_valid",
        ):
            self.assertIn(key, payload, key)

    def test_payload_reports_remaining_match_time(self):
        run = _run(match_time_limit_s=360.0)
        _start(run, now=100.0)
        payload = run.payload(100.0 + 60.0)
        # `_start` 会推进几拍，所以路线起点略晚于 100.0；只要求量级正确
        self.assertLess(abs(payload["match_remaining_s"] - 300.0), 1.0)

    def test_match_clock_stops_everything(self):
        run = _run(match_time_limit_s=360.0)
        _start(run, now=100.0)
        run.tick(100.0 + 361.0)
        self.assertIs(run.machine.state, MissionState.SAFE_STOP)
        self.assertIn("比赛时间到", run.machine.detail)

    def test_pose_observation_accumulates_segment_distance(self):
        run = _run()
        _start(run)
        run.observe_pose(Pose2D(0.7, 0.7, 1.5708), 0.1)
        run.observe_pose(Pose2D(0.7, 0.8, 1.5708), 0.15)
        run.observe_pose(Pose2D(0.7, 0.9, 1.5708), 0.2)
        self.assertAlmostEqual(run.segment_distance_m, 0.2, places=6)


class LineCalibrationGateTests(unittest.TestCase):
    """B4 开赛门在**运行循环**里的行为（不只是纯函数）。

    为什么要在这一层再测一次：纯函数只证明「给定三态能算出结论」，
    这里证明「运行循环真的把巡线节点的三态喂给了判据」——中间任何一环
    接错了（忘了传、传了旧值、断流后还当可用），现场表现都是「带着假读数开赛」。
    """

    def _status(self, ready):
        return LineStatus(
            state=LineSensorState.ON_LINE, stale=False, blocked=False,
            calibration_ready=ready,
        )

    def _run_to_wait(self, **config):
        run = _run(**config)
        for _ in range(4):
            run.tick(0.0, communication_ok=True, physical_start=False)
        self.assertIs(run.machine.state, MissionState.WAIT_FOR_PHYSICAL_START)
        return run

    def test_gate_off_by_default_keeps_mock_and_unit_flows_working(self):
        run = self._run_to_wait()
        run.tick(0.1, physical_start=True)
        self.assertIsNot(run.machine.state, MissionState.FAILED)

    def test_gate_blocks_launch_when_the_node_reports_unusable_calibration(self):
        run = self._run_to_wait(require_line_calibration=True)
        run.observe_line_status(self._status(False), 0.1)
        run.tick(0.1, physical_start=True)
        self.assertIs(run.machine.state, MissionState.FAILED)
        self.assertIn("标定不可用", run.machine.detail)

    def test_gate_blocks_launch_when_the_node_never_reported(self):
        """没有任何 /line_follow/status = 未知 = 拒绝（保守方向）。"""
        run = self._run_to_wait(require_line_calibration=True)
        run.tick(0.1, physical_start=True)
        self.assertIs(run.machine.state, MissionState.FAILED)
        self.assertIn("未上报", run.machine.detail)

    def test_gate_allows_launch_after_a_ready_report(self):
        run = self._run_to_wait(require_line_calibration=True)
        run.observe_line_status(self._status(True), 0.1)
        run.tick(0.1, physical_start=True)
        self.assertIs(run.machine.state, MissionState.ROUTE_RUNNING)

    def test_a_stale_report_does_not_count_as_ready(self):
        """旧的「可用」不能证明现在可用——节点可能刚重启成未标定状态。"""
        run = self._run_to_wait(require_line_calibration=True)
        run.observe_line_status(self._status(True), 0.0)
        run.tick(0.0 + run.line_status_timeout_s + 0.1, physical_start=True)
        self.assertIs(run.machine.state, MissionState.FAILED)
        self.assertIsNone(run.line_calibration_ready(1.0))

    def test_readiness_is_visible_before_the_start_signal(self):
        """赛前就要能在网页上看到「现在能不能开赛」，而不是按下开始才知道。"""
        run = self._run_to_wait(require_line_calibration=True)
        run.observe_line_status(self._status(False), 0.1)
        payload = run.payload(0.1)
        self.assertIs(payload["line_calibration_ready"], False)
        self.assertEqual(len(payload["readiness_blockers"]), 1)
        self.assertIn("标定", payload["readiness_blockers"][0])

    def test_no_blockers_when_the_gate_is_off(self):
        """门没开就不该报阻塞项——否则网页会显示一个不存在的故障。"""
        run = self._run_to_wait()
        self.assertEqual(run.payload(0.1)["readiness_blockers"], [])

    def test_ready_report_clears_the_blockers(self):
        run = self._run_to_wait(require_line_calibration=True)
        run.observe_line_status(self._status(True), 0.1)
        payload = run.payload(0.1)
        self.assertIs(payload["line_calibration_ready"], True)
        self.assertEqual(payload["readiness_blockers"], [])

    def test_readiness_rule_is_shared_with_the_legacy_demo_flow(self):
        """判据只能有一份：路线模式与演示模式必须给出同样的三态。

        两处各写一遍的漂移表现是「同一个 require_line_calibration: true，
        换条流程就变了意思」——而这条参数关系到能不能开赛。
        """
        from robogame_core.mission_run import calibration_readiness

        status = self._status(True)
        self.assertIs(calibration_readiness(status, 10.0, 10.1, 0.5), True)
        self.assertIsNone(calibration_readiness(status, 10.0, 11.0, 0.5), "断流 = 未知")
        self.assertIsNone(calibration_readiness(None, 0.0, 10.0, 0.5), "没收到 = 未知")
        self.assertIsNone(
            calibration_readiness(status, 0.0, 0.0, 0.5),
            "时间戳为 0 = 从没收到过（不能当成「刚收到」）",
        )
        # 与 MissionRun 自己的答案一致
        run = self._run_to_wait(require_line_calibration=True)
        run.observe_line_status(status, 10.0)
        self.assertEqual(run.line_calibration_ready(10.1), calibration_readiness(status, 10.0, 10.1, 0.5))
        self.assertEqual(run.line_calibration_ready(11.0), calibration_readiness(status, 10.0, 11.0, 0.5))


class CargoTrackingTests(unittest.TestCase):
    """路线模式下必须真的记账（R15 故障矩阵发现的真缺陷）。

    原先 `machine.cargo` 只在旧的演示流程里更新，路线模式从不更新。两个后果
    都是真车上的：① 网页面板一整趟显示载货 0 块；② 降级阶梯用
    `cargo.total >= 1` 判断「框里已有块 → 跳过剩余取块去搭建」，
    而它永远读到 0 → **`SKIP_TO_BUILD` 在真车上根本走不到**。
    """

    def _run_to_pick(self, **config):
        run = _run(**config)
        _start(run)
        run.machine.route_runner.skip_to("S06_PICK3")
        run.machine._sync_route()
        run.tick(0.1)
        return run

    def test_pick_success_puts_a_cube_on_board(self):
        run = self._run_to_pick()
        run.handle_result("manipulator", "SUCCESS", now=1.0)
        self.assertEqual(run.machine.cargo.orange, 1)
        self.assertIs(run.machine.cargo.valid, True)

    def test_the_count_reaches_the_web_panel(self):
        """记了账还不够：这个数必须出现在网页读的载荷里（现场要一眼看到）。"""
        run = self._run_to_pick()
        self.assertEqual(run.payload(1.0)["cargo_onboard"], 0)
        run.handle_result("manipulator", "SUCCESS", now=1.0)
        payload = run.payload(2.0)
        self.assertEqual(payload["cargo_onboard"], 1)
        self.assertIs(payload["cargo_valid"], True)

    def test_shift_does_not_change_the_cargo_count(self):
        """侧移成功不是一次作业：不能把微移记成"抓了一块"。"""
        run = self._run_to_pick()
        run.handle_result("manipulator", "SUCCESS", now=1.0)  # 抓第 1 块
        self.assertEqual(run.machine.cargo.total, 1)
        before = run.machine.cargo.total
        for step_index in range(6):
            run.handle_result("motion", "SUCCESS", now=2.0 + step_index)
            run.notes.clear()
        self.assertEqual(
            run.machine.cargo.total, before,
            "侧移不该改变载货数量",
        )

    def test_segment_failure_with_one_cube_on_board_skips_to_build(self):
        """这条就是 SKIP_TO_BUILD：以前因为 cargo 永远是 0 而走不到。"""
        run = self._run_to_pick(max_retries=1)
        run.handle_result("manipulator", "SUCCESS", now=1.0)  # 抓到 1 块
        self.assertEqual(run.machine.cargo.total, 1)
        run.handle_result("manipulator", "SUCCESS", now=2.0)  # 侧移
        for attempt in range(2):
            run.handle_result("manipulator", "MECHANISM_ERROR: 抓空", now=3.0 + attempt)
        self.assertEqual(run.machine.segment_id, "S12_BUILD_2LAYER")
        self.assertEqual(run.machine.degradations, 1)
        self.assertIn("已有 1 块", run.machine.detail)

    def test_place_success_takes_a_cube_off_board(self):
        run = self._run_to_pick()
        # 取 3 块的序列是「抓 → 侧移 → 抓 → 侧移 → 抓」：必须按步骤类型喂结果，
        # 否则侧移那一步等的是 motion 结果，机构结果会被忽略（这正是设计里的保护）。
        for index in range(3):
            run.handle_result("manipulator", "SUCCESS", now=1.0 + 2 * index)
            if index < 2:
                run.handle_result("motion", "SUCCESS", now=1.5 + 2 * index)
        self.assertEqual(run.machine.cargo.total, 3)
        run.machine.route_runner.skip_to("S12_BUILD_2LAYER")
        run.machine._sync_route()
        run.tick(0.1)
        run.handle_result("manipulator", "SUCCESS", now=5.0)
        self.assertEqual(run.machine.cargo.total, 2)

    def test_impossible_place_does_not_crash_but_marks_cargo_untrusted(self):
        """簿记对不上时**不能**抛异常打断任务；但要把它标成不可信并留下说明。"""
        run = self._run_to_pick()
        run.machine.route_runner.skip_to("S12_BUILD_2LAYER")
        run.machine._sync_route()
        run.tick(0.1)
        run.handle_result("manipulator", "SUCCESS", now=5.0)  # 手里没有块却"放"成功
        self.assertIs(run.machine.cargo.valid, False)
        self.assertTrue(any("载货簿记异常" in note for note in run.notes))
        self.assertIs(run.machine.state, MissionState.ROUTE_RUNNING, "簿记问题不该打断任务")


class SelfCheckFaultTests(unittest.TestCase):
    """自检期机构故障：立刻按机构故障处理，而不是干等到状态超时（R15 修正）。"""

    def test_mechanism_fault_at_self_check_fails_with_the_right_reason(self):
        run = _run()
        for _ in range(4):
            run.tick(0.0, mechanism_fault=True)
        self.assertIs(run.machine.state, MissionState.FAILED)
        self.assertIs(run.machine.result, MissionResult.MECHANISM_ERROR)
        self.assertIn("机制故障", run.machine.detail)

    def test_it_fails_without_waiting_for_the_state_timeout(self):
        """60 s（20 s × 3 次）的等待在无人干预模式下毫无意义：必须在 1 s 内判死。"""
        run = _run(state_timeout_s=20.0)
        elapsed = 0.0
        for _ in range(20):
            run.tick(elapsed, mechanism_fault=True)
            elapsed += 0.05
            if run.machine.state is MissionState.FAILED:
                break
        self.assertIs(run.machine.state, MissionState.FAILED)
        self.assertLess(elapsed, 1.0)

    def test_healthy_self_check_still_starts_normally(self):
        run = _run()
        _start(run)
        self.assertIs(run.machine.state, MissionState.ROUTE_RUNNING)


class CompletionHonestyTests(unittest.TestCase):
    """降级过就必须在终态 detail 里写出来（R15）：COMPLETE 不等于全做成了。"""

    def _finish(self, run, *, degradations: int):
        """把状态机推到 COMPLETE（走真实的 VERIFY_BUILD 稳定计时）。"""
        _start(run)
        run.machine.degradations = degradations
        run.machine._enter(MissionState.VERIFY_BUILD, 0.0)
        run.tick(run.machine.config.build_stability_s + 0.1)
        run.tick(run.machine.config.build_stability_s + 0.2)
        return run

    def test_clean_completion_keeps_the_plain_wording(self):
        run = self._finish(_run(), degradations=0)
        self.assertIs(run.machine.state, MissionState.COMPLETE)
        self.assertEqual(run.machine.detail, "mission complete")

    def test_completion_after_degradation_says_so(self):
        """降级让任务继续走到终点，终态是 COMPLETE/SUCCESS——只看这一行会误读。"""
        run = self._finish(_run(max_retries=1), degradations=1)
        self.assertIs(run.machine.state, MissionState.COMPLETE)
        self.assertIn("降级 1 次", run.machine.detail)
        self.assertIn("不等于全部作业都做成了", run.machine.detail)

    def test_degradation_is_reachable_at_all_with_cargo(self):
        """端到端确认降级真的发生了（不是只把计数改成 1 的假测试）。"""
        run = _run(max_retries=1)
        _start(run)
        run.machine.route_runner.skip_to("S06_PICK3")
        run.machine._sync_route()
        run.tick(0.1)
        run.handle_result("manipulator", "SUCCESS", now=1.0)
        run.handle_result("motion", "SUCCESS", now=2.0)
        for attempt in range(2):
            run.handle_result("manipulator", "MECHANISM_ERROR: 抓空", now=3.0 + attempt)
        self.assertEqual(run.machine.degradations, 1)


if __name__ == "__main__":
    unittest.main()
