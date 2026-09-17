"""失败降级阶梯的测试（纯逻辑，零 ROS）。

降级是「6 分钟无人干预」的收场策略，测试要钉住两条底线：

1. **位置/朝向不可信时一律安全停车**（丢线、没转过去、微移失败）——不许在
   没有依据的情况下继续运动；
2. **位置可信的作业失败才允许继续**（取块失败但有存货 → 去搭建；放置失败 → 撤退），
   且必须真的跳到目标段、把原因写清楚。

另外测 `RouteRunner.skip_to()`（跳段后判据要从头算）与状态机级行为
（重试耗尽 → 降级 → 继续跑完 / 或判失败）。
"""

from __future__ import annotations

import unittest

from robogame_core.mission import MissionConfig, MissionMachine, MissionState
from robogame_core.mission_recovery import (
    RecoveryAction,
    recovery_for_failure,
)
from robogame_core.mission_route import (
    ExitKind,
    RouteRunner,
    SegmentRole,
    WorkKind,
    build_route_plan,
)
from robogame_core.models import Cargo, CubeColor, MissionResult
from robogame_core.route_loader import load_survey


def _plan():
    return build_route_plan(load_survey())


def _cargo(orange: int = 0) -> Cargo:
    cargo = Cargo()
    for _ in range(orange):
        cargo.add(CubeColor.ORANGE)
    return cargo


class RecoveryDecisionTests(unittest.TestCase):
    def test_pick_failure_with_cargo_skips_to_build(self):
        plan = _plan()
        decision = recovery_for_failure(plan, plan.segment("S06_PICK3"), _cargo(1))
        self.assertIs(decision.action, RecoveryAction.SKIP_TO_BUILD)
        self.assertEqual(decision.target_segment_id, "S12_BUILD_2LAYER")
        self.assertIn("已有 1 块", decision.reason)

    def test_pick_failure_without_cargo_retreats(self):
        plan = _plan()
        decision = recovery_for_failure(plan, plan.segment("S06_PICK3"), _cargo(0))
        self.assertIs(decision.action, RecoveryAction.RETREAT)
        self.assertEqual(decision.target_segment_id, "S13_RETREAT")
        self.assertIn("没什么可搭", decision.reason)

    def test_place_failure_retreats(self):
        plan = _plan()
        decision = recovery_for_failure(plan, plan.segment("S12_BUILD_2LAYER"), _cargo(2))
        self.assertIs(decision.action, RecoveryAction.RETREAT)
        self.assertIn("搭建区", decision.reason)

    def test_movement_and_turn_failures_always_safe_stop(self):
        """位置/朝向不可信 → 一律停车；这是阶梯的底线。"""
        plan = _plan()
        for segment_id in (
            "S01_LINE_START", "S02_LINE_MAIN", "S03_RAMP_APPROACH", "S04_RAMP_UP",
            "S05_LINE_PLATFORM", "S07_TURN_AROUND", "S09_RAMP_DOWN",
            "S10_LINE_BACK_MAIN", "S11_SHIFT_TO_BUILD", "S13_RETREAT",
        ):
            decision = recovery_for_failure(plan, plan.segment(segment_id), _cargo(3))
            self.assertIs(
                decision.action, RecoveryAction.SAFE_STOP,
                f"{segment_id} 失败后不许继续运动",
            )
            self.assertIsNone(decision.target_segment_id)

    def test_unknown_inputs_converge_to_safe_stop(self):
        plan = _plan()
        self.assertIs(
            recovery_for_failure(plan, None, _cargo(1)).action, RecoveryAction.SAFE_STOP
        )
        self.assertIs(
            recovery_for_failure(None, plan.segment("S01_LINE_START"), _cargo(1)).action,
            RecoveryAction.SAFE_STOP,
        )

    def test_skip_target_is_a_work_place_segment(self):
        plan = _plan()
        decision = recovery_for_failure(plan, plan.segment("S06_PICK3"), _cargo(3))
        target = plan.segment(decision.target_segment_id)
        self.assertIs(target.role, SegmentRole.WORK)
        self.assertIs(target.work, WorkKind.PLACE)

    def test_retreat_target_is_the_last_segment(self):
        plan = _plan()
        decision = recovery_for_failure(plan, plan.segment("S06_PICK3"), _cargo(0))
        self.assertEqual(decision.target_segment_id, plan.retreat_segment_id)
        self.assertEqual(plan.retreat_segment_id, plan.segments[-1].id)


class SkipToTests(unittest.TestCase):
    def test_skip_jumps_and_resets_per_segment_counters(self):
        plan = _plan()
        runner = RouteRunner(plan)
        runner.start()
        runner.observe_distance(0.42)
        runner.note_work_done()
        detail = runner.skip_to("S12_BUILD_2LAYER")
        self.assertIn("S12_BUILD_2LAYER", detail)
        self.assertEqual(runner.current_segment.id, "S12_BUILD_2LAYER")
        self.assertEqual(runner.segment_index, plan.segment_ids.index("S12_BUILD_2LAYER"))
        self.assertEqual(runner.observations.distance_m, 0.0)
        self.assertEqual(runner.observations.work_count, 0)

    def test_skip_backwards_is_allowed(self):
        """放置失败 → 退到撤退段时是往前跳，但也要支持向后跳（例如现场排障）。"""
        plan = _plan()
        runner = RouteRunner(plan)
        runner.start()
        runner.skip_to("S12_BUILD_2LAYER")
        runner.skip_to("S01_LINE_START")
        self.assertEqual(runner.segment_index, 0)

    def test_unknown_target_is_rejected(self):
        runner = RouteRunner(_plan())
        runner.start()
        with self.assertRaises(ValueError):
            runner.skip_to("S99_NOPE")
        self.assertEqual(runner.segment_index, 0)

    def test_skip_before_start_is_rejected(self):
        runner = RouteRunner(_plan())
        with self.assertRaises(ValueError):
            runner.skip_to("S12_BUILD_2LAYER")


