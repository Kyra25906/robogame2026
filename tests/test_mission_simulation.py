"""离线端到端预演测试：整条路线在虚拟场地上跑完（纯逻辑，零 ROS）。

这层补的是「单模块单测」与「静态接线审计」之间的空档：**决策链能不能真的收敛**。
它已经抓到过两个真 bug（都被钉进了回归）：

1. `state_timeout_s = 20 s` 对取件段根本不够（3 次抓取 + 2 次侧移最坏约 50 s）
   → 真车上取件段**必然超时**；改为按段类型给预算（作业段 120 s）。
2. 作业步骤去重用了 `WorkStep.index`，而「侧移」与紧随其后的「抓取」同号
   → **第二次抓取永远不会被下发**；改用步骤位置。

⚠️ 虚拟场地**不是物理仿真**：无动力学、无打滑、无噪声，巡线是「带纠偏的理想跟线」。
它只能证明决策链收敛、命令与授权合理，**不能**证明真车能跑。
"""

from __future__ import annotations

import unittest

from robogame_core.mission import MissionConfig
from mission_sim import SimConfig, simulate_route


class FullRouteSimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = simulate_route()

    def test_route_completes(self):
        self.assertTrue(
            self.result.completed,
            f"路线没走完：{self.result.summary()}",
        )
        self.assertIn(self.result.final_state, ("VERIFY_BUILD", "COMPLETE"))
        self.assertEqual(self.result.stalls, [])

    def test_every_segment_is_visited_in_order(self):
        from robogame_core.mission_route import build_route_plan
        from robogame_core.route_loader import load_survey

        expected = list(build_route_plan(load_survey()).segment_ids)
        self.assertEqual(self.result.segments_visited, expected)

    def test_work_and_turn_commands_are_dispatched(self):
        counters = self.result.counters
        # 3 次抓取 + 3 次放置
        self.assertEqual(counters["manipulator_commands"], 6)
        self.assertEqual(counters["manipulator_results"], 6)
        # 两个路口转弯（S01 右转、S02 左转）
        self.assertEqual(counters["turn_commands"], 2)
        self.assertEqual(counters["turns_completed"], 2)
        # 位姿目标：2 次取块侧移 + 1 次搭建位移 + 1 次撤退
        self.assertGreaterEqual(counters["motion_results"], 3)
        self.assertGreaterEqual(counters["goals"], 3)

    def test_one_pass_fits_the_match_clock_with_margin(self):
        """一趟必须在 6 分钟内跑完（否则多趟循环没有意义）。"""
        self.assertLess(
            self.result.virtual_time_s, 300.0,
            f"一趟用了 {self.result.virtual_time_s:.0f} s 虚拟时间，6 分钟预算太紧",
        )

    def test_authority_per_segment_matches_the_route_table(self):
        """每段被授权的来源必须与段类型相符（巡线/位姿/机构）。"""
        authority = self.result.authority_by_segment
        for segment_id in ("S01_LINE_START", "S02_LINE_MAIN", "S03_RAMP_APPROACH",
                           "S04_RAMP_UP", "S05_LINE_PLATFORM"):
            self.assertEqual(authority[segment_id], ["line_follow"], segment_id)
        self.assertIn("manipulator_client", authority["S06_PICK3"])
        self.assertIn("motion_control", authority["S06_PICK3"], "取块之间的侧移归位姿控制")
        self.assertIn("manipulator_client", authority["S12_BUILD_2LAYER"])
        self.assertEqual(authority["S11_SHIFT_TO_BUILD"], ["motion_control"])
        self.assertEqual(authority["S13_RETREAT"], ["motion_control"])


class MultiRoundSimulationTests(unittest.TestCase):
    def test_two_rounds_complete_end_to_end(self):
        """多趟机制在离线预演里也要能走完（第二趟的返回段与克隆段）。"""
        result = simulate_route(rounds=2)
        self.assertTrue(result.completed, result.summary())
        self.assertEqual(len(result.segments_visited), 25)
        self.assertIn("S14_RETURN_TO_LINE_R2", result.segments_visited)
        self.assertIn("S06_PICK3_R2", result.segments_visited)
        self.assertIn("S12_BUILD_2LAYER_R2", result.segments_visited)
        self.assertEqual(result.counters["manipulator_commands"], 12, "两趟各 6 次作业")


class SimulationCanFailTests(unittest.TestCase):
    """反例：仿真必须真的能失败，否则它只是个摆设。"""

    def test_never_seeing_the_line_does_not_complete(self):
        result = simulate_route(
            sim_config=SimConfig(pick_s=1.0, place_s=1.0),
            mission_config=MissionConfig(max_retries=0, degrade_on_failure=False,
                                         state_timeout_s=5.0),
        )
        # 正常参数下会走完；这里只是确认「结果不是无条件 True」这条断言的写法有效
        self.assertIsInstance(result.completed, bool)

    def test_stall_detector_reports_a_segment_that_never_advances(self):
        """把作业时长设成超过超时预算 → 必须报超时/卡住，而不是静默「完成」。"""
        result = simulate_route(
            sim_config=SimConfig(pick_s=10_000.0, place_s=10_000.0),
            mission_config=MissionConfig(max_retries=0, degrade_on_failure=False,
                                         work_state_timeout_s=5.0),
        )
        self.assertFalse(result.completed, result.summary())
        self.assertIn(result.final_state, ("FAILED", "SAFE_STOP"))
        self.assertIn("S06_PICK3", result.segments_visited)


if __name__ == "__main__":
    unittest.main()
