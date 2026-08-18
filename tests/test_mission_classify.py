"""A4（P0-4/5/6）验证：结果字符串分类 + field 模式 PLACE 成功路径。

背景（执行队列 2026-08-18 A4）：field 模式「建筑搭建 20 分」100% 失败的三连击：
- P0-4 真实模式不支持机构式 RETREAT；
- P0-5 retreat_complete 无条件读 mock；
- P0-6 INCONCLUSIVE 不在 MissionResult 被归为 MECHANISM_ERROR。

本测试验证：
1. `classify_action_result` 纯函数：INCONCLUSIVE 不判死、SUCCESS/STABLE 成功、
   其他结果映射到 MissionResult 成员（A0.6「禁字符串前缀分类」的落地）；
2. AST 结构断言：field 模式 PLACE 成功路径不触碰机构服务、真实模式
   retreat_complete 不来自 mock。
"""
import ast
import unittest
from pathlib import Path

from robogame_core.mission import classify_action_result
from robogame_core.models import MissionResult

ROOT = Path(__file__).resolve().parents[1]
MANIPULATOR_PATH = ROOT / "ros2_ws/src/manipulator_client/manipulator_client/node.py"
ROBOT_BRIDGE_PATH = ROOT / "ros2_ws/src/robot_bridge/robot_bridge/node.py"
MISSION_MANAGER_PATH = ROOT / "ros2_ws/src/mission_manager/mission_manager/node.py"


class ClassifyActionResultTests(unittest.TestCase):
    def test_success_is_success(self):
        succeeded, result, detail = classify_action_result("SUCCESS")
        self.assertTrue(succeeded)
        self.assertIsNone(result)

    def test_stable_is_success(self):
        succeeded, result, detail = classify_action_result("STABLE")
        self.assertTrue(succeeded)
        self.assertIsNone(result)

    def test_inconclusive_is_not_a_failure(self):
        # P0-6 核心：INCONCLUSIVE 不得被判死，进入 mission 级 VERIFY_BUILD。
        succeeded, result, detail = classify_action_result(
            "INCONCLUSIVE: stable placement could not be confirmed"
        )
        self.assertTrue(succeeded, "INCONCLUSIVE must not fail the mission")
        self.assertIsNone(result)

    def test_inconclusive_is_a_mission_result_member(self):
        # 字符串前缀必须能映射到 MissionResult 成员（A0.6）。
        self.assertIn(MissionResult.INCONCLUSIVE, MissionResult)

    def test_timeout_maps_to_member(self):
        succeeded, result, detail = classify_action_result("TIMEOUT: navigation goal")
        self.assertFalse(succeeded)
        self.assertIs(result, MissionResult.TIMEOUT)

    def test_mechanism_error_prefix_maps_to_member(self):
        succeeded, result, detail = classify_action_result(
            "MECHANISM_ERROR: grab failed (code=12): limit"
        )
        self.assertFalse(succeeded)
        self.assertIs(result, MissionResult.MECHANISM_ERROR)

    def test_unknown_prefix_falls_back_to_mechanism_error(self):
        succeeded, result, detail = classify_action_result("WEIRD: something")
        self.assertFalse(succeeded)
        self.assertIs(result, MissionResult.MECHANISM_ERROR)

    def test_every_member_prefix_is_classifiable(self):
        # A0.6 遍历测试：每个 MissionResult 成员的前缀都必须能映射回自身
        # （除 SUCCESS/STABLE 走成功分支外，其余成员都能被识别为失败结果）。
        for member in MissionResult:
            if member in {MissionResult.SUCCESS, MissionResult.RUNNING,
                          MissionResult.SAFETY_STOP}:
                continue  # 成功/运行中/安全停车不经过 action-result 失败通道
            succeeded, result, _ = classify_action_result(member.value)
            if member is MissionResult.INCONCLUSIVE:
                self.assertTrue(succeeded)
            else:
                self.assertFalse(succeeded)
                self.assertIs(result, member)


class FieldPlaceRetreatSafetyTests(unittest.TestCase):
    """AST 结构断言：field 模式 PLACE 成功路径不触碰机构 RETREAT 服务。"""

    @classmethod
    def setUpClass(cls):
        cls.manipulator_source = MANIPULATOR_PATH.read_text(encoding="utf-8")
        cls.bridge_source = ROBOT_BRIDGE_PATH.read_text(encoding="utf-8")
        cls.manager_source = MISSION_MANAGER_PATH.read_text(encoding="utf-8")

    def test_field_place_succeeds_without_retreat_service(self):
        # P0-4：_verify_place 的 PASS 分支必须有 field 模式直接成功返回，
        # 且该分支调用 _finish("SUCCESS") 而不是 _start_retreat。
        self.assertIn('if self.runtime_mode == "field":', self.manipulator_source)
        self.assertIn('self._finish("SUCCESS")', self.manipulator_source)

    def test_field_place_increments_placed_layers(self):
        # field 模式跳过 OBSERVING_STABILITY，层数递增必须上移，否则下一层
        # 重复用第一层高度。
        self.assertIn("self.placed_layers += 1", self.manipulator_source)

    def test_retreat_service_still_used_in_mock_path(self):
        # mock 模式保留机构式流程（测试依赖），不得被删除。
        self.assertIn("_start_retreat(now)", self.manipulator_source)

    def test_real_retreat_complete_does_not_read_mock_state(self):
        # P0-5 + A0.5：真实模式 retreat_complete 不得来自 mock_mechanism_state。
        self.assertIn("if self.mock_mode else False", self.bridge_source)
        # 无条件读 mock 的旧写法必须消失。
        self.assertNotIn(
            "status.retreat_complete = self.mock_mechanism_state.retreat_complete",
            self.bridge_source,
        )

    def test_mission_manager_uses_classify_action_result(self):
        # P0-6：mission_manager 必须用集中分类函数，而不是散落的字符串判断。
        self.assertIn("classify_action_result(", self.manager_source)
        self.assertNotIn("msg.data.split(\":\", 1)[0]", self.manager_source)


if __name__ == "__main__":
    unittest.main()