class MachineDegradationTests(unittest.TestCase):
    """状态机级：重试耗尽后的实际行为。"""

    def _machine_at_route_start(self, **config) -> MissionMachine:
        machine = MissionMachine(MissionConfig(**config), route=_plan())
        machine.tick(action_succeeded=True)
        machine.tick(physical_start=True)
        self.assertIs(machine.state, MissionState.ROUTE_RUNNING)
        return machine

    def _fail_until_segment_changes(self, machine, *, failure_result, attempts=6) -> None:
        """一直失败直到段发生变化（重试耗尽 → 降级），或状态离开 ROUTE_RUNNING。

        注意失败次数：`max_retries=1` 时是**第二次**失败触发降级——
        多打一拍会把新段重新带进重试，测试与实现都要按这个节奏走。
        """
        start = machine.segment_id
        for _ in range(attempts):
            machine.tick(action_failed=True, failure_result=failure_result,
                         failure_detail="segment failed")
            if machine.segment_id != start or machine.state is not MissionState.ROUTE_RUNNING:
                return
        self.fail(f"段没有变化也没失败：{machine.segment_id} / {machine.state}")

    def test_pick_failure_with_cargo_continues_to_build(self):
        machine = self._machine_at_route_start(max_retries=1)
        runner = machine.route_runner
        # 直接跳到取块段，并给框里放一块（模拟已抓到一块）
        runner.skip_to("S06_PICK3")
        machine.cargo.add(CubeColor.ORANGE)
        machine._sync_route()
        self._fail_until_segment_changes(machine, failure_result=MissionResult.TIMEOUT)
        self.assertIs(machine.state, MissionState.ROUTE_RUNNING, "有存货时不该判死")
        self.assertEqual(machine.segment_id, "S12_BUILD_2LAYER")
        self.assertEqual(machine.degradations, 1)
        self.assertIn("degraded", machine.detail)
        self.assertIn("S12_BUILD_2LAYER", machine.detail)

    def test_pick_failure_without_cargo_retreats_not_fails(self):
        machine = self._machine_at_route_start(max_retries=1)
        machine.route_runner.skip_to("S06_PICK3")
        machine._sync_route()
        self._fail_until_segment_changes(machine, failure_result=MissionResult.MECHANISM_ERROR)
        self.assertIs(machine.state, MissionState.ROUTE_RUNNING)
        self.assertEqual(machine.segment_id, "S13_RETREAT")
        self.assertEqual(machine.degradations, 1)

    def test_line_failure_still_fails_safe(self):
        machine = self._machine_at_route_start(max_retries=1)
        self._fail_until_segment_changes(machine, failure_result=MissionResult.TARGET_LOST)
        self.assertIs(machine.state, MissionState.FAILED)
        self.assertIs(machine.result, MissionResult.TARGET_LOST)
        self.assertIn("安全停车", machine.detail)
        self.assertEqual(machine.degradations, 0)

    def test_degradation_can_be_disabled(self):
        machine = self._machine_at_route_start(max_retries=1, degrade_on_failure=False)
        machine.route_runner.skip_to("S06_PICK3")
        machine.cargo.add(CubeColor.ORANGE)
        machine._sync_route()
        self._fail_until_segment_changes(machine, failure_result=MissionResult.TIMEOUT)
        self.assertIs(machine.state, MissionState.FAILED)
        self.assertEqual(machine.degradations, 0)

    def test_retries_are_reset_after_a_degradation(self):
        machine = self._machine_at_route_start(max_retries=1)
        machine.route_runner.skip_to("S06_PICK3")
        machine.cargo.add(CubeColor.ORANGE)
        machine._sync_route()
        self._fail_until_segment_changes(machine, failure_result=MissionResult.TIMEOUT)
        self.assertEqual(machine.retries, 0, "降级后应重新拥有完整重试预算")

    def test_route_progress_reports_degradations(self):
        machine = self._machine_at_route_start(max_retries=1)
        machine.route_runner.skip_to("S06_PICK3")
        machine.cargo.add(CubeColor.ORANGE)
        machine._sync_route()
        self._fail_until_segment_changes(machine, failure_result=MissionResult.TIMEOUT)
        progress = machine.route_progress()
        self.assertEqual(progress["degradations"], 1)
        self.assertEqual(progress["segment_id"], "S12_BUILD_2LAYER")

    def test_legacy_mode_is_unaffected(self):
        """没有绑定路线时行为不变（旧演示流程 / 既有测试依赖它）。"""
        machine = MissionMachine(MissionConfig(max_retries=1))
        machine.tick(action_succeeded=True)
        machine.tick(physical_start=True)
        self.assertIs(machine.state, MissionState.GO_TO_ORANGE)
        machine.cargo.add(CubeColor.ORANGE)
        for _ in range(3):
            machine.tick(action_failed=True, failure_result=MissionResult.TIMEOUT,
                         failure_detail="boom")
        self.assertIs(machine.state, MissionState.FAILED)
        self.assertEqual(machine.degradations, 0)


if __name__ == "__main__":
    unittest.main()
