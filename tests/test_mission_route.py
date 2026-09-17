"""B1 全流程路线数据与段推进的单元测试（纯算法，零 ROS、零硬件）。

覆盖三件事：

1. **路线数据与实测场地一致**：段序列、边、朝向、转向点、限速，全部对着
   `field_layout.yaml` 的 survey 段核验（谁改现场数据、这里就该红）。
2. **退出判据求值正确**：路口计数（直行通过的 N03/N09 不能误判）、线尽头稳定
   拍数、位移/位姿/航向/作业次数阈值、读数过期时一律不成立。
3. **段推进与任务状态机**：整条 13 段走通、无证据不推进、每段独立超时预算、
   WORK 段按「一次作业算一次」计数，且旧单方块流程不被破坏。

注：本机 Windows 环境没有 PyYAML（`D:\\python.exe` / 3.11 均无），所以这里用
项目既有的做法（见 `tests/test_config_validation.py`）——手写最小解析器读 yaml
文本。解析器自身有断言（11 节点 / 9 边 / 8 停车点），避免「解析成空 → 测试假通过」。
"""

from __future__ import annotations

import dataclasses
import math
import re
import unittest
from functools import lru_cache
from pathlib import Path

from robogame_core.line_follow import LineSensorState
from robogame_core.mission import (
    MissionConfig,
    MissionMachine,
    MissionPhase,
    MissionState,
)
from robogame_core.mission_route import (
    AXIS_HEADINGS,
    CARGO_PLAN_DEFAULT,
    EVIDENCE_LEVELS,
    JUNCTION_STATES,
    POSE_DEPENDENT_EXITS,
    ROLE_EDGE_KIND,
    ROLE_TO_KIND,
    CargoPlan,
    ExitCriteria,
    ExitKind,
    RouteObservations,
    RoutePlan,
    RoutePlanError,
    RouteRunner,
    SegmentRole,
    SpeedLimits,
    TurnDirection,
    build_route_plan,
    compile_route,
    evaluate_exit,
)
from robogame_core.models import Cargo, CubeColor, MissionResult, Pose2D
from robogame_core.route_segment import ChainStatus, SegmentKind

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIELD_LAYOUT = PROJECT_ROOT / "ros2_ws/src/robogame_bringup/config/field_layout.yaml"
ROBOT_YAML = PROJECT_ROOT / "ros2_ws/src/robogame_bringup/config/robot.yaml"

SURVEY_SECTIONS = ("line_nodes", "line_edges", "stops")

# ---------------------------------------------------------------------------
# 最小 yaml 解析（只为读 field_layout.yaml 的 survey 段；无 pyyaml 环境）
# ---------------------------------------------------------------------------

_KEY_PATTERN = re.compile(r"(?:^|[,\s{])([A-Za-z_]+)\s*:")


def _parse_inline_map(text: str) -> dict:
    """解析 `{type: turn, x: 0.7, y: 1.6, note: 含，全角逗号}` 这种行内映射。

    按「下一个已知键」切分而不是按逗号切分，这样 note 里的中文逗号不会打断解析。
    """
    inner = text.strip().lstrip("{").rstrip("}")
    matches = list(_KEY_PATTERN.finditer(inner))
    result: dict = {}
    for index, match in enumerate(matches):
        key = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(inner)
        result[key] = _parse_value(inner[start:end].strip().rstrip(",").strip())
    return result


def _parse_value(raw: str):
    value = raw.strip().strip("'\"")
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
    try:
        return float(value)
    except ValueError:
        return value


def _load_survey_text(text: str) -> dict:
    sections: dict[str, dict] = {name: {} for name in SURVEY_SECTIONS}
    in_survey = False
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        if indent == 0:
            in_survey = body.startswith("survey:")
            current = None
            continue
        if not in_survey:
            continue
        if indent == 2 and body.endswith(":"):
            name = body[:-1].strip()
            current = name if name in sections else None
            continue
        if indent >= 4 and current is not None and body.endswith("}"):
            entry_id, _, raw_map = body.partition(":")
            if not raw_map.strip().startswith("{"):
                continue
            sections[current][entry_id.strip()] = _parse_inline_map(raw_map)
    return sections


