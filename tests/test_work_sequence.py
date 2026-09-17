"""作业段动作序列的单元测试（纯逻辑，零 ROS）。

这是「取 3 块」「搭 2 层」真正被展开成动作的地方，测试要钉住：

1. **序列由计划推导**：抓 3 块 = 抓→侧移→抓→侧移→抓；摆法 `2+1` =
   放→侧移→放→退回→放；摆法 `1+1` = 放→放（没有侧移）。
2. **车体系侧移换算**：车头朝 +y 时「右侧」= +x（符号写错就会撞墙，必须有测试）。
3. **结果来源必须匹配**：车体侧移的成功不能被记成「抓了一块」。
4. 步数与取命令次数一致（第 4 次作业没有命令可发）。
"""

from __future__ import annotations

import math
import unittest

from robogame_core.mission_route import CargoPlan, build_route_plan
from robogame_core.models import Pose2D
from robogame_core.route_loader import load_survey
from robogame_core.work_sequence import (
    DEFAULT_BUILD_PITCH_M,
    DEFAULT_SLOT_PITCH_M,
    WorkPlan,
    WorkStep,
    WorkStepKind,
    shifted_pose,
    work_steps,
)


def _plan():
    return build_route_plan(load_survey())


class ShiftGeometryTests(unittest.TestCase):
    def test_lateral_positive_is_the_cars_right(self):
        """车头朝 +y（W02/W04 的朝向）时，右侧是 +x。符号错了会往墙上撞。"""
        facing_plus_y = Pose2D(3.5, 4.7, math.pi / 2)
        moved = shifted_pose(facing_plus_y, lateral_m=0.15)
        self.assertAlmostEqual(moved.x, 3.5 + 0.15, places=9)
        self.assertAlmostEqual(moved.y, 4.7, places=9)

    def test_forward_moves_along_heading(self):
        facing_plus_y = Pose2D(3.5, 4.7, math.pi / 2)
        moved = shifted_pose(facing_plus_y, forward_m=0.20)
        self.assertAlmostEqual(moved.x, 3.5, places=9)
        self.assertAlmostEqual(moved.y, 4.9, places=9)

    def test_heading_is_preserved(self):
        pose = Pose2D(1.0, 2.0, -math.pi / 2)
        moved = shifted_pose(pose, forward_m=0.3, lateral_m=0.2)
        self.assertAlmostEqual(moved.yaw, pose.yaw)
        # 朝 -y 时右侧是 -x
        self.assertAlmostEqual(moved.x, 1.0 - 0.2, places=9)
        self.assertAlmostEqual(moved.y, 2.0 - 0.3, places=9)

    def test_non_finite_shift_is_rejected(self):
        with self.assertRaises(ValueError):
            shifted_pose(Pose2D(0.0, 0.0, 0.0), lateral_m=float("nan"))


