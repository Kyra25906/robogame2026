"""B1: 全流程路线（比赛 run）数据与段推进 —— 纯算法，零 ROS / 零硬件。

## 为什么需要这个模块

`mission.py` 的状态机是写死的「取 2 橙 → 取 1 紫 → 搭建」，`GO_TO_*` 各发一个
`/motion/goal`，**没有巡线、没有坡道、没有下坡**，也没有「同一停车点连续取 3 块」
的语义；`route_segment.py`（C1 路段链）虽然已经写好，但全项目零引用。

本模块把现场计划（2026-09-17 用户确认版）落成**可校验的段序列数据**：

    启动区 → 巡线右转(N02) → 主路巡线(N03 直行) → 左转上坡(N06)
    → 上坡(E06) → 高台巡线到线尽头(N10) → 在 W02 连取 3 橙（存本体框）
    → 原地掉头 → 原路巡线回坡顶 → 下坡 → 主路巡线回线尽头(N05)
    → 短距平移到位 W04 → 搭建 2 层（3 块：底层 2 + 顶层 1）
    → 撤退到 W05

坐标**不写死在本文件**：全部按节点/停车点 ID 从 `field_layout.yaml`（现场实测
survey 段）解析，本模块只声明「走哪条边、什么朝向、怎么算走完」。

## 边界（B1 明确不做）

- 不接 ROS 节点、不改 `mission_manager`（B2）；不实现路口转弯控制、坡道速度闭环
  （B3）；不碰真车配置；`field_layout.yaml` 只读。
- 不改 C1 的 `SegmentKind`（`route_segment.py` 与其测试保持原样）。本模块用
  `SegmentRole` 表达「这段要干什么」，再映射到 `SegmentKind`（C1 只认 4 种），
  这样 C1 的既有测试不需要动。映射关系见 `ROLE_TO_KIND`。

## 退出判据怎么定（本模块的核心价值）

三条候选信号按可信度分工：

| 信号 | 来源 | 可信度 | 用在哪 |
|---|---|---|---|
| 巡线状态（路口 / 线尽头） | `/line_follow/status` | 主判据 | 巡线段 |
| 里程计位移与航向 | `/pose` | **未标定（文档记录有 10 倍偏差 + 跳变）** | 坡道段兜底、原地转向 |
| 视觉标签 PnP | AprilTag | 备用矫正，B1 不用 | — |

因此有一条硬性自检：**只要退出判据依赖位姿/航向，`evidence` 必须标 `estimated`**
（`POSE_DEPENDENT_EXITS`）。这防止「把没验证过的阈值当成实测值」。

路口转弯**不靠里程计**：靠巡线传感器自己判断——先在路口签名（多路黑）上计一次
事件，略过 `passthrough_junctions` 个直行通过的路口（如主路上的 N03），之后等
线重新回到 `ON_LINE` 并稳定 `settle_samples` 拍。航向只作为**可选**确认
（`require_yaw=False`，B3 现场决定是否打开）。

## 已知的诚实边界

- `LINE_END`（线尽头）与「意外丢线」在传感器上长得一样，靠 `settle_samples`
  稳定判据 + 任务级超时/重试兜底区分；真正的区分能力要现场验证。
- 路口签名（多路黑）目前标 `estimated`：90° 拐角与 T 形路口在 5cm 黑线上的
  实际波形必须在真车上量，不能靠推测当结论。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

from .line_follow import LineSensorState
from .models import Cargo, CubeColor, Pose2D
from .route_segment import ChainStatus, RouteChain, RouteSegment, SegmentKind

# 本份路线数据的版本与来源（B2 会把它带进参数/日志，便于现场对版本）
ROUTE_VERSION = "b1-2026-09-17"
ROUTE_SOURCE_NOTE = "坐标来源：field_layout.yaml survey（现场实测 2026-08-18/19）；计划来源：现场确认版"

# 路口签名状态：多路黑（交叉口）或全黑
JUNCTION_STATES = (LineSensorState.INTERSECTION, LineSensorState.ALL_BLACK)

# 合法可信度等级（与 GENERAL_FIELD_MAP_2026.md 的标记一致）
EVIDENCE_LEVELS = ("rule", "measured", "estimated")

# 允许的行驶朝向声明；"free" = 非轴向移动（如侧向平移），不做轴向几何自检
AXIS_HEADINGS = {"x+": 0.0, "x-": math.pi, "y+": math.pi / 2.0, "y-": -math.pi / 2.0}
HEADINGS = tuple(AXIS_HEADINGS) + ("free",)


class RoutePlanError(ValueError):
    """路线数据不合法（自检失败）。任何一条都不允许「带病上路」。"""


#: 位置比较容差（survey 的坐标是十进制定值，可以卡很紧）
POSITION_TOLERANCE_M = 1e-6

#: 角度比较容差。**为什么不是 1e-6**：`field_layout.yaml` 里的 yaw 只写到
#: 小数点后 4 位（如 1.5708 而 π/2 = 1.5707963…，差 3.7e-6 rad ≈ 0.0002°）。
#: 用 1e-6 会把「实测数据的写精度」误判成「计划与场地矛盾」。
YAW_TOLERANCE_RAD = 1e-3


def angle_diff(a: float, b: float) -> float:
    """两角之差，归一化到 [-pi, pi)。"""
    return (a - b + math.pi) % (2.0 * math.pi) - math.pi


# ----------------------------------------------------------------------------
# 枚举与判据
# ----------------------------------------------------------------------------


class SegmentRole(str, Enum):
    """本段要干什么（决定 B2/B3 用哪个控制模式，与 C1 的 SegmentKind 解耦）。"""

    LINE = "LINE"  # 巡线（含拐角/路口段的处理由退出判据描述）
    RAMP_UP = "RAMP_UP"  # 上坡（限速 + 打滑判据，C3）
    RAMP_DOWN = "RAMP_DOWN"  # 下坡（限速 + 防冲，C3）
    SHIFT = "SHIFT"  # 不在黑线上的短距移动（路点控制；麦轮可侧移）
    WORK = "WORK"  # 原地作业（抓取 / 放置），本段不驱动底盘
    TURN = "TURN"  # 原地转向（如高台上掉头）


#: SegmentRole -> C1 SegmentKind（C1 只认 4 种，不改它）
ROLE_TO_KIND: Mapping[SegmentRole, SegmentKind] = {
    SegmentRole.LINE: SegmentKind.LINE_FOLLOW,
    SegmentRole.RAMP_UP: SegmentKind.RAMP_UP,
    SegmentRole.RAMP_DOWN: SegmentKind.RAMP_DOWN,
    SegmentRole.SHIFT: SegmentKind.WAYPOINT,
    SegmentRole.WORK: SegmentKind.WAYPOINT,
    SegmentRole.TURN: SegmentKind.WAYPOINT,
}

#: 原路段类型 -> 必须有边、且边的 kind 必须一致
ROLE_EDGE_KIND: Mapping[SegmentRole, str] = {
    SegmentRole.LINE: "LINE",
    SegmentRole.RAMP_UP: "RAMP",
    SegmentRole.RAMP_DOWN: "RAMP",
}


class TurnDirection(str, Enum):
    """路口转向方向（登记用；B3 才实现控制）。"""

    LEFT = "LEFT"
    RIGHT = "RIGHT"
    AROUND = "AROUND"  # 180° 掉头
    STRAIGHT = "STRAIGHT"  # 直行通过（只登记，不转向）


class WorkKind(str, Enum):
    """WORK 段的作业类型（B2：决定给 manipulator_client 发什么命令）。"""

    PICK = "PICK"  # 取块入本体框
    PLACE = "PLACE"  # 放置到搭建区


class ExitKind(str, Enum):
    """本段「算走完了」的判据类型。"""

    JUNCTION_TURN = "JUNCTION_TURN"  # 到路口/拐角并按方向转过去，线重新捕获
    LINE_END = "LINE_END"  # 线走到尽头（稳定失去黑线）
    ODOM_DISTANCE = "ODOM_DISTANCE"  # 本段累计位移达标
    STOP_POINT = "STOP_POINT"  # 到停车点（位姿容差）
    POSE_TOLERANCE = "POSE_TOLERANCE"  # 位姿容差（含航向）
    YAW_TARGET = "YAW_TARGET"  # 原地转到目标航向
    WORK_DONE = "WORK_DONE"  # 本段完成 N 次作业（抓/放）


#: 依赖位姿/航向的判据：evidence 必须标 estimated（里程计未标定）
POSE_DEPENDENT_EXITS = frozenset(
    {
        ExitKind.ODOM_DISTANCE,
        ExitKind.STOP_POINT,
        ExitKind.POSE_TOLERANCE,
        ExitKind.YAW_TARGET,
    }
)

#: 转向方向 -> 航向变化量（弧度）。车头朝 +y(π/2) 右转后朝 +x(0)，变化 -π/2。
TURN_YAW_DELTA: Mapping[TurnDirection, float] = {
    TurnDirection.RIGHT: -math.pi / 2.0,
    TurnDirection.LEFT: math.pi / 2.0,
    TurnDirection.STRAIGHT: 0.0,
}

#: 依赖巡线传感器的判据
LINE_DEPENDENT_EXITS = frozenset({ExitKind.JUNCTION_TURN, ExitKind.LINE_END})


@dataclass(frozen=True)
class ExitCriteria:
    """单段退出判据（纯数据，求值见 `evaluate_exit`）。"""

    kind: ExitKind
    # 判据引用的节点/停车点 ID（日志与自检用）
    at_ref: str = ""
    target_ref: str = ""
    # JUNCTION_TURN / LINE_END：期望的巡线状态
    expected_line_states: tuple[LineSensorState, ...] = ()
    # JUNCTION_TURN：转向方向、转向后的目标航向、要略过的直行路口数
    turn: TurnDirection | None = None
    target_yaw_rad: float | None = None
    passthrough_junctions: int = 0
    # 稳定拍数：连续多少拍满足才算数（抗抖）
    settle_samples: int = 3
    # 容差
    tolerance_m: float = 0.05
    tolerance_rad: float = 0.10
    # ODOM_DISTANCE
    distance_m: float = 0.0
    # STOP_POINT / POSE_TOLERANCE 的解析后目标位姿
    target_pose: Pose2D | None = None
    # WORK_DONE
    required_count: int = 1
    # 是否用航向做确认（B1 默认关闭：转弯靠巡线自己判断）
    require_yaw: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ExitKind):
            raise RoutePlanError(f"exit kind must be ExitKind, got {self.kind!r}")
        if self.settle_samples < 1:
            raise RoutePlanError("settle_samples must be >= 1")
        if self.tolerance_m <= 0.0 or not math.isfinite(self.tolerance_m):
            raise RoutePlanError("tolerance_m must be positive and finite")
        if self.tolerance_rad <= 0.0 or not math.isfinite(self.tolerance_rad):
            raise RoutePlanError("tolerance_rad must be positive and finite")

        if self.kind is ExitKind.JUNCTION_TURN:
            if not self.at_ref:
                raise RoutePlanError("JUNCTION_TURN requires at_ref")
            if self.turn is None:
                raise RoutePlanError("JUNCTION_TURN requires turn direction")
            if not self.expected_line_states:
                raise RoutePlanError("JUNCTION_TURN requires expected_line_states")
            if self.passthrough_junctions < 0:
                raise RoutePlanError("passthrough_junctions cannot be negative")
            if self.require_yaw and self.target_yaw_rad is None:
                raise RoutePlanError("require_yaw needs target_yaw_rad")
        elif self.kind is ExitKind.LINE_END:
            if not self.expected_line_states:
                raise RoutePlanError("LINE_END requires expected_line_states")
        elif self.kind is ExitKind.ODOM_DISTANCE:
            if not math.isfinite(self.distance_m) or self.distance_m <= 0.0:
                raise RoutePlanError("ODOM_DISTANCE requires positive distance_m")
        elif self.kind in (ExitKind.STOP_POINT, ExitKind.POSE_TOLERANCE):
            if self.target_pose is None:
                raise RoutePlanError(f"{self.kind.value} requires target_pose")
            if not self.target_ref:
                raise RoutePlanError(f"{self.kind.value} requires target_ref")
        elif self.kind is ExitKind.YAW_TARGET:
            if self.target_yaw_rad is None or not math.isfinite(self.target_yaw_rad):
                raise RoutePlanError("YAW_TARGET requires finite target_yaw_rad")
        elif self.kind is ExitKind.WORK_DONE:
            if self.required_count < 1:
                raise RoutePlanError("WORK_DONE requires required_count >= 1")
        for state in self.expected_line_states:
            if not isinstance(state, LineSensorState):
                raise RoutePlanError(f"expected_line_states must be LineSensorState, got {state!r}")


@dataclass(frozen=True)
class TurnSpec:
    """登记的路口转向点（B3 实现控制时按它接线）。"""

    segment_id: str
    ref: str
    direction: TurnDirection
    target_yaw_rad: float | None
    passthrough_junctions: int
    settle_samples: int
    require_yaw: bool


@dataclass(frozen=True)
class CargoPlan:
    """本次任务的载货与搭建计划（3 橙、2 层；规则上限由 Cargo 单点校验）。"""

    orange: int = 3
    purple: int = 0
    layers: int = 2
    layout: str = "2+1"  # 底层 2 块并排 + 顶层 1 块
    note: str = "只停 W02 取 3 橙；搭建区高 10cm，车不上台"

    def __post_init__(self) -> None:
        if self.layers < 1:
            raise RoutePlanError("layers must be >= 1")
        if not self.layout:
            raise RoutePlanError("layout must be non-empty")
        # 用 Cargo 的真实约束校验（3 块上限 / 至多 1 紫），不复制规则逻辑
        cargo = Cargo()
        for _ in range(self.orange):
            if not cargo.can_add(CubeColor.ORANGE):
                raise RoutePlanError(f"cargo plan exceeds capacity: {self.orange} orange")
            cargo.add(CubeColor.ORANGE)
        for _ in range(self.purple):
            if not cargo.can_add(CubeColor.PURPLE):
                raise RoutePlanError(f"cargo plan exceeds capacity: {self.purple} purple")
            cargo.add(CubeColor.PURPLE)
        if cargo.total < self.layers:
            raise RoutePlanError("cargo plan cannot build the requested layers")


#: 现场确认的载货/搭建计划（3 橙、只停 W02、搭 2 层：底 2 + 顶 1）
CARGO_PLAN_DEFAULT = CargoPlan()


@dataclass(frozen=True)
class SpeedLimits:
    """底盘限幅（必须 ≤ 固件限幅 0.3/0.3/1.0，超限触发协议故障锁存）。"""

    max_vx: float = 0.30
    max_vy: float = 0.30
    max_wz: float = 1.00

    def __post_init__(self) -> None:
        for name, value in (("max_vx", self.max_vx), ("max_vy", self.max_vy), ("max_wz", self.max_wz)):
            if not math.isfinite(value) or value <= 0.0:
                raise RoutePlanError(f"{name} must be positive and finite")


# ----------------------------------------------------------------------------
# 路线数据
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteSegmentPlan:
    """一段路：走哪条（实测）边、什么朝向、限速、怎么算走完。"""

    id: str
    role: SegmentRole
    label: str
    from_ref: str
    to_ref: str
    from_pose: Pose2D
    to_pose: Pose2D
    exit: ExitCriteria
    edges: tuple[str, ...] = ()
    heading: str = "free"
    max_speed_mps: float = 0.0  # 0.0 = 不覆盖链默认
    max_yaw_radps: float = 0.0
    passthrough_refs: tuple[str, ...] = ()
    evidence: str = "estimated"
    note: str = ""
    #: WORK 段的作业类型（取 / 放）；非 WORK 段必须为 None
    work: WorkKind | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.label:
            raise RoutePlanError("segment requires id and label")
        if not isinstance(self.role, SegmentRole):
            raise RoutePlanError(f"{self.id}: role must be SegmentRole")
        if self.role is SegmentRole.WORK:
            if not isinstance(self.work, WorkKind):
                raise RoutePlanError(f"{self.id}: WORK segment must declare work kind (PICK/PLACE)")
        elif self.work is not None:
            raise RoutePlanError(f"{self.id}: only WORK segments may declare a work kind")
        if self.heading not in HEADINGS:
            raise RoutePlanError(f"{self.id}: unknown heading {self.heading!r}")
        if self.evidence not in EVIDENCE_LEVELS:
            raise RoutePlanError(f"{self.id}: evidence must be one of {EVIDENCE_LEVELS}")
        for name, value in (
            ("max_speed_mps", self.max_speed_mps),
            ("max_yaw_radps", self.max_yaw_radps),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise RoutePlanError(f"{self.id}: {name} must be finite and non-negative")
        if self.role in (SegmentRole.WORK, SegmentRole.TURN) and self.max_speed_mps != 0.0:
            raise RoutePlanError(f"{self.id}: {self.role.value} segment must not drive (max_speed_mps must be 0)")
        if self.role in ROLE_EDGE_KIND and not self.edges:
            raise RoutePlanError(f"{self.id}: {self.role.value} segment must declare edges")
        if self.role not in ROLE_EDGE_KIND and self.edges:
            raise RoutePlanError(f"{self.id}: {self.role.value} segment must not declare line edges")

    @property
    def kind(self) -> SegmentKind:
        """映射到 C1 的 SegmentKind。"""
        return ROLE_TO_KIND[self.role]

    @property
    def is_in_place(self) -> bool:
        """原地段（起点终点同一位置）。"""
        return _same_xy(self.from_pose, self.to_pose)


@dataclass(frozen=True)
class RoutePlan:
    """一整条比赛路线 + 自检。

    构造即校验：任何一条不合法都抛 `RoutePlanError`——路线是「要开真车」的数据，
    不允许带病上路。
    """

    start_pose: Pose2D
    segments: tuple[RouteSegmentPlan, ...]
    refs: Mapping[str, Pose2D]
    node_types: Mapping[str, str]
    edge_kinds: Mapping[str, str]
    edge_endpoints: Mapping[str, tuple[str, ...]]
    cargo_plan: CargoPlan = field(default_factory=CargoPlan)
    speed_limits: SpeedLimits = field(default_factory=SpeedLimits)
    default_speed_mps: float = 0.15
    default_yaw_radps: float = 0.50
    version: str = ROUTE_VERSION
    source_note: str = ROUTE_SOURCE_NOTE

    def __post_init__(self) -> None:
        if not self.version:
            raise RoutePlanError("version must be non-empty")
        if not self.segments:
            raise RoutePlanError("route requires at least one segment")
        if not math.isfinite(self.default_speed_mps) or self.default_speed_mps <= 0.0:
            raise RoutePlanError("default_speed_mps must be positive and finite")
        if not math.isfinite(self.default_yaw_radps) or self.default_yaw_radps <= 0.0:
            raise RoutePlanError("default_yaw_radps must be positive and finite")
        if self.default_speed_mps > self.speed_limits.max_vx:
            raise RoutePlanError("default_speed_mps exceeds chassis limit")
        if self.default_yaw_radps > self.speed_limits.max_wz:
            raise RoutePlanError("default_yaw_radps exceeds chassis limit")

        ids = [segment.id for segment in self.segments]
        if len(set(ids)) != len(ids):
            raise RoutePlanError("segment ids must be unique")

        for segment in self.segments:
            self._check_refs(segment)
            self._check_speeds(segment)
            self._check_edges(segment)
            self._check_heading(segment)
            self._check_exit(segment)

        # 作业段的作业次数必须等于载货计划里的总块数：取块段要抓完所有块，
        # 搭建段要放完所有块。两者不一致时（例如把计划改成 2 块却忘了改 required_count）
        # 会出现「抓 3 次但只有 2 块」这种到现场才暴露的错误。
        cargo_total = self.cargo_plan.orange + self.cargo_plan.purple
        for segment in self.segments:
            if segment.role is SegmentRole.WORK and segment.exit.required_count != cargo_total:
                raise RoutePlanError(
                    f"{segment.id}: required_count={segment.exit.required_count} "
                    f"但载货计划共 {cargo_total} 块——两者必须一致"
                )

        previous: RouteSegmentPlan | None = None
        for segment in self.segments:
            if previous is not None:
                if not _same_xy(previous.to_pose, segment.from_pose):
                    raise RoutePlanError(
                        f"route is not continuous between {previous.id} and {segment.id}: "
                        f"{previous.to_pose} -> {segment.from_pose}"
                    )
                self._check_yaw_continuity(previous, segment)
            previous = segment

        if not _same_pose(self.start_pose, self.segments[0].from_pose):
            raise RoutePlanError("start_pose must equal first segment from_pose")

    # -- 自检细节 ---------------------------------------------------------
    def _check_yaw_continuity(self, previous: RouteSegmentPlan, current: RouteSegmentPlan) -> None:
        """航向连续性：普通边界必须同向；转向边界必须正好转出登记的角度。

        为什么不能一刀切要求「上一段末航向 == 下一段起航向」：转向段（如 S01 以
        +y 到达 N02、S02 以 +x 出发）在边界上航向**本来就该**差 90°。反过来，
        把「计划写了右转、实测几何是左转」这种矛盾查出来，才是这条检查的价值。
        """
        delta = angle_diff(current.from_pose.yaw, previous.to_pose.yaw)
        turn = previous.exit.turn if previous.exit.kind is ExitKind.JUNCTION_TURN else None
        if turn is None:
            if abs(delta) > YAW_TOLERANCE_RAD:
                raise RoutePlanError(
                    f"heading discontinuity between {previous.id} and {current.id}: "
                    f"{previous.to_pose.yaw:.4f} -> {current.from_pose.yaw:.4f} without a registered turn"
                )
            return
        if turn is TurnDirection.AROUND:
            if abs(abs(delta) - math.pi) > YAW_TOLERANCE_RAD:
                raise RoutePlanError(
                    f"{previous.id}: AROUND turn must change heading by 180°, measured {math.degrees(delta):.1f}°"
                )
        elif abs(angle_diff(delta, TURN_YAW_DELTA[turn])) > YAW_TOLERANCE_RAD:
            raise RoutePlanError(
                f"{previous.id}: declared {turn.value} turn does not match measured heading change "
                f"({math.degrees(delta):.1f}°)"
            )
        target = previous.exit.target_yaw_rad
        if target is not None and abs(angle_diff(current.from_pose.yaw, target)) > YAW_TOLERANCE_RAD:
            raise RoutePlanError(
                f"{previous.id}: declared target_yaw_rad {target:.4f} does not match next segment "
                f"departure heading {current.from_pose.yaw:.4f}"
            )
    def _check_refs(self, segment: RouteSegmentPlan) -> None:
        for ref in (segment.from_ref, segment.to_ref):
            if ref not in self.refs:
                raise RoutePlanError(f"{segment.id}: unknown ref {ref!r} (not in survey)")
        if not _same_xy(segment.from_pose, self.refs[segment.from_ref]):
            raise RoutePlanError(f"{segment.id}: from_pose does not match survey ref {segment.from_ref}")
        if not _same_xy(segment.to_pose, self.refs[segment.to_ref]):
            raise RoutePlanError(f"{segment.id}: to_pose does not match survey ref {segment.to_ref}")

    def _check_speeds(self, segment: RouteSegmentPlan) -> None:
        if segment.max_speed_mps > self.speed_limits.max_vx:
            raise RoutePlanError(
                f"{segment.id}: max_speed_mps {segment.max_speed_mps} exceeds chassis limit "
                f"{self.speed_limits.max_vx}"
            )
        if segment.max_yaw_radps > self.speed_limits.max_wz:
            raise RoutePlanError(
                f"{segment.id}: max_yaw_radps {segment.max_yaw_radps} exceeds chassis limit "
                f"{self.speed_limits.max_wz}"
            )

    def _check_edges(self, segment: RouteSegmentPlan) -> None:
        expected_kind = ROLE_EDGE_KIND.get(segment.role)
        if expected_kind is None:
            return
        for edge_id in segment.edges:
            if edge_id not in self.edge_kinds:
                raise RoutePlanError(f"{segment.id}: unknown edge {edge_id!r} (not in survey)")
            if self.edge_kinds[edge_id] != expected_kind:
                raise RoutePlanError(
                    f"{segment.id}: edge {edge_id} kind {self.edge_kinds[edge_id]!r} "
                    f"does not match role {segment.role.value} (expected {expected_kind!r})"
                )

    def _check_heading(self, segment: RouteSegmentPlan) -> None:
        if segment.heading == "free":
            return
        want = AXIS_HEADINGS[segment.heading]
        dx = segment.to_pose.x - segment.from_pose.x
        dy = segment.to_pose.y - segment.from_pose.y
        if math.hypot(dx, dy) < 1e-9:
            raise RoutePlanError(f"{segment.id}: heading {segment.heading} declared but segment does not move")
        actual = math.atan2(dy, dx)
        if abs(angle_diff(actual, want)) > POSITION_TOLERANCE_M:
            raise RoutePlanError(
                f"{segment.id}: heading {segment.heading} does not match measured geometry "
                f"(dx={dx:.4f}, dy={dy:.4f})"
            )
        # 段末车头朝向必须与声明朝向一致（末段由目标位姿给出，见 build_route_plan）
        if abs(angle_diff(segment.to_pose.yaw, want)) > YAW_TOLERANCE_RAD:
            raise RoutePlanError(
                f"{segment.id}: to_pose yaw {segment.to_pose.yaw:.4f} does not match heading {segment.heading}"
            )

    def _check_exit(self, segment: RouteSegmentPlan) -> None:
        criteria = segment.exit
        if criteria.kind in POSE_DEPENDENT_EXITS and segment.evidence != "estimated":
            raise RoutePlanError(
                f"{segment.id}: exit {criteria.kind.value} depends on uncalibrated pose/heading, "
                "evidence must be 'estimated'"
            )
        if criteria.kind is ExitKind.JUNCTION_TURN:
            if criteria.at_ref != segment.to_ref:
                raise RoutePlanError(
                    f"{segment.id}: JUNCTION_TURN at_ref {criteria.at_ref!r} must equal to_ref {segment.to_ref!r}"
                )
            node_type = self.node_types.get(criteria.at_ref)
            if node_type not in ("junction", "turn"):
                raise RoutePlanError(
                    f"{segment.id}: turn point {criteria.at_ref!r} must be a measured junction/turn, "
                    f"got {node_type!r}"
                )
        for ref in segment.passthrough_refs:
            if ref in (segment.from_ref, segment.to_ref):
                raise RoutePlanError(f"{segment.id}: passthrough ref {ref!r} duplicates an endpoint")
            reachable = set()
            for edge_id in segment.edges:
                reachable.update(self.edge_endpoints.get(edge_id, ()))
            if ref not in reachable:
                raise RoutePlanError(
                    f"{segment.id}: passthrough ref {ref!r} is not on the declared edges {segment.edges}"
                )

    # -- 查询 -------------------------------------------------------------
    def segment(self, segment_id: str) -> RouteSegmentPlan:
        for segment in self.segments:
            if segment.id == segment_id:
                return segment
        raise KeyError(segment_id)

    @property
    def segment_ids(self) -> tuple[str, ...]:
        return tuple(segment.id for segment in self.segments)

    def turn_registry(self) -> tuple[TurnSpec, ...]:
        """所有路口转向点（B3 按它接线；B1 只登记 + 自检）。"""
        registry = []
        for segment in self.segments:
            criteria = segment.exit
            if criteria.kind is ExitKind.JUNCTION_TURN:
                registry.append(
                    TurnSpec(
                        segment_id=segment.id,
                        ref=criteria.at_ref,
                        direction=criteria.turn,
                        target_yaw_rad=criteria.target_yaw_rad,
                        passthrough_junctions=criteria.passthrough_junctions,
                        settle_samples=criteria.settle_samples,
                        require_yaw=criteria.require_yaw,
                    )
                )
        return tuple(registry)

    def passthrough_registry(self) -> tuple[tuple[str, str], ...]:
        """直行通过的路口：(segment_id, ref)。"""
        return tuple(
            (segment.id, ref) for segment in self.segments for ref in segment.passthrough_refs
        )

    def summary(self) -> list[dict[str, Any]]:
        """只读摘要（B2 推到网页显示「第几段 / 下一步」用）。"""
        rows = []
        for index, segment in enumerate(self.segments):
            rows.append(
                {
                    "index": index,
                    "id": segment.id,
                    "role": segment.role.value,
                    "kind": segment.kind.value,
                    "label": segment.label,
                    "from": segment.from_ref,
                    "to": segment.to_ref,
                    "heading": segment.heading,
                    "max_speed_mps": segment.max_speed_mps,
                    "exit": segment.exit.kind.value,
                    "exit_ref": segment.exit.at_ref or segment.exit.target_ref,
                    "evidence": segment.evidence,
                    "work": None if segment.work is None else segment.work.value,
                }
            )
        return rows


def _same_xy(a: Pose2D, b: Pose2D, tol: float = POSITION_TOLERANCE_M) -> bool:
    return abs(a.x - b.x) <= tol and abs(a.y - b.y) <= tol


def _same_pose(a: Pose2D, b: Pose2D, tol: float = POSITION_TOLERANCE_M) -> bool:
    return _same_xy(a, b, tol) and abs(angle_diff(a.yaw, b.yaw)) <= YAW_TOLERANCE_RAD


# ----------------------------------------------------------------------------
# 从实测场地数据构建路线
# ----------------------------------------------------------------------------


def _section(survey: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = survey.get(name)
    if not isinstance(value, Mapping) or not value:
        raise RoutePlanError(f"survey section {name!r} missing or empty")
    return value


def _require_number(mapping: Mapping[str, Any], key: str, where: str) -> float:
    if key not in mapping:
        raise RoutePlanError(f"{where}: missing {key!r}")
    try:
        value = float(mapping[key])
    except (TypeError, ValueError) as exc:
        raise RoutePlanError(f"{where}: {key!r} must be a number") from exc
    if not math.isfinite(value):
        raise RoutePlanError(f"{where}: {key!r} must be finite")
    return value


def build_route_plan(survey: Mapping[str, Any]) -> RoutePlan:
    """把 `field_layout.yaml` 的 survey 段编译成自检过的 `RoutePlan`。

    本函数只声明「走哪条边、什么朝向、怎么算走完」；坐标一律从 survey 解析，
    不写死——现场重新测量后改 yaml，路线自动跟着走（并由段间连续性自检兜住）。
    """
    nodes = _section(survey, "line_nodes")
    edges = _section(survey, "line_edges")
    stops = _section(survey, "stops")

    refs: dict[str, Pose2D] = {}
    node_types: dict[str, str] = {}
    for node_id, node in nodes.items():
        node_types[node_id] = str(node.get("type", ""))
        # 节点本身不带朝向，yaw 占位 0.0；段的位姿在下面按朝向显式给定
        refs[node_id] = Pose2D(_require_number(node, "x", node_id), _require_number(node, "y", node_id), 0.0)
    for stop_id, stop in stops.items():
        yaw = _require_number(stop, "yaw", stop_id)
        refs[stop_id] = Pose2D(_require_number(stop, "x", stop_id), _require_number(stop, "y", stop_id), yaw)

    edge_kinds: dict[str, str] = {}
    edge_endpoints: dict[str, tuple[str, ...]] = {}
    for edge_id, edge in edges.items():
        edge_kinds[edge_id] = str(edge.get("kind", ""))
        endpoints = [str(edge.get("from", "")), str(edge.get("to", ""))]
        endpoints.extend(str(item) for item in edge.get("via", []) or [])
        edge_endpoints[edge_id] = tuple(item for item in endpoints if item)

    def pos(ref: str) -> tuple[float, float]:
        if ref not in refs:
            raise RoutePlanError(f"unknown ref {ref!r} in route table")
        return refs[ref].x, refs[ref].y

    def pose(ref: str, yaw: float) -> Pose2D:
        x, y = pos(ref)
        return Pose2D(x, y, yaw)

    def stop_yaw(ref: str) -> float:
        if ref not in stops:
            raise RoutePlanError(f"{ref!r} is not a stop point")
        return refs[ref].yaw

    junction_states = (LineSensorState.INTERSECTION, LineSensorState.ALL_BLACK)
    lost_states = (LineSensorState.LOST,)

    segments: list[RouteSegmentPlan] = []

    # S01 启动区出线 → N02 右转
    segments.append(
        RouteSegmentPlan(
            id="S01_LINE_START",
            role=SegmentRole.LINE,
            label="启动区出线巡线到 N02",
            from_ref="W01",
            to_ref="N02",
            from_pose=pose("W01", stop_yaw("W01")),  # 启动姿态朝 +y
            to_pose=pose("N02", AXIS_HEADINGS["y+"]),
            edges=("E01",),
            heading="y+",
            max_speed_mps=0.20,
            exit=ExitCriteria(
                kind=ExitKind.JUNCTION_TURN,
                at_ref="N02",
                expected_line_states=junction_states,
                turn=TurnDirection.RIGHT,
                target_yaw_rad=AXIS_HEADINGS["x+"],
                settle_samples=3,
            ),
            evidence="measured",
            note="E01 尽头是 L 拐角：车沿 +y 走到 N02 后要右转向 +x",
        )
    )

    # S02 主路向东 → N06 左转（N03 直行通过）
    segments.append(
        RouteSegmentPlan(
            id="S02_LINE_MAIN",
            role=SegmentRole.LINE,
            label="主路 y=1.6 巡线到 N06",
            from_ref="N02",
            to_ref="N06",
            from_pose=pose("N02", AXIS_HEADINGS["x+"]),
            to_pose=pose("N06", AXIS_HEADINGS["x+"]),
            edges=("E02",),
            heading="x+",
            max_speed_mps=0.25,
            passthrough_refs=("N03",),
            exit=ExitCriteria(
                kind=ExitKind.JUNCTION_TURN,
                at_ref="N06",
                expected_line_states=junction_states,
                turn=TurnDirection.LEFT,
                target_yaw_rad=AXIS_HEADINGS["y+"],
                passthrough_junctions=1,  # N03 是 T 形路口，直行通过
                settle_samples=3,
            ),
            evidence="measured",
            note="N03 直行通过（略过 1 个路口），N06 左转向 +y 接坡道",
        )
    )

    # S03 坡前接近段
    segments.append(
        RouteSegmentPlan(
            id="S03_RAMP_APPROACH",
            role=SegmentRole.LINE,
            label="N06 → 坡前 N07",
            from_ref="N06",
            to_ref="N07",
            from_pose=pose("N06", AXIS_HEADINGS["y+"]),
            to_pose=pose("N07", AXIS_HEADINGS["y+"]),
            edges=("E05",),
            heading="y+",
            max_speed_mps=0.15,
            exit=ExitCriteria(
                kind=ExitKind.ODOM_DISTANCE,
                target_ref="W06",
                distance_m=0.60,
            ),
            evidence="estimated",
            note="0.6m 已知短距离；W06 与 N07 同点（坡前等待位）",
        )
    )

    # S04 上坡
    segments.append(
        RouteSegmentPlan(
            id="S04_RAMP_UP",
            role=SegmentRole.RAMP_UP,
            label="上坡 N07 → N08",
            from_ref="N07",
            to_ref="N08",
            from_pose=pose("N07", AXIS_HEADINGS["y+"]),
            to_pose=pose("N08", AXIS_HEADINGS["y+"]),
            edges=("E06",),
            heading="y+",
            max_speed_mps=0.15,
            max_yaw_radps=0.30,
            exit=ExitCriteria(
                kind=ExitKind.ODOM_DISTANCE,
                target_ref="W07",
                distance_m=0.80,
            ),
            evidence="estimated",
            note="坡长 0.8m / 14°；打滑时会走不满，B3 接 C3 的 ramp_control 判据",
        )
    )

    # S05 高台上巡线到线尽头（N09 直行通过）
    segments.append(
        RouteSegmentPlan(
            id="S05_LINE_PLATFORM",
            role=SegmentRole.LINE,
            label="高台巡线 N08 → N10（墙材区抓取位）",
            from_ref="N08",
            to_ref="N10",
            from_pose=pose("N08", AXIS_HEADINGS["y+"]),
            to_pose=pose("N10", AXIS_HEADINGS["y+"]),
            edges=("E07", "E08"),
            heading="y+",
            max_speed_mps=0.15,
            passthrough_refs=("N09",),
            exit=ExitCriteria(
                kind=ExitKind.LINE_END,
                target_ref="W02",
                expected_line_states=lost_states,
                settle_samples=2,
            ),
            evidence="measured",
            note="N09 直行通过；N10 是 L4 终点（线尽头）= W02 红方墙材区抓取位",
        )
    )

    # S06 同点连取 3 块
    segments.append(
        RouteSegmentPlan(
            id="S06_PICK3",
            role=SegmentRole.WORK,
            label="W02 连续取 3 橙块存入本体框",
            from_ref="W02",
            to_ref="W02",
            from_pose=pose("W02", stop_yaw("W02")),
            to_pose=pose("W02", stop_yaw("W02")),
            exit=ExitCriteria(kind=ExitKind.WORK_DONE, target_ref="W02", required_count=3),
            evidence="measured",
            work=WorkKind.PICK,
            note="槽距约 0.15m：需 3 次抓取 + 槽间微移，微移方式（里程计/视觉）B3 现场定",
        )
    )

    # S07 原地掉头
    segments.append(
        RouteSegmentPlan(
            id="S07_TURN_AROUND",
            role=SegmentRole.TURN,
            label="W02 原地掉头朝 -y",
            from_ref="W02",
            to_ref="W02",
            from_pose=pose("W02", stop_yaw("W02")),
            to_pose=pose("W02", AXIS_HEADINGS["y-"]),
            exit=ExitCriteria(
                kind=ExitKind.YAW_TARGET,
                target_ref="W02",
                target_yaw_rad=AXIS_HEADINGS["y-"],
                tolerance_rad=0.10,
            ),
            evidence="estimated",
            note="依赖航向；imu_valid=false 时 B3 需改用「沿线折返」方案",
        )
    )

    # S08 原路返回坡顶
    segments.append(
        RouteSegmentPlan(
            id="S08_LINE_BACK_PLATFORM",
            role=SegmentRole.LINE,
            label="高台原路返回坡顶 N10 → N08",
            from_ref="N10",
            to_ref="N08",
            from_pose=pose("N10", AXIS_HEADINGS["y-"]),
            to_pose=pose("N08", AXIS_HEADINGS["y-"]),
            edges=("E08", "E07"),
            heading="y-",
            max_speed_mps=0.15,
            passthrough_refs=("N09",),
            exit=ExitCriteria(kind=ExitKind.ODOM_DISTANCE, target_ref="W07", distance_m=1.70),
            evidence="estimated",
            note="返程仍沿同一条线（E08+E07 反向）；N09 直行通过",
        )
    )

    # S09 下坡
    segments.append(
        RouteSegmentPlan(
            id="S09_RAMP_DOWN",
            role=SegmentRole.RAMP_DOWN,
            label="下坡 N08 → N07",
            from_ref="N08",
            to_ref="N07",
            from_pose=pose("N08", AXIS_HEADINGS["y-"]),
            to_pose=pose("N07", AXIS_HEADINGS["y-"]),
            edges=("E06",),
            heading="y-",
            max_speed_mps=0.15,
            max_yaw_radps=0.30,
            exit=ExitCriteria(kind=ExitKind.ODOM_DISTANCE, target_ref="W06", distance_m=0.80),
            evidence="estimated",
            note="下坡防冲由 C3 ramp_control 负责；B3 接入",
        )
    )

    # S10 主路返程到线尽头（N06 直行通过）
    segments.append(
        RouteSegmentPlan(
            id="S10_LINE_BACK_MAIN",
            role=SegmentRole.LINE,
            label="主路返程 N07 → N05 线尽头",
            from_ref="N07",
            to_ref="N05",
            from_pose=pose("N07", AXIS_HEADINGS["y-"]),
            to_pose=pose("N05", AXIS_HEADINGS["y-"]),
            edges=("E05", "E04"),
            heading="y-",
            max_speed_mps=0.20,
            passthrough_refs=("N06",),
            exit=ExitCriteria(
                kind=ExitKind.LINE_END,
                target_ref="N05",
                expected_line_states=lost_states,
                settle_samples=2,
            ),
            evidence="measured",
            note="N06 直行通过（L4 与主路交叉）；N05 是 L4 底端终点",
        )
    )

    # S11 平移进搭建位
    segments.append(
        RouteSegmentPlan(
            id="S11_SHIFT_TO_BUILD",
            role=SegmentRole.SHIFT,
            label="N05 → 搭建放置位 W04",
            from_ref="N05",
            to_ref="W04",
            from_pose=pose("N05", AXIS_HEADINGS["y-"]),
            to_pose=pose("W04", stop_yaw("W04")),
            heading="free",
            max_speed_mps=0.10,
            exit=ExitCriteria(
                kind=ExitKind.POSE_TOLERANCE,
                target_ref="W04",
                target_pose=pose("W04", stop_yaw("W04")),
                tolerance_m=0.05,
                tolerance_rad=0.0872665,
            ),
            evidence="estimated",
            note="W04 不在黑线上（比 N05 偏 x-0.30 / y+0.10）：0.32m 侧向平移，麦轮可做",
        )
    )

    # S12 搭建 2 层
    segments.append(
        RouteSegmentPlan(
            id="S12_BUILD_2LAYER",
            role=SegmentRole.WORK,
            label="W04 搭建 2 层（底 2 + 顶 1）",
            from_ref="W04",
            to_ref="W04",
            from_pose=pose("W04", stop_yaw("W04")),
            to_pose=pose("W04", stop_yaw("W04")),
            exit=ExitCriteria(kind=ExitKind.WORK_DONE, target_ref="W04", required_count=3),
            evidence="measured",
            work=WorkKind.PLACE,
            note="搭建区高 10cm，车不上台，只在外沿放置；3s 稳定观察仍由 mission 级 VERIFY_BUILD 兜底",
        )
    )

    # S13 撤退
    segments.append(
        RouteSegmentPlan(
            id="S13_RETREAT",
            role=SegmentRole.SHIFT,
            label="W04 → 撤退位 W05",
            from_ref="W04",
            to_ref="W05",
            from_pose=pose("W04", stop_yaw("W04")),
            to_pose=pose("W05", stop_yaw("W05")),
            heading="y+",
            max_speed_mps=0.15,
            exit=ExitCriteria(
                kind=ExitKind.POSE_TOLERANCE,
                target_ref="W05",
                target_pose=pose("W05", stop_yaw("W05")),
                tolerance_m=0.05,
                tolerance_rad=0.0872665,
            ),
            evidence="estimated",
            note="放完退回主路，避免挡住搭建区",
        )
    )

    return RoutePlan(
        start_pose=pose("W01", stop_yaw("W01")),
        segments=tuple(segments),
        refs=refs,
        node_types=node_types,
        edge_kinds=edge_kinds,
        edge_endpoints=edge_endpoints,
    )


# ----------------------------------------------------------------------------
# 运行时观测量与退出判据求值
# ----------------------------------------------------------------------------


@dataclass
class RouteObservations:
    """段执行期间可用的观测量（由调用方更新；本类只做计数与清零）。

    计数都是「本段内」的语义：段切换时由 `RouteRunner` 调 `reset_segment()`。
    """

    line_state: LineSensorState = LineSensorState.LOST
    state_samples: int = 0  # 当前巡线状态已连续多少拍
    junction_events: int = 0  # 本段内进入路口签名的次数
    pose: Pose2D | None = None
    yaw_rad: float | None = None
    distance_m: float = 0.0  # 本段累计位移
    work_count: int = 0  # 本段完成作业次数
    stale: bool = False  # 读数过期：任何退出判据都不得成立（安全）

    def update_line(self, state: LineSensorState) -> None:
        """喂一帧巡线状态：维护稳定拍数，并把「非路口 → 路口」计为一次路口事件。"""
        if not isinstance(state, LineSensorState):
            raise ValueError(f"line state must be LineSensorState, got {state!r}")
        if self.line_state is state:
            self.state_samples += 1
            return
        if state in JUNCTION_STATES and self.line_state not in JUNCTION_STATES:
            self.junction_events += 1
        self.line_state = state
        self.state_samples = 1

    def observe_pose(self, pose: Pose2D) -> None:
        if not (math.isfinite(pose.x) and math.isfinite(pose.y) and math.isfinite(pose.yaw)):
            raise ValueError("pose must be finite")
        self.pose = pose
        self.yaw_rad = pose.yaw

    def observe_distance(self, distance_m: float) -> None:
        if not math.isfinite(distance_m) or distance_m < 0.0:
            raise ValueError("distance_m must be finite and non-negative")
        self.distance_m = distance_m

    def note_work_done(self) -> None:
        self.work_count += 1

    def mark_stale(self, stale: bool = True) -> None:
        self.stale = bool(stale)

    def reset_segment(self) -> None:
        """段切换：清本段累计量（保留绝对位姿与过期标志）。"""
        self.state_samples = 0
        self.junction_events = 0
        self.distance_m = 0.0
        self.work_count = 0

    def clear(self) -> None:
        """整条路线复位。"""
        self.line_state = LineSensorState.LOST
        self.stale = False
        self.reset_segment()


def _pose_within(pose: Pose2D | None, target: Pose2D, tolerance_m: float, tolerance_rad: float) -> bool:
    if pose is None:
        return False
    if math.hypot(pose.x - target.x, pose.y - target.y) > tolerance_m:
        return False
    return abs(angle_diff(pose.yaw, target.yaw)) <= tolerance_rad


def evaluate_exit(criteria: ExitCriteria, observations: RouteObservations) -> bool:
    """退出判据求值（纯函数）。

    安全约定：`observations.stale` 为真时一律不成立——读数过期不能算「走完了」。
    """
    if observations.stale:
        return False

    kind = criteria.kind
    if kind is ExitKind.JUNCTION_TURN:
        # 先略过要直行通过的路口，再等线重新捕获并稳定
        if observations.junction_events <= criteria.passthrough_junctions:
            return False
        if observations.line_state is not LineSensorState.ON_LINE:
            return False
        if observations.state_samples < criteria.settle_samples:
            return False
        if criteria.require_yaw:
            if observations.yaw_rad is None or criteria.target_yaw_rad is None:
                return False
            if abs(angle_diff(observations.yaw_rad, criteria.target_yaw_rad)) > criteria.tolerance_rad:
                return False
        return True

    if kind is ExitKind.LINE_END:
        return (
            observations.line_state in criteria.expected_line_states
            and observations.state_samples >= criteria.settle_samples
        )

    if kind is ExitKind.ODOM_DISTANCE:
        return observations.distance_m >= criteria.distance_m

    if kind in (ExitKind.STOP_POINT, ExitKind.POSE_TOLERANCE):
        return _pose_within(
            observations.pose, criteria.target_pose, criteria.tolerance_m, criteria.tolerance_rad
        )

    if kind is ExitKind.YAW_TARGET:
        if observations.yaw_rad is None or criteria.target_yaw_rad is None:
            return False
        return abs(angle_diff(observations.yaw_rad, criteria.target_yaw_rad)) <= criteria.tolerance_rad

    if kind is ExitKind.WORK_DONE:
        return observations.work_count >= criteria.required_count

    raise ValueError(f"unhandled exit kind: {kind!r}")


# ----------------------------------------------------------------------------
# 编译成 C1 路段链 + 运行器
# ----------------------------------------------------------------------------


def compile_route(
    plan: RoutePlan,
    observations: RouteObservations,
    *,
    default_speed_mps: float | None = None,
    default_yaw_radps: float | None = None,
) -> RouteChain:
    """把 `RoutePlan` 编译成 C1 的 `RouteChain`（退出判据绑到观测快照）。

    注意 C1 的语义（`route_segment.py:149-153`）：`exit_check=None` 表示
    **无条件立即推进**，因此每一段都必须绑定真实判据；`entry_check` 的语义是
    「离开本段的前置条件」，B1 全部不使用（保持 `None`）。
    """

    def make_check(criteria: ExitCriteria) -> Callable[[], bool]:
        return lambda: evaluate_exit(criteria, observations)

    segments = [
        RouteSegment(
            kind=segment.kind,
            max_speed_mps=segment.max_speed_mps,
            max_yaw_radps=segment.max_yaw_radps,
            entry_check=None,
            exit_check=make_check(segment.exit),
            label=f"{segment.id} {segment.label}",
        )
        for segment in plan.segments
    ]
    return RouteChain(
        segments=segments,
        default_speed_mps=plan.default_speed_mps if default_speed_mps is None else default_speed_mps,
        default_yaw_radps=plan.default_yaw_radps if default_yaw_radps is None else default_yaw_radps,
    )


class RouteRunner:
    """路线运行器：喂观测 → tick 推进段。

    只做「段推进」，不下发任何命令（命令由 B2 的 `mission_manager` 按当前段的
    role 决定走 `/motion/goal` 还是巡线授权）。
    """

    def __init__(self, plan: RoutePlan, *, observations: RouteObservations | None = None) -> None:
        self.plan = plan
        self.observations = observations if observations is not None else RouteObservations()
        self.chain = compile_route(plan, self.observations)
        self._started = False
        self.switch_log: list[str] = []

    # -- 查询 -------------------------------------------------------------
    @property
    def status(self) -> ChainStatus:
        return self.chain.status

    @property
    def is_complete(self) -> bool:
        return self.chain.is_complete

    @property
    def segment_index(self) -> int:
        return self.chain.current_index

    @property
    def current_segment(self) -> RouteSegmentPlan | None:
        if self.chain.status is not ChainStatus.RUNNING:
            return None
        return self.plan.segments[self.chain.current_index]

    @property
    def current_speed(self) -> float:
        return self.chain.current_speed

    @property
    def current_yaw_limit(self) -> float:
        return self.chain.current_yaw_limit

    @property
    def detail(self) -> str:
        return self.chain.detail

    # -- 观测输入 ---------------------------------------------------------
    def observe_line(self, state: LineSensorState) -> None:
        self.observations.update_line(state)

    def observe_pose(self, pose: Pose2D) -> None:
        self.observations.observe_pose(pose)

    def observe_distance(self, distance_m: float) -> None:
        self.observations.observe_distance(distance_m)

    def note_work_done(self) -> None:
        self.observations.note_work_done()

    def mark_stale(self, stale: bool = True) -> None:
        self.observations.mark_stale(stale)

    # -- 推进 -------------------------------------------------------------
    def start(self) -> None:
        if self._started:
            raise ValueError("route runner can only be started once (use reset())")
        self.chain.start()
        self.observations.reset_segment()
        self._started = True
        self.switch_log.append(f"start: {self.chain.detail}")

    def tick(self) -> bool:
        """推进一拍；返回 True 表示发生了段切换（调用方应刷新命令来源）。"""
        switched = self.chain.tick()
        if switched:
            self.observations.reset_segment()
            self.switch_log.append(self.chain.detail)
        return switched

    def reset(self) -> None:
        self.chain.reset()
        self.observations.clear()
        self._started = False
        self.switch_log.clear()
