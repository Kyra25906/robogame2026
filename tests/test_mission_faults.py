"""故障注入矩阵的测试（纯逻辑 + 虚拟场地）。

这份工具的用途：B4 的「故障降级」只有真的把故障灌进去看结果，才有证据。
这里测两件事：

1. **矩阵本身必须全绿**：9 个场景都要收敛到安全终态、且行为符合降级阶梯的设计；
2. **检查器本身必须能红**：故意写一个错的期望、故意造一个不安全的终态，
   检查器都得报出来——否则「9/9 通过」只是「没检查」。
"""

from __future__ import annotations

import unittest

from robogame_core.mission import MissionState
from robogame_core.mission_run import Command, CommandKind
from mission_faults import (
    KIND_COMMS_LOSS,
    FaultScenario,
    _evaluate,
    format_table,
    run_all,
    run_scenario,
    scenarios,
)


class FaultMatrixTests(unittest.TestCase):
    """9 个场景：虚拟场地上真的跑一遍（每个 ~2 s）。"""

    @classmethod
    def setUpClass(cls):
        cls.results = run_all()

    def test_every_scenario_holds_its_invariants(self):
        for result in self.results:
            self.assertEqual(
                result.invariant_violations, [],
                f"{result.name} 违反了安全不变式：{result.invariant_violations}",
            )

    def test_every_scenario_matches_its_expectations(self):
        for result in self.results:
            self.assertEqual(
                result.expectation_mismatches, [],
                f"{result.name} 与期望不符：{result.expectation_mismatches}",
            )

    def test_terminal_state_always_releases_authority(self):
        """最重要的安全性质：**不管怎么失败，最后都没人再驱动底盘**。"""
        for result in self.results:
            self.assertEqual(result.terminal_authority, "none", result.name)
            self.assertEqual(result.terminal_leftovers, [], result.name)

    def test_no_scenario_ends_stuck(self):
        for result in self.results:
            self.assertIn(
                result.final_state,
                {MissionState.COMPLETE.value, MissionState.FAILED.value,
                 MissionState.SAFE_STOP.value},
                f"{result.name} 停在 {result.final_state}",
            )

    def test_summary_is_honest_about_its_limits(self):
        """表必须自带边界：证明的是决策链，不是真车。"""
        text = format_table(self.results)
        for caveat in ("虚拟场地", "不证明", "轮速里程计"):
            self.assertIn(caveat, text)

    def test_table_reports_the_pass_ratio(self):
        text = format_table(self.results)
        self.assertIn(f"{len(self.results)}/{len(self.results)}", text)


class ScenarioDefinitionTests(unittest.TestCase):
    """场景定义本身要守住几条规矩。"""

    def test_every_scenario_has_a_field_counterpart_note(self):
        for scenario in scenarios():
            self.assertTrue(scenario.note.strip(), f"{scenario.name} 没写对应的真车动作")

    def test_expected_states_are_real_mission_states(self):
        known = {state.value for state in MissionState}
        for scenario in scenarios():
            if scenario.expect_state:
                self.assertIn(scenario.expect_state, known, scenario.name)
            for state in scenario.expect_state_in:
                self.assertIn(state, known, scenario.name)

    def test_pick_and_place_scenarios_are_segment_scoped(self):
        """「这一段抓取一直失败」是段内故障：不该锁存到后面所有作业段。

        如果锁存，跳段去搭建之后每次放也会"失败"，
        那测的就不是「抓取失败」，而是「机构彻底坏了」——两种故障要分开测。
        """
        for scenario in scenarios():
            if scenario.at_segment.startswith("S06") or scenario.at_segment.startswith("S12"):
                self.assertFalse(scenario.latched, f"{scenario.name} 不该锁存")

    def test_sensor_level_faults_are_latched(self):
        """断线/位姿不可信不会自己好：必须锁存（不锁存会漏掉"故障持续"的后果）。"""
        for scenario in scenarios():
            if scenario.kind in (KIND_COMMS_LOSS, "line_blackout", "ramp_stuck"):
                self.assertTrue(scenario.latched, f"{scenario.name} 应该锁存")

    def test_expected_outcomes_cover_both_safe_ends(self):
        """矩阵必须同时包含「停下」和「降级收场」两类结局，否则覆盖是偏的。"""
        states = set()
        for scenario in scenarios():
            if scenario.expect_state:
                states.add(scenario.expect_state)
            states.update(scenario.expect_state_in)
        self.assertIn(MissionState.FAILED.value, states)
        self.assertIn(MissionState.SAFE_STOP.value, states)
        self.assertIn(MissionState.COMPLETE.value, states)


class CheckerCanFailTests(unittest.TestCase):
    """检查器必须能红——否则「通过」没有意义。"""

    def test_wrong_expectation_is_reported(self):
        wrong = FaultScenario(
            name="故意写错的期望", kind=KIND_COMMS_LOSS,
            at_segment="S02_LINE_MAIN", delay_s=1.0,
            expect_state=MissionState.COMPLETE.value,
        )
        result = run_scenario(wrong)
        self.assertFalse(result.ok)
        self.assertTrue(any("终态" in item for item in result.expectation_mismatches))

    def test_invariant_violations_are_reported(self):
        """造一个"不安全"的终态：终态没释放授权、还留着 GOAL、重试超限、没写原因。"""

        class FakeMachine:
            state = MissionState.FAILED
            result = type("R", (), {"value": "MECHANISM_ERROR"})()
            detail = "   "
            degradations = 0
            retries = 9
            config = type("C", (), {"max_retries": 2})()

        commands = [
            Command(CommandKind.AUTHORITY, text="line_follow"),
            Command(CommandKind.MANIPULATOR, text="PICK_ORANGE"),
        ]
        result = _evaluate(
            scenarios()[0], machine=FakeMachine(), commands=commands, visited=[],
            stalls=[], virtual_time_s=1.0,
        )
        self.assertFalse(result.ok)
        joined = " ".join(result.invariant_violations)
        self.assertIn("I2", joined, "没释放授权必须被抓到")
        self.assertIn("I3", joined, "残留机构指令必须被抓到")
        self.assertIn("I4", joined, "没写原因必须被抓到")
        self.assertIn("I5", joined, "重试超限必须被抓到")


if __name__ == "__main__":
    unittest.main()
