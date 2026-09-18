"""B2 分发决策与巡线状态解析的单元测试（纯逻辑，零 ROS）。

这一层是「无人干预自主完赛」里最危险的一环：**谁有权驱动底盘**。测试要钉住：

1. 每个段类型只授权一个来源，且与段类型语义一致（巡线→巡线、平移/转向→位姿控制、
   作业→视觉对准、结束→谁都不许）；
2. 发给机构的命令只用 `manipulator_client` 既有的四个命令，不发明新协议；
3. `/line_follow/status` 解析**失败必须 fail-safe**（stale=True），不能让格式变化
   变成「线还在」的假证据；解析器与 `line_follow_node` 实际发布格式绑定；
4. 「3 块搭 2 层」对放置高度的要求算得出来，且与 `robot.yaml` 现状的差异被显式报出。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from robogame_core.cmd_vel_arbiter import SOURCE_ALIGN, SOURCE_LINE_FOLLOW, SOURCE_NAVIGATE
from robogame_core.line_follow import LineSensorState
from robogame_core.mission_dispatch import (
    ROLE_ACTIVE_SOURCE,
    SOURCE_NONE,
    WORK_COMMANDS,
    active_source_for,
    decide,
    goal_for,
    layer_counts,
    layer_height_m,
    parse_line_status,
    placement_heights_issue,
    required_placement_heights,
    work_command_for,
    work_sequence,
)
from robogame_core.mission_route import (
    CargoPlan,
    ExitKind,
    RoutePlan,
    SegmentRole,
    WorkKind,
    build_route_plan,
)
from robogame_core.models import CubeColor
from tools.field_dashboard_core import load_survey

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROBOT_YAML = PROJECT_ROOT / "ros2_ws/src/robogame_bringup/config/robot.yaml"
LINE_NODE = PROJECT_ROOT / "ros2_ws/src/motion_control/motion_control/line_follow_node.py"


def _plan() -> RoutePlan:
    return build_route_plan(load_survey())


def _configured_place_heights() -> list[float]:
    text = ROBOT_YAML.read_text(encoding="utf-8")
    match = re.search(r"^\s*place_heights_m:\s*\[([^\]]*)\]", text, re.MULTILINE)
    assert match, "robot.yaml 必须显式写出 place_heights_m"
    return [float(item) for item in match.group(1).split(",")]


class ActiveSourceTests(unittest.TestCase):
    def test_every_segment_role_has_exactly_one_authorized_source(self):
        self.assertEqual(set(ROLE_ACTIVE_SOURCE), set(SegmentRole))
        for role in SegmentRole:
            source = ROLE_ACTIVE_SOURCE[role]
            self.assertIn(
                source, {SOURCE_LINE_FOLLOW, SOURCE_NAVIGATE, SOURCE_ALIGN, SOURCE_NONE}, role
            )

    def test_role_to_source_semantics(self):
        route = _plan()
        self.assertEqual(
            active_source_for(route.segment("S01_LINE_START")), SOURCE_LINE_FOLLOW
        )
        self.assertEqual(
            active_source_for(route.segment("S04_RAMP_UP")), SOURCE_LINE_FOLLOW
        )
        self.assertEqual(
            active_source_for(route.segment("S09_RAMP_DOWN")), SOURCE_LINE_FOLLOW
        )
        self.assertEqual(
            active_source_for(route.segment("S11_SHIFT_TO_BUILD")), SOURCE_NAVIGATE
        )
        self.assertEqual(
            active_source_for(route.segment("S07_TURN_AROUND")), SOURCE_NAVIGATE
        )
        self.assertEqual(active_source_for(route.segment("S06_PICK3")), SOURCE_ALIGN)
        self.assertEqual(active_source_for(route.segment("S12_BUILD_2LAYER")), SOURCE_ALIGN)

    def test_finished_route_authorizes_nobody(self):
        self.assertEqual(active_source_for(None), SOURCE_NONE)
        decision = decide(None, CargoPlan())
        self.assertEqual(decision.active_source, SOURCE_NONE)
        self.assertFalse(decision.drives_chassis)
        self.assertIsNone(decision.goal)
        self.assertIsNone(decision.work_command)

    def test_only_shift_and_turn_segments_send_motion_goals(self):
        route = _plan()
        for segment in route.segments:
            goal = goal_for(segment)
            if segment.role in (SegmentRole.SHIFT, SegmentRole.TURN):
                self.assertEqual(goal, segment.to_pose, segment.id)
            else:
                self.assertIsNone(goal, f"{segment.id} 不该发 /motion/goal")
        # 原地转向段：目标位置与起点相同、朝向不同
        turn = route.segment("S07_TURN_AROUND")
        self.assertEqual((turn.to_pose.x, turn.to_pose.y), (turn.from_pose.x, turn.from_pose.y))
        self.assertNotAlmostEqual(turn.to_pose.yaw, turn.from_pose.yaw)


class WorkCommandTests(unittest.TestCase):
    def test_work_sequence_follows_the_cargo_plan(self):
        self.assertEqual(
            work_sequence(CargoPlan(orange=3, purple=0)), (CubeColor.ORANGE,) * 3
        )
        self.assertEqual(
            work_sequence(CargoPlan(orange=2, purple=1)),
            (CubeColor.ORANGE, CubeColor.ORANGE, CubeColor.PURPLE),
        )

    def test_pick_commands_count_three_times_then_stop(self):
        route = _plan()
        segment = route.segment("S06_PICK3")
        plan = route.cargo_plan
        self.assertEqual(
            [work_command_for(segment, plan, index) for index in range(3)],
            ["PICK_ORANGE"] * 3,
        )
        self.assertIsNone(work_command_for(segment, plan, 3))
        self.assertIsNone(work_command_for(segment, plan, 99))

    def test_place_commands_use_the_same_contract(self):
        route = _plan()
        segment = route.segment("S12_BUILD_2LAYER")
        self.assertEqual(
            [work_command_for(segment, route.cargo_plan, index) for index in range(3)],
            ["PLACE_ORANGE"] * 3,
        )

    def test_only_known_manipulator_commands_are_generated(self):
        """不许发明新命令：manipulator_client 只认这四条（node.py:177）。"""
        node = (
            PROJECT_ROOT / "ros2_ws/src/manipulator_client/manipulator_client/node.py"
        ).read_text(encoding="utf-8")
        accepted = set(re.findall(r'"(PICK_[A-Z]+|PLACE_[A-Z]+)"', node))
        self.assertTrue(accepted)
        for command in WORK_COMMANDS.values():
            self.assertIn(command, accepted, f"{command} 不在机构侧接受集合里")

    def test_non_work_segments_never_send_commands(self):
        route = _plan()
        for segment in route.segments:
            if segment.role is SegmentRole.WORK:
                continue
            self.assertIsNone(work_command_for(segment, route.cargo_plan, 0), segment.id)

    def test_negative_work_index_is_rejected(self):
        route = _plan()
        with self.assertRaises(ValueError):
            work_command_for(route.segment("S06_PICK3"), route.cargo_plan, -1)

    def test_work_segments_declare_their_kind(self):
        route = _plan()
        self.assertIs(route.segment("S06_PICK3").work, WorkKind.PICK)
        self.assertIs(route.segment("S12_BUILD_2LAYER").work, WorkKind.PLACE)
        for segment in route.segments:
            if segment.role is not SegmentRole.WORK:
                self.assertIsNone(segment.work, segment.id)


class DecideTests(unittest.TestCase):
    def test_decide_covers_all_thirteen_segments(self):
        route = _plan()
        seen_sources = set()
        for segment in route.segments:
            decision = decide(segment, route.cargo_plan, 0)
            self.assertEqual(decision.segment_id, segment.id)
            self.assertEqual(decision.active_source, ROLE_ACTIVE_SOURCE[segment.role])
            self.assertIn(segment.id, decision.reason)
            seen_sources.add(decision.active_source)
            if segment.role is SegmentRole.WORK:
                self.assertTrue(decision.is_work)
                self.assertIsNotNone(decision.work_command)
            else:
                self.assertFalse(decision.is_work)
                self.assertIsNone(decision.work_command)
        # 三种驱动来源都被用到（否则说明路线里少了某类段）
        self.assertEqual(seen_sources, {SOURCE_LINE_FOLLOW, SOURCE_NAVIGATE, SOURCE_ALIGN})

    def test_work_index_is_consumed_per_segment(self):
        """段内第 4 次作业没有命令可发（判据要求 3 次，多余的一次说明计数错了）。"""
        route = _plan()
        self.assertIsNone(decide(route.segment("S06_PICK3"), route.cargo_plan, 3).work_command)
        self.assertEqual(
            decide(route.segment("S06_PICK3"), route.cargo_plan, 2).work_command, "PICK_ORANGE"
        )

    def test_each_segment_authorizes_only_one_kind_of_control(self):
        """同段不许同时「发导航目标」和「让巡线走」——这正是抢 /cmd_vel 的根因。"""
        route = _plan()
        for segment in route.segments:
            decision = decide(segment, route.cargo_plan, 0)
            if decision.goal is not None:
                self.assertEqual(decision.active_source, SOURCE_NAVIGATE, segment.id)
            if decision.work_command is not None:
                self.assertEqual(decision.active_source, SOURCE_ALIGN, segment.id)
            if decision.active_source == SOURCE_LINE_FOLLOW:
                self.assertIsNone(decision.goal, segment.id)
                self.assertIsNone(decision.work_command, segment.id)


class WorkCommandColorTests(unittest.TestCase):
    """机构命令 → 颜色的反查（载货簿记要用）。"""

    def test_every_contract_command_maps_to_its_color(self):
        from robogame_core.mission_dispatch import WORK_COMMANDS, color_from_work_command

        for (_kind, color), command in WORK_COMMANDS.items():
            self.assertIs(color_from_work_command(command), color, command)

    def test_unknown_or_empty_command_returns_none(self):
        """不认识就返回 None，**不猜**：猜错会让「车上还有几块」悄悄变假。"""
        from robogame_core.mission_dispatch import color_from_work_command

        for value in (None, "", "PICK_BLUE", "pick_orange"):
            self.assertIsNone(color_from_work_command(value), repr(value))

    def test_mapping_is_derived_from_the_command_table(self):
        """反查表必须由 `WORK_COMMANDS` 推出，不能再写一遍（两处会漂移）。"""
        source = (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "ros2_ws/src/robogame_core/robogame_core/mission_dispatch.py"
        ).read_text(encoding="utf-8")
        self.assertIn("for (_kind, color), command in WORK_COMMANDS.items()", source)


class LineStatusParsingTests(unittest.TestCase):
    def _status_text(self, **over) -> str:
        payload = {
            "state": "ON_LINE", "dev": -0.12, "lost": 0, "stale": False,
            "valid_frames": 120, "invalid_frames": 0, "blocked": False,
            "out_vx": 0.2, "out_wz": 0.05,
        }
        payload.update(over)
        return (
            f"state={payload['state']} dev={payload['dev']} lost={payload['lost']} "
            f"stale={payload['stale']} valid_frames={payload['valid_frames']} "
            f"invalid_frames={payload['invalid_frames']} blocked={payload['blocked']} #diag#"
            + json.dumps(payload)
        )

    def test_parses_the_structured_diag_payload(self):
        status = parse_line_status(self._status_text())
        self.assertIs(status.state, LineSensorState.ON_LINE)
        self.assertFalse(status.stale)
        self.assertFalse(status.blocked)
        self.assertAlmostEqual(status.deviation, -0.12)

    def test_junction_and_lost_states_round_trip(self):
        for name in ("INTERSECTION", "ALL_BLACK", "LOST", "LEFT_EDGE", "RIGHT_EDGE"):
            status = parse_line_status(self._status_text(state=name))
            self.assertEqual(status.state.value, name)

    def test_falls_back_to_prefix_key_values(self):
        status = parse_line_status("state=LOST dev=nan lost=6 stale=True blocked=True")
        self.assertIs(status.state, LineSensorState.LOST)
        self.assertTrue(status.stale)
        self.assertTrue(status.blocked)
        self.assertIsNone(status.deviation, "dev=nan 不该被当成一个数字")

    def test_garbage_is_stale_not_trusted(self):
        for text in ("", "   ", "no keys here", "state=WHAT? stale=False", "{}", "#diag#not json"):
            status = parse_line_status(text)
            self.assertTrue(status.stale, repr(text))
            self.assertTrue(status.blocked, repr(text))
            self.assertIs(status.state, LineSensorState.LOST)

    def test_unknown_state_is_stale_even_with_fresh_flag(self):
        status = parse_line_status(self._status_text(state="ON_LINE_ISH"))
        self.assertTrue(status.stale)

    def test_string_flags_are_parsed_consistently(self):
        status = parse_line_status("state=ON_LINE stale=false blocked=0")
        self.assertFalse(status.stale)
        self.assertFalse(status.blocked)

    def test_parser_contract_matches_the_publishing_node(self):
        """解析器依赖 line_follow_node 的 `#diag#` 标记与字段名；改了那边这里必须红。"""
        source = LINE_NODE.read_text(encoding="utf-8")
        self.assertIn("#diag#", source, "line_follow_node 的结构化状态标记变了")
        for key in ('"state"', '"stale"', '"blocked"', '"dev"'):
            self.assertIn(key, source, f"line_follow_node 不再上送 {key}")

    def test_calibration_ready_is_tri_state(self):
        """B4 开赛门：True / False / 字段缺失（None）必须是三个不同结论。

        为什么不能把缺失当 False：那样「老版本节点没上报」和「上报了但不可用」
        会得到同一个原因字符串，现场排查时看不出该去查哪一头。
        """
        self.assertIs(parse_line_status(self._status_text(calibration_ready=True)).calibration_ready, True)
        self.assertIs(parse_line_status(self._status_text(calibration_ready=False)).calibration_ready, False)
        self.assertIsNone(
            parse_line_status(self._status_text()).calibration_ready,
            "节点没上报该字段时必须保持未知，不能被当成「不可用」或「可用」",
        )

    def test_calibration_ready_accepts_string_flags(self):
        """面板/日志里常见字符串写法，两种格式都要一致。"""
        self.assertIs(parse_line_status("state=ON_LINE stale=False blocked=False calibration_ready=true").calibration_ready, True)
        self.assertIs(parse_line_status("state=ON_LINE stale=False blocked=False calibration_ready=0").calibration_ready, False)

    def test_node_publishes_calibration_ready(self):
        """字段存在性契约：解析器读得到，前提是节点真的上送这个名字。"""
        self.assertIn('"calibration_ready"', LINE_NODE.read_text(encoding="utf-8"))


class PlacementHeightTests(unittest.TestCase):
    def test_layer_counts_parse_the_layout(self):
        self.assertEqual(layer_counts(CargoPlan(orange=3, purple=0, layers=2, layout="2+1")), (2, 1))
        self.assertEqual(layer_counts(CargoPlan(orange=2, purple=0, layers=2, layout="1+1")), (1, 1))
        self.assertEqual(layer_counts(CargoPlan(orange=3, purple=0, layers=1, layout="3")), (3,))

    def test_layer_counts_reject_inconsistent_layouts(self):
        with self.assertRaises(ValueError):
            layer_counts(CargoPlan(orange=3, purple=0, layers=2, layout="1+1"))  # 只摆 2 块
        with self.assertRaises(ValueError):
            layer_counts(CargoPlan(orange=3, purple=0, layers=3, layout="2+1"))  # 层数不符
        with self.assertRaises(ValueError):
            layer_counts(CargoPlan(orange=3, purple=0, layers=2, layout="two+one"))

    def assertHeights(self, actual, expected, msg=None):
        """高度按 1e-9 容差比较（0.1+2×0.1 在二进制浮点里是 0.30000000000000004）。"""
        self.assertEqual(len(actual), len(expected), msg)
        for index, (got, want) in enumerate(zip(actual, expected)):
            self.assertAlmostEqual(got, want, places=9, msg=f"{msg} 第 {index + 1} 项")

    def test_required_placement_sequence_for_two_layers(self):
        """口径与 manipulator.select_place_height 一致：按「第几次放置」索引。"""
        plan_2plus1 = CargoPlan(orange=3, purple=0, layers=2, layout="2+1")
        self.assertHeights(required_placement_heights(plan_2plus1), [0.10, 0.10, 0.20])
        plan_3_layers = CargoPlan(orange=3, purple=0, layers=3, layout="1+1+1")
        self.assertHeights(required_placement_heights(plan_3_layers), [0.10, 0.20, 0.30])

    def test_layer_height_assumption_is_explicit(self):
        self.assertAlmostEqual(layer_height_m(0, base_height_m=0.10, cube_size_m=0.10), 0.10)
        self.assertAlmostEqual(layer_height_m(2, base_height_m=0.10, cube_size_m=0.10), 0.30)
        with self.assertRaises(ValueError):
            layer_height_m(-1, base_height_m=0.10, cube_size_m=0.10)

    def test_issue_message_names_every_mismatch(self):
        plan = CargoPlan(orange=3, purple=0, layers=2, layout="2+1")
        issue = placement_heights_issue(plan, [0.10, 0.20, 0.30])
        self.assertIsNotNone(issue)
        self.assertIn("第 2 次放置", issue)
        self.assertIn("第 3 次放置", issue)
        self.assertIn("搭建区台面", issue)

    def test_no_issue_when_config_matches_the_plan(self):
        plan = CargoPlan(orange=3, purple=0, layers=2, layout="2+1")
        self.assertIsNone(placement_heights_issue(plan, [0.10, 0.10, 0.20]))

    def test_two_cube_two_layer_layout_ignores_the_unused_third_height(self):
        """底 1 顶 1 只放 2 块：配置里第 3 项用不到，不算不一致。"""
        plan = CargoPlan(orange=2, purple=0, layers=2, layout="1+1")
        self.assertIsNone(placement_heights_issue(plan, [0.10, 0.20, 0.30]))

    def test_issue_reports_missing_layer_heights(self):
        plan = CargoPlan(orange=3, purple=0, layers=2, layout="2+1")
        issue = placement_heights_issue(plan, [0.10])
        self.assertIsNotNone(issue)
        self.assertIn("只有 1 项", issue)

    def test_real_config_is_currently_inconsistent_with_the_two_layer_plan(self):
        """现状（必须被看见）：robot.yaml 的层高序列会搭成 3 层，与「2 层」计划不符。

        这条测试把现状钉住：B3 现场定下摆法后，要么改 `place_heights_m` 为
        `[0.10, 0.10, 0.20]`（底 2 顶 1），要么把计划改成 3 层——两者必选其一，
        改完这条测试必须同步更新。
        """
        route = _plan()
        configured = _configured_place_heights()
        self.assertEqual(configured, [0.10, 0.20, 0.30])
        issue = placement_heights_issue(route.cargo_plan, configured)
        self.assertIsNotNone(issue, "计划写 2 层、配置给 3 个递增层高：必须报出来")
        self.assertIn("[0.1, 0.1, 0.2]", issue)


class LineAndRampCommandTests(unittest.TestCase):
    """B3：每段巡线限速 + 坡道参数（C3 接入）。"""

    def test_line_segments_carry_their_planned_speed(self):
        """路线登记表写了每段限速，此前巡线节点完全没采用——现在按段下发。"""
        from robogame_core.mission_dispatch import line_command_for

        route = _plan()
        commands = {
            segment.id: line_command_for(segment)
            for segment in route.segments
            if line_command_for(segment) is not None
        }
        self.assertEqual(
            set(commands),
            {
                "S01_LINE_START", "S02_LINE_MAIN", "S03_RAMP_APPROACH",
                "S04_RAMP_UP", "S05_LINE_PLATFORM", "S08_LINE_BACK_PLATFORM",
                "S09_RAMP_DOWN", "S10_LINE_BACK_MAIN",
            },
        )
        for segment_id, command in commands.items():
            segment = route.segment(segment_id)
            self.assertAlmostEqual(command.max_speed_mps, segment.max_speed_mps, msg=segment_id)

    def test_only_ramp_segments_carry_a_ramp_profile(self):
        from robogame_core.mission_dispatch import line_command_for

        route = _plan()
        ramps = {
            segment.id: line_command_for(segment)
            for segment in route.segments
            if (command := line_command_for(segment)) is not None and command.is_ramp
        }
        self.assertEqual(set(ramps), {"S04_RAMP_UP", "S09_RAMP_DOWN"})
        self.assertEqual(ramps["S04_RAMP_UP"].ramp_profile.kind.value, "RAMP_UP")
        self.assertEqual(ramps["S09_RAMP_DOWN"].ramp_profile.kind.value, "RAMP_DOWN")
        # 下坡的生效限速要比登记限速更保守（防冲）
        down = ramps["S09_RAMP_DOWN"]
        self.assertLess(
            down.ramp_profile.max_speed_mps * down.ramp_profile.descent_speed_factor,
            down.max_speed_mps + 1e-9,
        )

    def test_non_line_segments_produce_no_command(self):
        from robogame_core.mission_dispatch import line_command_for

        route = _plan()
        for segment in route.segments:
            if segment.role.value in ("WORK", "SHIFT", "TURN"):
                self.assertIsNone(line_command_for(segment), segment.id)
        self.assertIsNone(line_command_for(None))

    def test_command_round_trips_through_json(self):
        from robogame_core.mission_dispatch import line_command_for, parse_line_command

        route = _plan()
        for segment in route.segments:
            command = line_command_for(segment)
            if command is None:
                continue
            parsed = parse_line_command(command.to_json())
            self.assertIsNotNone(parsed, segment.id)
            self.assertEqual(parsed, command, segment.id)

    def test_garbage_commands_are_rejected_not_guessed(self):
        from robogame_core.mission_dispatch import parse_line_command

        for text in ("", " ", "not json", "[]", '{"segment_id": "S01"}',
                     '{"segment_id": "S01", "max_speed_mps": 0}',
                     '{"segment_id": "S04", "max_speed_mps": 0.15, "ramp": {"kind": "FLAT"}}'):
            self.assertIsNone(parse_line_command(text), repr(text))

    def test_effective_speed_takes_the_stricter_limit(self):
        from robogame_core.mission_dispatch import (
            effective_line_speed,
            line_command_for,
        )

        route = _plan()
        command = line_command_for(route.segment("S02_LINE_MAIN"))  # 计划 0.25
        self.assertAlmostEqual(command.max_speed_mps, 0.25)
        # 节点参数是现场兜底上限：更小就按节点来（这里 0.2 < 0.25）
        self.assertAlmostEqual(effective_line_speed(command, 0.2), 0.2)
        self.assertAlmostEqual(effective_line_speed(command, 0.3), 0.25)
        # 没有命令时用节点参数
        self.assertAlmostEqual(effective_line_speed(None, 0.2), 0.2)
        with self.assertRaises(ValueError):
            effective_line_speed(command, 0.0)


class RampSlipCompositionTests(unittest.TestCase):
    """坡道限速与打滑判定在「PD 输出 → 限速 → 坡道 → 命令」这条链上的行为。"""

    def _controller(self, kind="RAMP_UP"):
        from robogame_core.ramp_control import RampController, RampProfile
        from robogame_core.route_segment import SegmentKind

        profile = RampProfile(kind=SegmentKind(kind), max_speed_mps=0.15)
        controller = RampController(profile)
        controller.begin(now=0.0)
        return controller

    def test_ramp_limits_the_line_follower_output(self):
        from robogame_core.models import Velocity2D

        controller = self._controller()
        command, decision = controller.step(
            now=0.05, desired=Velocity2D(0.25, 0.0, 0.0), measured_speed=0.25, dt=0.05
        )
        self.assertEqual(decision.value, "NORMAL")
        self.assertLessEqual(command.vx, controller.effective_max_speed)

    def test_descent_is_stricter_than_ascent(self):
        up = self._controller("RAMP_UP")
        down = self._controller("RAMP_DOWN")
        self.assertLess(down.effective_max_speed, up.effective_max_speed)
        self.assertLess(down.effective_max_accel, up.effective_max_accel)

    def test_wheel_speed_far_below_command_is_slipping_then_stuck(self):
        """轮速跟不上命令（上坡上不去的典型表现）→ 先降速，超时后停车。"""
        from robogame_core.models import Velocity2D

        controller = self._controller()
        first, decision = controller.step(
            now=0.05, desired=Velocity2D(0.15, 0.0, 0.0), measured_speed=0.01, dt=0.05
        )
        self.assertEqual(decision.value, "SLIPPING")
        self.assertLessEqual(first.vx, controller.profile.slip_retreat_speed_mps + 1e-9)
        command, decision = controller.step(
            now=2.0, desired=Velocity2D(0.15, 0.0, 0.0), measured_speed=0.01, dt=0.05
        )
        self.assertEqual(decision.value, "STUCK")
        self.assertEqual((command.vx, command.wz), (0.0, 0.0), "卡住必须停车")

    def test_normal_wheel_speed_never_triggers_slip(self):
        from robogame_core.models import Velocity2D

        controller = self._controller()
        for step in range(40):
            _, decision = controller.step(
                now=0.05 * (step + 1), desired=Velocity2D(0.15, 0.0, 0.0),
                measured_speed=0.15, dt=0.05,
            )
            self.assertEqual(decision.value, "NORMAL")


if __name__ == "__main__":
    unittest.main()
