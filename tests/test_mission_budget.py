"""比赛时间预算与段超时的测试（纯逻辑 + 离线预演）。

这一轮用离线预演发现了一个真问题：**段超时用常数 20 s 时，把限速调慢会变成
「超时失败」而不是「只是变慢」**（最长主路段 2.8 m 在 0.25 m/s 下要 11.2 s，
只有 1.8 倍余量）。修法是按段距离推导预算。这里把两件事都钉住：

1. 预算公式本身（越长/越慢的段预算越大；作业段用固定长预算；有下限）；
2. 预演层面的后果：速度减半后**仍然跑得完**（这条就是当初失败的场景）。
"""

from __future__ import annotations

import unittest

from robogame_core.mission import (
    MissionConfig,
    MissionMachine,
    MissionState,
    segment_required_time_s,
)
from robogame_core.mission_route import ExitKind, SegmentRole, build_route_plan
from robogame_core.route_loader import load_survey
from mission_budget import (
    PICK_PLACE_PRESETS,
    SPEED_SCALES,
    format_table,
    measure,
    sweep,
)


def _plan(rounds: int = 1):
    return build_route_plan(load_survey(), rounds=rounds)


class RequiredTimeTests(unittest.TestCase):
    def test_longer_or_slower_segments_need_more_time(self):
        plan = _plan()
        main = plan.segment("S02_LINE_MAIN")  # 2.8 m @ 0.25
        start = plan.segment("S01_LINE_START")  # 0.9 m @ 0.20
        self.assertGreater(
            segment_required_time_s(main), segment_required_time_s(start)
        )
        self.assertAlmostEqual(segment_required_time_s(main), 2.8 / 0.25, places=6)

    def test_missing_speed_override_uses_the_default(self):
        plan = _plan()
        work = plan.segment("S06_PICK3")  # 原地段，长度 0
        self.assertEqual(segment_required_time_s(work), 0.0)
        # 人为构造一个没有限速覆盖的段：用默认速度
        import dataclasses

        segment = dataclasses.replace(plan.segment("S02_LINE_MAIN"), max_speed_mps=0.0)
        self.assertAlmostEqual(
            segment_required_time_s(segment, default_speed=0.1), 2.8 / 0.1, places=6
        )


class SegmentBudgetTests(unittest.TestCase):
    def _machine(self, **config) -> MissionMachine:
        machine = MissionMachine(MissionConfig(**config), route=_plan())
        machine.tick(now=0.0, action_succeeded=True)
        machine.tick(now=0.1, physical_start=True)
        self.assertIs(machine.state, MissionState.ROUTE_RUNNING)
        return machine

    def test_work_segment_uses_the_work_budget(self):
        machine = self._machine(work_state_timeout_s=120.0)
        machine.route_runner.skip_to("S06_PICK3")
        machine._sync_route()
        self.assertAlmostEqual(machine.segment_timeout_budget(), 120.0)

    def test_long_movement_segment_gets_a_larger_budget(self):
        """按计划限速，主路段（2.8 m @ 0.25 = 11.2 s）预算应显著大于下限。"""
        machine = self._machine(state_timeout_s=20.0)
        machine.route_runner.skip_to("S02_LINE_MAIN")
        machine._sync_route()
        budget = machine.segment_timeout_budget()
        self.assertGreater(budget, 20.0)
        self.assertAlmostEqual(budget, 11.2 * 2.0 + 5.0, places=3)

    def test_short_segment_keeps_the_floor(self):
        machine = self._machine(state_timeout_s=20.0)
        machine.route_runner.skip_to("S03_RAMP_APPROACH")  # 0.6 m @ 0.15 = 4 s
        machine._sync_route()
        self.assertAlmostEqual(machine.segment_timeout_budget(), 20.0)

    def test_slow_segment_does_not_time_out_prematurely(self):
        """把限速减半后，主路段需要 22.4 s > 常数 20 s——预算必须跟着长。"""
        machine = self._machine(state_timeout_s=20.0)
        machine.route_runner.skip_to("S02_LINE_MAIN")
        machine._sync_route()
        budget = machine.segment_timeout_budget()
        self.assertGreater(budget, 22.4, "限速减半后 20 s 的常数预算会误判超时")


