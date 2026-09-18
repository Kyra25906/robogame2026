import unittest

from robogame_core.mission import (
    MissionConfig,
    MissionMachine,
    MissionState,
    line_calibration_blocker,
)
from robogame_core.models import MissionResult


class MissionTests(unittest.TestCase):
    def start(self, machine):
        machine.tick(action_succeeded=True)
        machine.tick(physical_start=True)
        self.assertEqual(machine.state, MissionState.GO_TO_ORANGE)

    def test_single_cube_mission(self):
        machine = MissionMachine(MissionConfig(orange_target=1, purple_target=0))
        self.start(machine)
        for _ in range(5):
            machine.tick(action_succeeded=True)
        self.assertEqual(machine.state, MissionState.VERIFY_BUILD)
        machine.tick(action_succeeded=True)
        self.assertEqual(machine.state, MissionState.COMPLETE)
        self.assertEqual(machine.result, MissionResult.SUCCESS)
        self.assertEqual(machine.cargo.total, 0)

    def test_roof_tower_order_and_capacity(self):
        machine = MissionMachine(MissionConfig(orange_target=2, purple_target=1))
        self.start(machine)
        visited = []
        while machine.state not in {MissionState.COMPLETE, MissionState.FAILED}:
            visited.append(machine.state)
            machine.tick(action_succeeded=True)
        self.assertEqual(machine.result, MissionResult.SUCCESS)
        self.assertEqual(visited.count(MissionState.PICK_ORANGE), 2)
        self.assertEqual(visited.count(MissionState.PLACE_ORANGE), 2)
        self.assertLess(visited.index(MissionState.PLACE_ORANGE), visited.index(MissionState.PLACE_PURPLE))
        self.assertEqual(machine.cargo.total, 0)

    def test_emergency_stop_is_terminal(self):
        machine = MissionMachine()
        machine.tick(emergency_stop=True)
        self.assertEqual(machine.state, MissionState.SAFE_STOP)
        self.assertEqual(machine.result, MissionResult.SAFETY_STOP)

    def test_timeout_has_finite_retries(self):
        machine = MissionMachine(MissionConfig(max_retries=2, state_timeout_s=1.0))
        # A2: 初始状态是 WAIT_FOR_COMMUNICATION，通信就绪后第一拍推进到
        # SELF_CHECK（entered_at 重置为当前时刻），之后再手动拨慢时钟测超时重试。
        machine.tick(now=1.0)
        machine.entered_at = 0.0
        machine.tick(now=2.0)
        self.assertEqual(machine.retries, 1)
        machine.tick(now=4.0)
        self.assertEqual(machine.retries, 2)
        machine.tick(now=6.0)
        self.assertEqual(machine.state, MissionState.FAILED)


class LineCalibrationGateTests(unittest.TestCase):
    """B4 开赛门：标定不可用时**拒绝开赛**，并且要说清为什么。

    真实问题：上电自主模式下没有人会在赛前看一眼归一化读数。标定不可用时
    巡线偏差是假的，车会「看起来在巡线」地走错路线——赛中无法补救。
    """

    def _at_physical_start(self, config, ready):
        machine = MissionMachine(config)
        machine.tick(action_succeeded=True)  # SELF_CHECK → WAIT_FOR_PHYSICAL_START
        self.assertEqual(machine.state, MissionState.WAIT_FOR_PHYSICAL_START)
        machine.tick(physical_start=True, line_calibration_ready=ready)
        return machine

    def test_gate_disabled_by_default_starts_anyway(self):
        """默认不开门：单元测试与 mock 演示不接真线，不能因为缺标定就跑不起来。"""
        machine = self._at_physical_start(MissionConfig(), None)
        self.assertEqual(machine.state, MissionState.GO_TO_ORANGE)

    def test_ready_calibration_starts_the_route_mode(self):
        machine = self._at_physical_start(MissionConfig(require_line_calibration=True), True)
        self.assertEqual(machine.state, MissionState.GO_TO_ORANGE)

    def test_unready_calibration_fails_loudly_instead_of_starting(self):
        machine = self._at_physical_start(MissionConfig(require_line_calibration=True), False)
        self.assertEqual(machine.state, MissionState.FAILED)
        self.assertEqual(machine.result, MissionResult.MECHANISM_ERROR)
        self.assertIn("标定不可用", machine.detail)
        self.assertIn("网页面板", machine.detail, "原因必须告诉人下一步怎么做")

    def test_unknown_calibration_state_also_blocks(self):
        """「不知道」当「不行」：巡线节点没上报时不能默认可用。"""
        machine = self._at_physical_start(MissionConfig(require_line_calibration=True), None)
        self.assertEqual(machine.state, MissionState.FAILED)
        self.assertIn("未上报", machine.detail)

    def test_blocker_text_distinguishes_unknown_from_unusable(self):
        """两种拒绝必须是不同的原因字符串，否则现场不知道该查哪一头。"""
        config = MissionConfig(require_line_calibration=True)
        self.assertIsNone(line_calibration_blocker(config, True))
        unusable = line_calibration_blocker(config, False)
        unknown = line_calibration_blocker(config, None)
        self.assertIsNotNone(unusable)
        self.assertIsNotNone(unknown)
        self.assertNotEqual(unusable, unknown)

    def test_gate_is_evaluated_only_at_physical_start(self):
        """SELF_CHECK 阶段不评估：标定可能在自检之后才被面板推送/落盘。"""
        machine = MissionMachine(MissionConfig(require_line_calibration=True))
        machine.tick(action_succeeded=True, line_calibration_ready=False)
        self.assertEqual(
            machine.state, MissionState.WAIT_FOR_PHYSICAL_START,
            "自检阶段不该因为标定缺失就判死（还有机会补标定）",
        )


if __name__ == "__main__":
    unittest.main()