class PickSequenceTests(unittest.TestCase):
    def test_three_picks_with_two_shifts(self):
        route = _plan()
        steps = work_steps(route.segment("S06_PICK3"), route.cargo_plan)
        self.assertEqual(
            [step.kind for step in steps],
            [WorkStepKind.PICK, WorkStepKind.SHIFT, WorkStepKind.PICK,
             WorkStepKind.SHIFT, WorkStepKind.PICK],
        )
        picks = [step for step in steps if step.kind is WorkStepKind.PICK]
        self.assertEqual([step.command for step in picks], ["PICK_ORANGE"] * 3)
        shifts = [step for step in steps if step.kind is WorkStepKind.SHIFT]
        self.assertEqual([step.lateral_m for step in shifts], [DEFAULT_SLOT_PITCH_M] * 2)
        self.assertEqual([step.forward_m for step in shifts], [0.0, 0.0])

    def test_slot_pitch_is_configurable_and_recorded_in_labels(self):
        route = _plan()
        steps = work_steps(route.segment("S06_PICK3"), route.cargo_plan, slot_pitch_m=0.18)
        shift = next(step for step in steps if step.kind is WorkStepKind.SHIFT)
        self.assertAlmostEqual(shift.lateral_m, 0.18)
        self.assertIn("0.18", shift.label)

    def test_result_sources_point_at_the_right_node(self):
        route = _plan()
        steps = work_steps(route.segment("S06_PICK3"), route.cargo_plan)
        for step in steps:
            if step.kind is WorkStepKind.SHIFT:
                self.assertEqual(step.result_source, "motion")
            else:
                self.assertEqual(step.result_source, "manipulator")

    def test_non_work_segments_have_no_steps(self):
        route = _plan()
        for segment in route.segments:
            if segment.id not in ("S06_PICK3", "S12_BUILD_2LAYER"):
                self.assertEqual(work_steps(segment, route.cargo_plan), (), segment.id)

    def test_more_required_than_cargo_is_rejected(self):
        route = _plan()
        segment = route.segment("S06_PICK3")
        too_few = CargoPlan(orange=1, purple=0, layers=1, layout="1")
        with self.assertRaises(ValueError):
            work_steps(segment, too_few)

    def test_non_positive_pitch_is_rejected(self):
        route = _plan()
        with self.assertRaises(ValueError):
            work_steps(route.segment("S06_PICK3"), route.cargo_plan, slot_pitch_m=0.0)


class PlaceSequenceTests(unittest.TestCase):
    def test_two_plus_one_layout(self):
        """底 2 + 顶 1：放 → 侧移 → 放 → 退回 → 放（共 5 步）。"""
        route = _plan()
        plan = CargoPlan(orange=3, purple=0, layers=2, layout="2+1")
        steps = work_steps(route.segment("S12_BUILD_2LAYER"), plan)
        self.assertEqual(
            [step.kind for step in steps],
            [WorkStepKind.PLACE, WorkStepKind.SHIFT, WorkStepKind.PLACE,
             WorkStepKind.SHIFT, WorkStepKind.PLACE],
        )
        shifts = [step for step in steps if step.kind is WorkStepKind.SHIFT]
        self.assertAlmostEqual(shifts[0].lateral_m, DEFAULT_BUILD_PITCH_M)
        self.assertAlmostEqual(shifts[1].lateral_m, -DEFAULT_BUILD_PITCH_M,
                               msg="换层必须退回中间")
        places = [step for step in steps if step.kind is WorkStepKind.PLACE]
        self.assertEqual([step.command for step in places], ["PLACE_ORANGE"] * 3)
        self.assertIn("第 1 层", places[0].label)
        self.assertIn("第 2 层", places[2].label)

    def test_one_plus_one_layout_has_no_shift(self):
        """底 1 顶 1（2 块 2 层）：直接放两次，不侧移（层高由配置决定）。

        注意：本路线当前是 3 块（required_count=3），所以这里用「同一段只要放 2 块」
        的变体来演示摆法如何改变序列——`RoutePlan` 会强制 required_count 与载货
        块数一致（见 tests/test_mission_route.py 的对应负例）。
        """
        import dataclasses

        route = _plan()
        segment = dataclasses.replace(
            route.segment("S12_BUILD_2LAYER"),
            exit=dataclasses.replace(route.segment("S12_BUILD_2LAYER").exit, required_count=2),
        )
        plan = CargoPlan(orange=2, purple=0, layers=2, layout="1+1")
        steps = work_steps(segment, plan)
        self.assertEqual(
            [step.kind for step in steps],
            [WorkStepKind.PLACE, WorkStepKind.PLACE],
        )

    def test_required_count_must_match_the_cargo_plan(self):
        """抓 3 次但只有 2 块这种错误必须在构造路线时就红，而不是到现场才发现。"""
        import dataclasses

        from robogame_core.mission_route import CargoPlan as RouteCargoPlan
        from robogame_core.mission_route import RoutePlanError

        route = _plan()
        two_cubes = RouteCargoPlan(orange=2, purple=0, layers=2, layout="1+1")
        with self.assertRaises(RoutePlanError) as caught:
            dataclasses.replace(route, cargo_plan=two_cubes)
        self.assertIn("required_count", str(caught.exception))

    def test_three_layer_layout_expands_per_layer(self):
        route = _plan()
        plan = CargoPlan(orange=3, purple=0, layers=3, layout="1+1+1")
        steps = work_steps(route.segment("S12_BUILD_2LAYER"), plan)
        self.assertEqual(
            [step.kind for step in steps], [WorkStepKind.PLACE] * 3
        )