@lru_cache(maxsize=1)
def survey() -> dict:
    return _load_survey_text(FIELD_LAYOUT.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def plan() -> RoutePlan:
    return build_route_plan(survey())


def _variant(segment_id: str, **changes) -> RoutePlan:
    """复制当前路线，只改一段（用于负例：自检必须拦下来）。"""
    base = plan()
    segments = tuple(
        dataclasses.replace(segment, **changes) if segment.id == segment_id else segment
        for segment in base.segments
    )
    return dataclasses.replace(base, segments=segments)


def _exit_variant(segment_id: str, **changes) -> RoutePlan:
    base = plan()
    segments = tuple(
        dataclasses.replace(segment, exit=dataclasses.replace(segment.exit, **changes))
        if segment.id == segment_id
        else segment
        for segment in base.segments
    )
    return dataclasses.replace(base, segments=segments)


def _motion_limits_from_robot_yaml() -> dict:
    """从 robot.yaml 文本里取 motion_controller 的限速（手写解析，无 pyyaml）。"""
    text = ROBOT_YAML.read_text(encoding="utf-8")
    section = text.split("motion_controller:", 1)[1].split("\ncube_perception:", 1)[0]
    limits = {}
    for key in ("max_vx", "max_vy", "max_wz"):
        match = re.search(rf"^\s*{key}:\s*([0-9.]+)", section, re.MULTILINE)
        if match:
            limits[key] = float(match.group(1))
    return limits


# ---------------------------------------------------------------------------
# 1. 路线数据与实测场地一致
# ---------------------------------------------------------------------------


class SurveyParsingTests(unittest.TestCase):
    def test_survey_parser_reads_measured_topology(self):
        """解析器本身要有自检：11 节点 / 9 边 / 8 停车点，否则后面的断言可能假通过。"""
        sections = survey()
        self.assertEqual(len(sections["line_nodes"]), 11)
        self.assertEqual(len(sections["line_edges"]), 9)
        self.assertEqual(len(sections["stops"]), 8)
        self.assertEqual(sections["line_nodes"]["N02"]["type"], "turn")
        self.assertAlmostEqual(sections["line_nodes"]["N02"]["x"], 0.7)
        self.assertAlmostEqual(sections["line_nodes"]["N02"]["y"], 1.6)
        self.assertEqual(sections["line_edges"]["E06"]["kind"], "RAMP")
        self.assertEqual(sections["line_edges"]["E02"]["via"], ["N03"])
        self.assertAlmostEqual(sections["stops"]["W04"]["yaw"], 4.7124)


class RouteDataTests(unittest.TestCase):
    def test_route_has_the_thirteen_planned_segments(self):
        expected = [
            ("S01_LINE_START", SegmentRole.LINE, SegmentKind.LINE_FOLLOW),
            ("S02_LINE_MAIN", SegmentRole.LINE, SegmentKind.LINE_FOLLOW),
            ("S03_RAMP_APPROACH", SegmentRole.LINE, SegmentKind.LINE_FOLLOW),
            ("S04_RAMP_UP", SegmentRole.RAMP_UP, SegmentKind.RAMP_UP),
            ("S05_LINE_PLATFORM", SegmentRole.LINE, SegmentKind.LINE_FOLLOW),
            ("S06_PICK3", SegmentRole.WORK, SegmentKind.WAYPOINT),
            ("S07_TURN_AROUND", SegmentRole.TURN, SegmentKind.WAYPOINT),
            ("S08_LINE_BACK_PLATFORM", SegmentRole.LINE, SegmentKind.LINE_FOLLOW),
            ("S09_RAMP_DOWN", SegmentRole.RAMP_DOWN, SegmentKind.RAMP_DOWN),
            ("S10_LINE_BACK_MAIN", SegmentRole.LINE, SegmentKind.LINE_FOLLOW),
            ("S11_SHIFT_TO_BUILD", SegmentRole.SHIFT, SegmentKind.WAYPOINT),
            ("S12_BUILD_2LAYER", SegmentRole.WORK, SegmentKind.WAYPOINT),
            ("S13_RETREAT", SegmentRole.SHIFT, SegmentKind.WAYPOINT),
        ]
        route = plan()
        self.assertEqual(len(route.segments), len(expected))
        for segment, (seg_id, role, kind) in zip(route.segments, expected):
            self.assertEqual(segment.id, seg_id)
            self.assertEqual(segment.role, role)
            self.assertEqual(segment.kind, kind)
            self.assertEqual(segment.kind, ROLE_TO_KIND[segment.role])
        # 13 段的 id 清单与顺序稳定（网页按它显示进度）
        self.assertEqual(route.segment_ids, tuple(item[0] for item in expected))

    def test_segment_poses_come_from_measured_survey(self):
        """段位姿必须等于 survey 里实测的节点/停车点坐标，不允许另写一套。"""
        route = plan()
        nodes = survey()["line_nodes"]
        stops = survey()["stops"]
        for segment in route.segments:
            for ref, pose in ((segment.from_ref, segment.from_pose), (segment.to_ref, segment.to_pose)):
                self.assertIn(ref, route.refs)
                source = nodes.get(ref) or stops.get(ref)
                self.assertIsNotNone(source, f"{segment.id}: ref {ref} not in survey")
                self.assertAlmostEqual(pose.x, float(source["x"]), places=9, msg=f"{segment.id}/{ref}")
                self.assertAlmostEqual(pose.y, float(source["y"]), places=9, msg=f"{segment.id}/{ref}")
        # 几个关键「停车点落在节点上」的对应关系（改场地时必须同步改计划）
        for stop_id, node_id in (("W02", "N10"), ("W06", "N07"), ("W07", "N08")):
            self.assertAlmostEqual(stops[stop_id]["x"], nodes[node_id]["x"], places=9)
            self.assertAlmostEqual(stops[stop_id]["y"], nodes[node_id]["y"], places=9)
        # W04（搭建放置位）刻意不在黑线上：与 N05 有 0.3m 横向差
        self.assertGreater(abs(float(stops["W04"]["x"]) - float(nodes["N05"]["x"])), 0.2)

    def test_segments_are_pose_continuous(self):
        route = plan()
        for previous, current in zip(route.segments, route.segments[1:]):
            self.assertAlmostEqual(previous.to_pose.x, current.from_pose.x, places=9)
            self.assertAlmostEqual(previous.to_pose.y, current.from_pose.y, places=9)
            self.assertAlmostEqual(
                math.atan2(
                    math.sin(previous.to_pose.yaw - current.from_pose.yaw),
                    math.cos(previous.to_pose.yaw - current.from_pose.yaw),
                ),
                0.0,
                places=9,
                msg=f"{previous.id} -> {current.id} 航向不连续",
            )

    def test_line_and_ramp_segments_use_real_survey_edges(self):
        """巡线/坡道段必须挂在实测黑线拓扑上，且段类型与边的 kind 一致。"""
        route = plan()
        edges = survey()["line_edges"]
        self.assertEqual(set(route.edge_kinds), set(edges))
        for segment in route.segments:
            expected = ROLE_EDGE_KIND.get(segment.role)
            if expected is None:
                self.assertEqual(segment.edges, (), f"{segment.id} 不该声明黑线边")
                continue
            self.assertTrue(segment.edges, f"{segment.id} 必须声明黑线边")
            for edge_id in segment.edges:
                self.assertIn(edge_id, edges)
                self.assertEqual(edges[edge_id]["kind"], expected)
        # 上/下坡段走的是同一条实测坡道边 E06
        self.assertEqual(route.segment("S04_RAMP_UP").edges, ("E06",))
        self.assertEqual(route.segment("S09_RAMP_DOWN").edges, ("E06",))

    def test_turn_and_passthrough_registry(self):
        """转向点/直行通过点清单：N02 右转、N06 左转、N03/N09/N06 直行通过。"""
        route = plan()
        turns = route.turn_registry()
        self.assertEqual(
            [(spec.segment_id, spec.ref, spec.direction) for spec in turns],
            [
                ("S01_LINE_START", "N02", TurnDirection.RIGHT),
                ("S02_LINE_MAIN", "N06", TurnDirection.LEFT),
            ],
        )
        self.assertEqual(turns[0].target_yaw_rad, AXIS_HEADINGS["x+"])
        self.assertEqual(turns[1].target_yaw_rad, AXIS_HEADINGS["y+"])
        # 主路上的 N03 必须被显式略过，否则会在 T 形路口误停/误转
        self.assertEqual(turns[1].passthrough_junctions, 1)
        self.assertEqual(
            route.passthrough_registry(),
            (
                ("S02_LINE_MAIN", "N03"),
                ("S05_LINE_PLATFORM", "N09"),
                ("S08_LINE_BACK_PLATFORM", "N09"),
                ("S10_LINE_BACK_MAIN", "N06"),
            ),
        )
        # 高台掉头是 TURN 段（YAW_TARGET），不走路口判据
        turn_around = route.segment("S07_TURN_AROUND")
        self.assertIs(turn_around.role, SegmentRole.TURN)
        self.assertIs(turn_around.exit.kind, ExitKind.YAW_TARGET)
        self.assertAlmostEqual(turn_around.exit.target_yaw_rad, AXIS_HEADINGS["y-"])

    def test_speed_limits_match_chassis_config_and_firmware(self):
        """限速必须 ≤ 固件限幅 0.3/0.3/1.0，并与 robot.yaml 的 motion_controller 一致。"""
        route = plan()
        limits = _motion_limits_from_robot_yaml()
        self.assertEqual(
            (limits["max_vx"], limits["max_vy"], limits["max_wz"]),
            (0.30, 0.30, 1.00),
            "robot.yaml 限速与本测试的固件限幅假设已不一致，需同步核对",
        )
        self.assertEqual(
            (route.speed_limits.max_vx, route.speed_limits.max_vy, route.speed_limits.max_wz),
            (limits["max_vx"], limits["max_vy"], limits["max_wz"]),
        )
        self.assertLessEqual(route.speed_limits.max_vx, 0.30)
        self.assertLessEqual(route.speed_limits.max_wz, 1.00)
        for segment in route.segments:
            self.assertLessEqual(segment.max_speed_mps, route.speed_limits.max_vx, segment.id)
            self.assertLessEqual(segment.max_yaw_radps, route.speed_limits.max_wz, segment.id)
        # 坡道段必须比平地慢（打滑/防冲）
        self.assertLessEqual(route.segment("S04_RAMP_UP").max_speed_mps, 0.15)
        self.assertLessEqual(route.segment("S09_RAMP_DOWN").max_speed_mps, 0.15)

    def test_work_and_turn_segments_never_drive(self):
        route = plan()
        for segment in route.segments:
            if segment.role in (SegmentRole.WORK, SegmentRole.TURN):
                self.assertEqual(segment.max_speed_mps, 0.0, segment.id)
                self.assertTrue(segment.is_in_place, segment.id)

    def test_pose_dependent_exits_are_honestly_marked_estimated(self):
        """依赖未标定里程计/航向的判据不许标成 measured（诚实纪律）。"""
        route = plan()
        dependent = [
            segment for segment in route.segments if segment.exit.kind in POSE_DEPENDENT_EXITS
        ]
        self.assertTrue(dependent, "路线里应当存在依赖位姿的段（坡道距离/搭建位姿）")
        for segment in dependent:
            self.assertEqual(segment.evidence, "estimated", segment.id)
            self.assertIn(segment.evidence, EVIDENCE_LEVELS)
        # 巡线段（拓扑来自实测）才允许 measured
        self.assertEqual(route.segment("S01_LINE_START").evidence, "measured")
        self.assertEqual(route.segment("S05_LINE_PLATFORM").evidence, "measured")

    def test_route_summary_rows_for_web_panel(self):
        rows = plan().summary()
        self.assertEqual(len(rows), 13)
        self.assertEqual(rows[0]["id"], "S01_LINE_START")
        self.assertEqual(
            set(rows[0]),
            {
                "index", "id", "role", "kind", "label", "from", "to",
                "heading", "max_speed_mps", "exit", "exit_ref", "evidence",
            },
        )
        self.assertEqual(rows[5]["exit"], ExitKind.WORK_DONE.value)

    def test_cargo_plan_matches_confirmed_decision(self):
        """现场确认：3 橙、只停 W02、搭 2 层（底 2 + 顶 1）。"""
        route = plan()
        self.assertEqual((route.cargo_plan.orange, route.cargo_plan.purple), (3, 0))
        self.assertEqual(route.cargo_plan.layers, 2)
        self.assertEqual(route.cargo_plan.layout, "2+1")
        self.assertEqual(CARGO_PLAN_DEFAULT.orange, 3)


# ---------------------------------------------------------------------------
# 2. 自检必须拦下不合法的路线（负例）
# ---------------------------------------------------------------------------


class RouteValidationTests(unittest.TestCase):
    def test_unknown_ref_is_rejected(self):
        with self.assertRaises(RoutePlanError):
            _variant("S01_LINE_START", from_ref="N99")

    def test_pose_must_match_survey_ref(self):
        wrong = Pose2D(0.0, 0.0, AXIS_HEADINGS["y+"])
        with self.assertRaises(RoutePlanError):
            _variant("S01_LINE_START", from_pose=wrong)

    def test_discontinuity_is_rejected(self):
        """把 S02 起点挪走 → 段间不连续必须被拦。"""
        moved = Pose2D(0.9, 1.6, AXIS_HEADINGS["x+"])
        with self.assertRaises(RoutePlanError):
            _variant("S02_LINE_MAIN", from_pose=moved)

    def test_heading_must_match_measured_geometry(self):
        """朝向声明与实测坐标矛盾时必须被拦（防止「计划写右转、数据是左转」）。"""
        with self.assertRaises(RoutePlanError):
            _variant("S02_LINE_MAIN", heading="y+")

    def test_speed_over_chassis_limit_is_rejected(self):
        with self.assertRaises(RoutePlanError):
            _variant("S02_LINE_MAIN", max_speed_mps=0.35)

    def test_negative_speed_is_rejected(self):
        with self.assertRaises(RoutePlanError):
            _variant("S02_LINE_MAIN", max_speed_mps=-0.1)

    def test_unknown_edge_is_rejected(self):
        with self.assertRaises(RoutePlanError):
            _variant("S02_LINE_MAIN", edges=("E99",))

    def test_edge_kind_mismatch_is_rejected(self):
        """巡线段不许挂到坡道边 E06 上。"""
        with self.assertRaises(RoutePlanError):
            _variant("S02_LINE_MAIN", edges=("E06",))

    def test_duplicate_segment_ids_are_rejected(self):
        base = plan()
        segments = (base.segments[0], base.segments[0]) + base.segments[1:]
        with self.assertRaises(RoutePlanError):
            dataclasses.replace(base, segments=segments)

    def test_empty_segments_are_rejected(self):
        with self.assertRaises(RoutePlanError):
            dataclasses.replace(plan(), segments=())

    def test_unknown_evidence_level_is_rejected(self):
        with self.assertRaises(RoutePlanError):
            _variant("S01_LINE_START", evidence="guessed")

    def test_pose_dependent_exit_cannot_claim_measured(self):
        with self.assertRaises(RoutePlanError):
            _variant("S03_RAMP_APPROACH", evidence="measured")

    def test_junction_turn_must_be_at_segment_end(self):
        with self.assertRaises(RoutePlanError):
            _exit_variant("S01_LINE_START", at_ref="N06")

    def test_junction_turn_must_point_at_a_measured_junction(self):
        """终点节点类型必须是实测的 junction/turn，不能拿线尽头(end)当路口。"""
        with self.assertRaises(RoutePlanError):
            _exit_variant(
                "S05_LINE_PLATFORM",
                kind=ExitKind.JUNCTION_TURN,
                at_ref="N10",
                turn=TurnDirection.STRAIGHT,
                expected_line_states=JUNCTION_STATES,
            )

    def test_work_segment_must_not_declare_motion(self):
        with self.assertRaises(RoutePlanError):
            _variant("S06_PICK3", max_speed_mps=0.10)

    def test_line_segment_requires_edges(self):
        with self.assertRaises(RoutePlanError):
            _variant("S02_LINE_MAIN", edges=())

    def test_passthrough_ref_must_be_on_declared_edges(self):
        with self.assertRaises(RoutePlanError):
            _variant("S02_LINE_MAIN", passthrough_refs=("N10",))

    def test_passthrough_ref_cannot_be_an_endpoint(self):
        with self.assertRaises(RoutePlanError):
            _variant("S02_LINE_MAIN", passthrough_refs=("N06",))

    def test_work_segment_without_drive_speed_is_accepted(self):
        """正向确认：WORK/ShIFT 段的合法形态不被误拦。"""
        route = plan()
        self.assertEqual(route.segment("S06_PICK3").max_speed_mps, 0.0)
        self.assertEqual(route.segment("S11_SHIFT_TO_BUILD").max_speed_mps, 0.10)

    def test_speed_limits_reject_non_positive(self):
        with self.assertRaises(RoutePlanError):
            SpeedLimits(max_vx=0.0)


class CargoConstraintTests(unittest.TestCase):
    """G4.2：载货约束的直接单测（此前只有间接覆盖）。"""

    def test_three_orange_allowed_fourth_rejected(self):
        cargo = Cargo()
        for _ in range(3):
            self.assertTrue(cargo.can_add(CubeColor.ORANGE))
            cargo.add(CubeColor.ORANGE)
        self.assertEqual(cargo.total, 3)
        self.assertFalse(cargo.can_add(CubeColor.ORANGE))
        with self.assertRaises(ValueError):
            cargo.add(CubeColor.ORANGE)

    def test_at_most_one_purple(self):
        cargo = Cargo()
        cargo.add(CubeColor.PURPLE)
        self.assertFalse(cargo.can_add(CubeColor.PURPLE))

    def test_cargo_plan_rejects_over_capacity(self):
        with self.assertRaises(RoutePlanError):
            CargoPlan(orange=4)
        with self.assertRaises(RoutePlanError):
            CargoPlan(orange=3, purple=1)

    def test_cargo_plan_rejects_impossible_layers(self):
        with self.assertRaises(RoutePlanError):
            CargoPlan(orange=1, purple=0, layers=3)


# ---------------------------------------------------------------------------
# 3. 退出判据求值
# ---------------------------------------------------------------------------


class ExitEvaluationTests(unittest.TestCase):
    def _junction_criteria(self, *, passthrough: int = 0, settle: int = 3) -> ExitCriteria:
        return ExitCriteria(
            kind=ExitKind.JUNCTION_TURN,
            at_ref="N02",
            expected_line_states=JUNCTION_STATES,
            turn=TurnDirection.RIGHT,
            target_yaw_rad=AXIS_HEADINGS["x+"],
            passthrough_junctions=passthrough,
            settle_samples=settle,
        )

    def test_junction_turn_needs_junction_then_reacquired_line(self):
        criteria = self._junction_criteria()
        obs = RouteObservations()
        self.assertFalse(evaluate_exit(criteria, obs), "还没有任何观测就不该判定完成")
        obs.update_line(LineSensorState.ON_LINE)  # 还没到路口
        self.assertFalse(evaluate_exit(criteria, obs))
        obs.update_line(LineSensorState.INTERSECTION)
        self.assertFalse(evaluate_exit(criteria, obs), "刚看到路口还没转过去")
        for _ in range(3):
            obs.update_line(LineSensorState.ON_LINE)
        self.assertTrue(evaluate_exit(criteria, obs))

    def test_passthrough_junction_does_not_exit(self):
        """主路上 N03 直行通过：第一个路口事件不得结束本段。"""
        criteria = self._junction_criteria(passthrough=1)
        obs = RouteObservations()
        obs.update_line(LineSensorState.INTERSECTION)
        for _ in range(3):
            obs.update_line(LineSensorState.ON_LINE)
        self.assertFalse(evaluate_exit(criteria, obs), "直行通过的路口被误判成转向完成")
        obs.update_line(LineSensorState.INTERSECTION)
        for _ in range(3):
            obs.update_line(LineSensorState.ON_LINE)
        self.assertTrue(evaluate_exit(criteria, obs), "第二个路口（N06）应当结束本段")

    def test_junction_states_do_not_double_count(self):
        obs = RouteObservations()
        obs.update_line(LineSensorState.INTERSECTION)
        obs.update_line(LineSensorState.ALL_BLACK)
        obs.update_line(LineSensorState.INTERSECTION)
        self.assertEqual(obs.junction_events, 1)

    def test_junction_turn_requires_settled_line(self):
        criteria = self._junction_criteria(settle=5)
        obs = RouteObservations()
        obs.update_line(LineSensorState.INTERSECTION)
        for _ in range(4):
            obs.update_line(LineSensorState.ON_LINE)
        self.assertFalse(evaluate_exit(criteria, obs))
        obs.update_line(LineSensorState.ON_LINE)
        self.assertTrue(evaluate_exit(criteria, obs))

    def test_junction_turn_optional_yaw_confirmation(self):
        criteria = dataclasses.replace(self._junction_criteria(), require_yaw=True)
        obs = RouteObservations()
        obs.update_line(LineSensorState.INTERSECTION)
        for _ in range(3):
            obs.update_line(LineSensorState.ON_LINE)
        obs.observe_pose(Pose2D(0.7, 1.6, AXIS_HEADINGS["y+"]))  # 还没转
        self.assertFalse(evaluate_exit(criteria, obs))
        obs.observe_pose(Pose2D(0.7, 1.6, AXIS_HEADINGS["x+"]))
        self.assertTrue(evaluate_exit(criteria, obs))

    def test_yaw_target_wraps_around(self):
        criteria = ExitCriteria(kind=ExitKind.YAW_TARGET, target_ref="W02", target_yaw_rad=-math.pi / 2)
        obs = RouteObservations()
        obs.observe_pose(Pose2D(3.5, 4.7, 3.0 * math.pi / 2.0))
        self.assertTrue(evaluate_exit(criteria, obs), "3π/2 与 -π/2 是同一朝向")
        obs.observe_pose(Pose2D(3.5, 4.7, math.pi / 2.0))
        self.assertFalse(evaluate_exit(criteria, obs))

    def test_line_end_requires_settled_lost(self):
        criteria = ExitCriteria(
            kind=ExitKind.LINE_END,
            target_ref="W02",
            expected_line_states=(LineSensorState.LOST,),
            settle_samples=2,
        )
        obs = RouteObservations()
        obs.update_line(LineSensorState.ON_LINE)
        obs.update_line(LineSensorState.LOST)
        self.assertFalse(evaluate_exit(criteria, obs), "单拍丢线不足以判定线尽头")
        obs.update_line(LineSensorState.LOST)
        self.assertTrue(evaluate_exit(criteria, obs))

    def test_odom_distance_threshold(self):
        criteria = ExitCriteria(kind=ExitKind.ODOM_DISTANCE, target_ref="W06", distance_m=0.6)
        obs = RouteObservations()
        obs.observe_distance(0.59)
        self.assertFalse(evaluate_exit(criteria, obs))
        obs.observe_distance(0.60)
        self.assertTrue(evaluate_exit(criteria, obs))

    def test_work_done_counts_required_times(self):
        criteria = ExitCriteria(kind=ExitKind.WORK_DONE, target_ref="W02", required_count=3)
        obs = RouteObservations()
        for _ in range(2):
            obs.note_work_done()
            self.assertFalse(evaluate_exit(criteria, obs))
        obs.note_work_done()
        self.assertTrue(evaluate_exit(criteria, obs))

    def test_pose_exits_need_a_pose(self):
        criteria = ExitCriteria(
            kind=ExitKind.POSE_TOLERANCE,
            target_ref="W04",
            target_pose=Pose2D(3.2, 1.1, AXIS_HEADINGS["y-"]),
            tolerance_m=0.05,
            tolerance_rad=0.0872665,
        )
        obs = RouteObservations()
        self.assertFalse(evaluate_exit(criteria, obs), "没有位姿就不能说到了")
        obs.observe_pose(Pose2D(3.2, 1.16, AXIS_HEADINGS["y-"]))
        self.assertFalse(evaluate_exit(criteria, obs), "超出 0.05m 容差")
        obs.observe_pose(Pose2D(3.2, 1.13, AXIS_HEADINGS["y-"]))
        self.assertTrue(evaluate_exit(criteria, obs))

    def test_stale_readings_block_every_exit(self):
        """安全底线：证据齐了才判完成，但读数一过期任何判据都不得成立。"""
        route = plan()
        for segment in route.segments:
            runner = RouteRunner(route)
            _feed_evidence(runner, segment)
            observations = runner.observations
            self.assertTrue(
                evaluate_exit(segment.exit, observations), f"{segment.id}: 证据齐了却没判定完成"
            )
            observations.mark_stale(True)
            self.assertFalse(
                evaluate_exit(segment.exit, observations), f"{segment.id}: 过期读数被当成完成"
            )

    def test_invalid_observations_are_rejected(self):
        obs = RouteObservations()
        with self.assertRaises(ValueError):
            obs.update_line("ON_LINE")
        with self.assertRaises(ValueError):
            obs.observe_distance(-1.0)
        with self.assertRaises(ValueError):
            obs.observe_pose(Pose2D(float("nan"), 0.0, 0.0))


# ---------------------------------------------------------------------------
# 4. 段推进与任务状态机
# ---------------------------------------------------------------------------


def _feed_evidence(runner: RouteRunner, segment) -> None:
    """按当前段的判据喂足证据（模拟真车/上层给出的观测）。"""
    criteria = segment.exit
    kind = criteria.kind
    if kind is ExitKind.JUNCTION_TURN:
        for _ in range(criteria.passthrough_junctions + 1):
            runner.observe_line(LineSensorState.INTERSECTION)
            for _ in range(criteria.settle_samples):
                runner.observe_line(LineSensorState.ON_LINE)
    elif kind is ExitKind.LINE_END:
        for _ in range(criteria.settle_samples):
            runner.observe_line(criteria.expected_line_states[0])
    elif kind is ExitKind.ODOM_DISTANCE:
        runner.observe_distance(criteria.distance_m)
    elif kind in (ExitKind.STOP_POINT, ExitKind.POSE_TOLERANCE):
        runner.observe_pose(criteria.target_pose)
    elif kind is ExitKind.YAW_TARGET:
        runner.observe_pose(Pose2D(segment.to_pose.x, segment.to_pose.y, criteria.target_yaw_rad))
    elif kind is ExitKind.WORK_DONE:
        for _ in range(criteria.required_count):
            runner.note_work_done()
    else:  # pragma: no cover - 判据类型新增时这里应当被补上
        raise AssertionError(f"未覆盖的判据类型: {kind}")


class RouteRunnerTests(unittest.TestCase):
    def test_compiled_chain_binds_every_exit_check(self):
        """C1 的陷阱：exit_check=None 表示无条件推进，所以每段都必须绑定判据。"""
        chain = compile_route(plan(), RouteObservations())
        self.assertEqual(len(chain.segments), 13)
        for segment in chain.segments:
            self.assertIsNotNone(segment.exit_check, segment.label)
            self.assertIsNone(segment.entry_check)

    def test_runner_does_not_advance_without_evidence(self):
        runner = RouteRunner(plan())
        runner.start()
        self.assertEqual(runner.segment_index, 0)
        for _ in range(5):
            self.assertFalse(runner.tick())
        self.assertEqual(runner.segment_index, 0)
        self.assertIs(runner.status, ChainStatus.RUNNING)

    def test_runner_walks_the_whole_route(self):
        route = plan()
        runner = RouteRunner(route)
        runner.start()
        visited = [runner.current_segment.id]
        for segment in route.segments:
            self.assertEqual(runner.current_segment.id, segment.id)
            _feed_evidence(runner, segment)
            self.assertTrue(runner.tick(), f"{segment.id} 喂足证据后仍未推进")
            if runner.current_segment is not None:
                visited.append(runner.current_segment.id)
        self.assertTrue(runner.is_complete)
        self.assertIs(runner.status, ChainStatus.COMPLETE)
        self.assertIsNone(runner.current_segment)
        self.assertEqual(visited, list(route.segment_ids))

    def test_per_segment_counters_are_reset_on_entry(self):
        route = plan()
        runner = RouteRunner(route)
        runner.start()
        _feed_evidence(runner, route.segments[0])
        runner.observe_distance(0.42)
        runner.tick()
        self.assertEqual(runner.segment_index, 1)
        self.assertEqual(runner.observations.distance_m, 0.0)
        self.assertEqual(runner.observations.junction_events, 0)
        self.assertEqual(runner.observations.work_count, 0)

    def test_current_speed_follows_segment_override(self):
        route = plan()
        runner = RouteRunner(route)
        runner.start()
        self.assertAlmostEqual(runner.current_speed, 0.20)  # S01 覆盖值
        _feed_evidence(runner, route.segments[0])
        runner.tick()
        self.assertAlmostEqual(runner.current_speed, 0.25)  # S02 覆盖值
        self.assertAlmostEqual(runner.current_yaw_limit, route.default_yaw_radps)

    def test_stale_readings_stop_progress(self):
        route = plan()
        runner = RouteRunner(route)
        runner.start()
        runner.mark_stale(True)
        _feed_evidence(runner, route.segments[0])
        self.assertFalse(runner.tick())
        runner.mark_stale(False)
        self.assertTrue(runner.tick())

    def test_reset_restarts_the_route(self):
        route = plan()
        runner = RouteRunner(route)
        runner.start()
        _feed_evidence(runner, route.segments[0])
        runner.tick()
        runner.reset()
        self.assertFalse(runner.is_complete)
        self.assertEqual(runner.segment_index, -1)
        with self.assertRaises(ValueError):
            runner.tick()
        runner.start()
        self.assertEqual(runner.current_segment.id, route.segment_ids[0])

    def test_runner_start_is_not_repeatable_without_reset(self):
        runner = RouteRunner(plan())
        runner.start()
        with self.assertRaises(ValueError):
            runner.start()


class MissionRouteModeTests(unittest.TestCase):
    def _machine_at_route_start(self) -> MissionMachine:
        machine = MissionMachine(MissionConfig(), route=plan())
        machine.tick(action_succeeded=True)  # SELF_CHECK -> WAIT_FOR_PHYSICAL_START
        self.assertIs(machine.state, MissionState.WAIT_FOR_PHYSICAL_START)
        machine.tick(physical_start=True)
        return machine

    def test_legacy_flow_untouched_without_route(self):
        """不绑路线时旧演示流程不变（single_cube 链路与既有测试依赖它）。"""
        machine = MissionMachine(MissionConfig(orange_target=1, purple_target=0))
        machine.tick(action_succeeded=True)
        machine.tick(physical_start=True)
        self.assertIs(machine.state, MissionState.GO_TO_ORANGE)
        self.assertEqual(machine.segment_index, -1)
        self.assertIs(machine.phase, MissionPhase.IDLE)
        self.assertIsNone(machine.route_runner)

    def test_route_mode_starts_at_first_segment(self):
        machine = self._machine_at_route_start()
        self.assertIs(machine.state, MissionState.ROUTE_RUNNING)
        self.assertEqual(machine.segment_index, 0)
        self.assertEqual(machine.segment_id, "S01_LINE_START")
        self.assertIs(machine.phase, MissionPhase.MOVING)
        self.assertIsNotNone(machine.route_runner)

    def test_route_mode_runs_all_segments_then_verifies_and_completes(self):
        machine = self._machine_at_route_start()
        route = plan()
        runner = machine.route_runner
        seen = set()
        for _ in range(60):
            segment = machine.current_segment
            if segment is None:
                break
            seen.add(segment.id)
            if segment.exit.kind is ExitKind.WORK_DONE:
                machine.tick(action_succeeded=True)  # 一次作业算一次
            else:
                _feed_evidence(runner, segment)
                machine.tick(action_succeeded=False)
        self.assertEqual(seen, set(route.segment_ids), "有段没被执行到")
        self.assertIs(machine.state, MissionState.VERIFY_BUILD)
        self.assertIs(machine.phase, MissionPhase.VERIFY)
        self.assertEqual(machine.segment_index, len(route.segments))
        machine.tick(action_succeeded=True)
        self.assertIs(machine.state, MissionState.COMPLETE)
        self.assertIs(machine.result, MissionResult.SUCCESS)

    def test_work_phase_is_visible_and_needs_three_actions(self):
        machine = self._machine_at_route_start()
        route = plan()
        runner = machine.route_runner
        for segment in route.segments[:5]:
            _feed_evidence(runner, segment)
            machine.tick(action_succeeded=False)
        self.assertEqual(machine.segment_id, "S06_PICK3")
        self.assertIs(machine.phase, MissionPhase.WORKING)
        machine.tick(action_succeeded=True)
        self.assertEqual(runner.observations.work_count, 1)
        self.assertEqual(machine.segment_id, "S06_PICK3")
        machine.tick(action_succeeded=True)
        machine.tick(action_succeeded=True)
        self.assertEqual(machine.segment_id, "S07_TURN_AROUND")

    def test_segment_timeout_has_finite_retries(self):
        machine = self._machine_at_route_start()
        machine.entered_at = 0.0
        machine.tick(now=100.0)
        self.assertEqual(machine.retries, 1)
        self.assertIs(machine.state, MissionState.ROUTE_RUNNING)
        machine.tick(now=200.0)
        self.assertEqual(machine.retries, 2)
        machine.tick(now=300.0)
        self.assertIs(machine.state, MissionState.FAILED)
        self.assertIs(machine.result, MissionResult.TIMEOUT)

    def test_timeout_is_per_segment_not_per_route(self):
        """整条路线不能被一个 state_timeout_s 预算掐死。"""
        machine = self._machine_at_route_start()
        route = plan()
        runner = machine.route_runner
        _feed_evidence(runner, route.segments[0])
        machine.entered_at = 0.0
        machine.tick(now=10.0)  # 段切换 -> 计时重置
        self.assertEqual(machine.segment_index, 1)
        self.assertEqual(machine.entered_at, 10.0)
        machine.tick(now=11.0)
        self.assertIs(machine.state, MissionState.ROUTE_RUNNING)
        self.assertEqual(machine.retries, 0)

    def test_communication_loss_fails_during_route(self):
        machine = self._machine_at_route_start()
        machine.tick(communication_ok=False)
        self.assertIs(machine.state, MissionState.FAILED)
        self.assertIs(machine.result, MissionResult.COMMUNICATION_ERROR)

    def test_emergency_stop_has_priority_in_route_mode(self):
        machine = self._machine_at_route_start()
        machine.tick(emergency_stop=True)
        self.assertIs(machine.state, MissionState.SAFE_STOP)
        self.assertIs(machine.result, MissionResult.SAFETY_STOP)

    def test_route_progress_snapshot(self):
        machine = self._machine_at_route_start()
        progress = machine.route_progress()
        self.assertEqual(progress["state"], MissionState.ROUTE_RUNNING.value)
        self.assertEqual(progress["segment_index"], 0)
        self.assertEqual(progress["segment_id"], "S01_LINE_START")
        self.assertEqual(progress["segment_count"], 13)
        self.assertEqual(progress["phase"], MissionPhase.MOVING.value)
        self.assertIn("segment_label", progress)


if __name__ == "__main__":
    unittest.main()