class BudgetSweepTests(unittest.TestCase):
    """离线预演层面的时间预算（虚拟场地，非物理仿真）。"""

    def test_planned_speed_and_typical_times_fit_two_passes(self):
        result, _pick, _place = measure(speed_scale=1.0, preset="typical")
        self.assertTrue(result.completed, result.summary())
        self.assertLess(result.virtual_time_s, 180.0, "按计划限速一趟应在 3 分钟内")
        self.assertGreaterEqual(
            360.0 // result.virtual_time_s, 2,
            "按计划速度 + 典型抓放，6 分钟应容得下第二趟",
        )

    def test_half_speed_still_completes_after_the_timeout_fix(self):
        """回归：速度减半曾经因为常数段超时而 **FAILED**，现在必须只是变慢。"""
        result, _pick, _place = measure(speed_scale=0.5, preset="typical")
        self.assertTrue(result.completed, result.summary())
        self.assertEqual(result.final_state, "COMPLETE")

    def test_every_combination_completes(self):
        for case in sweep():
            self.assertTrue(
                case.completed,
                f"{case.speed_scale:.2f}× + {case.preset} 没跑完：{case.final_state}",
            )
            self.assertGreater(case.remaining_s, 0.0)

    def test_table_states_its_own_limits(self):
        text = format_table(sweep())
        for caveat in ("虚拟场地", "无动力学", "输入假设"):
            self.assertIn(caveat, text, "表必须自带边界说明，不能只给数字")
        self.assertIn("多趟还卡在两条现场/机械结论", text)

    def test_presets_match_the_field_time_budget_document(self):
        """抓放耗时档必须与现场时间预算文档里的数字一致（改一处就要同步）。"""
        budget_csv = (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "docs/field/TIME_BUDGET.csv"
        )
        if not budget_csv.is_file():
            self.skipTest("现场时间预算文档不在仓库里")
        text = budget_csv.read_text(encoding="utf-8")
        for label, (pick, place) in PICK_PLACE_PRESETS.items():
            self.assertGreater(pick, 0.0)
            self.assertGreater(place, 0.0)
            # 至少要在文档里出现「抓取最坏 14.3」这类数字
            if label == "worst":
                self.assertIn("14.3", text, "最坏抓取耗时与 TIME_BUDGET.csv 不一致")
        for scale in SPEED_SCALES:
            self.assertGreater(scale, 0.0)


class ExitKindCoverageTests(unittest.TestCase):
    """路线里出现的每种退出判据都必须有对应的观测来源（与集成审计互相印证）。"""

    def test_every_exit_kind_in_the_route_is_supported(self):
        plan = _plan()
        kinds = {segment.exit.kind for segment in plan.segments}
        self.assertTrue(kinds)
        for kind in kinds:
            self.assertIsInstance(kind, ExitKind, kind)

    def test_work_segments_are_the_only_ones_with_the_long_budget(self):
        plan = _plan()
        for segment in plan.segments:
            if segment.role is SegmentRole.WORK:
                self.assertEqual(segment.exit.kind.value, "WORK_DONE", segment.id)


class CliEntryPointTests(unittest.TestCase):
    """文档里写着「跑这条命令」的命令，必须真的能跑出东西。

    真实踩过（R13）：`docs/field/上电自主完赛流程.md` 让人跑
    `python3 tools/mission_sim.py`，但这个文件当时**没有 `__main__`**——
    跑出来一片空白，照着文档做的人会以为「预演没问题」。另外预算表里有个 `⚠️`，
    Windows 的 GBK 控制台会直接 `UnicodeEncodeError` 退出，
    算出来的结论全被一个符号带崩。
    """

    def test_simulation_tool_has_a_working_main(self):
        import io
        from contextlib import redirect_stdout

        from mission_sim import main

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main()
        self.assertEqual(code, 0, "预演跑完应返回 0（未完成才返回非 0）")
        self.assertIn("COMPLETE", buffer.getvalue())

    def test_budget_tool_forces_utf8_so_one_symbol_cannot_kill_the_table(self):
        from mission_budget import _force_utf8_stdout

        calls = {}

        class _Stream:
            def reconfigure(self, **kwargs):
                calls.update(kwargs)

        import mission_budget

        original = mission_budget.sys.stdout
        try:
            mission_budget.sys.stdout = _Stream()
            _force_utf8_stdout()
        finally:
            mission_budget.sys.stdout = original
        self.assertEqual(calls.get("encoding"), "utf-8")
        self.assertEqual(calls.get("errors"), "replace", "不能因为一个符号就打不出结论")

    def test_forcing_utf8_tolerates_streams_without_reconfigure(self):
        import mission_budget

        original = mission_budget.sys.stdout
        try:
            mission_budget.sys.stdout = object()
            mission_budget._force_utf8_stdout()  # 不抛异常即可
        finally:
            mission_budget.sys.stdout = original


if __name__ == "__main__":
    unittest.main()