class WorkPlanStateTests(unittest.TestCase):
    def _plan_object(self) -> WorkPlan:
        route = _plan()
        return WorkPlan(steps=work_steps(route.segment("S06_PICK3"), route.cargo_plan))

    def test_source_must_match_before_advancing(self):
        """侧移的成功不能被记成抓了一块（同一回调入口，必须区分）。"""
        plan = self._plan_object()
        self.assertIs(plan.current.kind, WorkStepKind.PICK)
        self.assertFalse(plan.advance(result_source="motion"), "机构步不接受运动结果")
        self.assertEqual(plan.index, 0)
        self.assertTrue(plan.advance(result_source="manipulator"))
        self.assertIs(plan.current.kind, WorkStepKind.SHIFT)
        self.assertFalse(plan.advance(result_source="manipulator"), "侧移步不接受机构结果")
        self.assertTrue(plan.advance(result_source="motion"))
        self.assertIs(plan.current.kind, WorkStepKind.PICK)

    def test_running_the_whole_sequence(self):
        plan = self._plan_object()
        guard = 0
        while not plan.is_complete and guard < 20:
            guard += 1
            self.assertTrue(plan.advance(result_source=plan.current.result_source))
        self.assertTrue(plan.is_complete)
        self.assertIsNone(plan.current)
        self.assertEqual(plan.total, 5)
        self.assertIn("已完成 5 步", plan.progress_text())

    def test_progress_text_is_readable(self):
        plan = self._plan_object()
        self.assertIn("第 1/5 步", plan.progress_text())
        self.assertIn("抓第 1 块", plan.progress_text())

    def test_reset_restarts_the_sequence(self):
        plan = self._plan_object()
        plan.advance(result_source="manipulator")
        plan.reset()
        self.assertEqual(plan.index, 0)
        self.assertIs(plan.current.kind, WorkStepKind.PICK)

    def test_advancing_a_finished_plan_is_a_no_op(self):
        plan = WorkPlan(steps=())
        self.assertFalse(plan.advance(result_source="manipulator"))
        self.assertTrue(plan.is_complete)


class StepValidationTests(unittest.TestCase):
    def test_shift_step_requires_motion_and_no_command(self):
        with self.assertRaises(ValueError):
            WorkStep(kind=WorkStepKind.SHIFT, label="x", index=0)
        with self.assertRaises(ValueError):
            WorkStep(kind=WorkStepKind.SHIFT, label="x", index=0, command="PICK_ORANGE",
                     lateral_m=0.1)

    def test_work_step_requires_command_and_no_motion(self):
        with self.assertRaises(ValueError):
            WorkStep(kind=WorkStepKind.PICK, label="x", index=0)
        with self.assertRaises(ValueError):
            WorkStep(kind=WorkStepKind.PICK, label="x", index=0, command="PICK_ORANGE",
                     lateral_m=0.1)

    def test_negative_index_is_rejected(self):
        with self.assertRaises(ValueError):
            WorkStep(kind=WorkStepKind.PICK, label="x", index=-1, command="PICK_ORANGE")

    def test_distance_is_reported_for_shifts(self):
        step = WorkStep(kind=WorkStepKind.SHIFT, label="x", index=0, lateral_m=0.15)
        self.assertAlmostEqual(step.distance_m, 0.15)


if __name__ == "__main__":
    unittest.main()
