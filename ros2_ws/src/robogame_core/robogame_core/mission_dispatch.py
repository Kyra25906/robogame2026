"""B2: 「段 → 命令」的分发决策（纯逻辑，零 ROS）。

## 这个模块解决什么

B1 把比赛路线变成了 13 段数据（`mission_route.py`），但「每一段到底谁有权驱动底盘、
要不要发导航目标、给机构发什么命令」还没定。本模块把这件事定成**一个纯函数**
（`decide`），`mission_manager` 只负责把结果翻译成 ROS 话题。

这样安排的理由：授权切换是「无人干预自主完赛」里最容易出错、也最危险的一环
（两个节点同时驱动底盘 = 车乱走）。把它做成纯函数以后，可以在没有 ROS、没有车的
情况下把每条规则钉死并测试。

## 谁能驱动底盘（单一授权）

`/mission/active_source` 是**唯一授权话题**：任何时刻只有一个来源被允许驱动底盘。

| 段类型 | 授权来源 | 为什么 |
|---|---|---|
| 巡线 / 上坡 / 下坡 | `line_follow` | 灰度阵列负责横向，PD 纠偏 |
| 平移 / 原地转向 | `motion_control` | 走 `/motion/goal` 位姿控制 |
| 作业（取 / 放） | `manipulator_client` | 视觉对准需要它自己动底盘靠近方块 |
| 路线结束 / 失败 / 急停 | `none` | 谁都不许驱动 |

安全例外（**不受授权限制**）：急停、通信丢失、机构故障仍然由机器人桥与各节点
自己的安全门控处理——授权只能「禁止运动」，永远不能「放行危险运动」。

## 与机构接口的对齐（重要）

`manipulator_client` 只接受 `PICK_ORANGE / PICK_PURPLE / PLACE_ORANGE /
PLACE_PURPLE` 四个命令（`manipulator_client/node.py:177`），且自己按
`placed_layers` 递增选择放置高度（`manipulator.select_place_height`）。所以本模块
**只生成这些既有命令**，不去改机构侧协议；同时把「3 块搭 2 层」对放置高度的要求
显式算出来（`placement_heights_for`）并与配置比对（`placement_heights_issue`），
不一致时给出人话原因——而不是让车默默搭成 3 层。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .cmd_vel_arbiter import SOURCE_ALIGN, SOURCE_LINE_FOLLOW, SOURCE_NAVIGATE
from .line_follow import LineSensorState
from .mission_route import CargoPlan, RouteSegmentPlan, SegmentRole, WorkKind
from .models import CubeColor, Pose2D
from .ramp_control import RampProfile
from .route_segment import SegmentKind

#: 谁都不许驱动底盘（路线结束 / 失败 / 安全停车）
SOURCE_NONE = "none"

#: 段类型 -> 授权来源
ROLE_ACTIVE_SOURCE: Mapping[SegmentRole, str] = {
    SegmentRole.LINE: SOURCE_LINE_FOLLOW,
    SegmentRole.RAMP_UP: SOURCE_LINE_FOLLOW,
    SegmentRole.RAMP_DOWN: SOURCE_LINE_FOLLOW,
    SegmentRole.SHIFT: SOURCE_NAVIGATE,
    SegmentRole.TURN: SOURCE_NAVIGATE,
    SegmentRole.WORK: SOURCE_ALIGN,
}

#: manipulator_client 接受的命令（不要在这里发明新命令）
WORK_COMMANDS: Mapping[tuple[WorkKind, CubeColor], str] = {
    (WorkKind.PICK, CubeColor.ORANGE): "PICK_ORANGE",
    (WorkKind.PICK, CubeColor.PURPLE): "PICK_PURPLE",
    (WorkKind.PLACE, CubeColor.ORANGE): "PLACE_ORANGE",
    (WorkKind.PLACE, CubeColor.PURPLE): "PLACE_PURPLE",
}

_LINE_STATES_BY_NAME = {state.value: state for state in LineSensorState}


# ---------------------------------------------------------------------------
# /line_follow/status 解析
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineStatus:
    """巡线节点上送的状态（mission_manager 用它判断「到路口了 / 线走到尽头了」）。"""

    state: LineSensorState
    stale: bool
    blocked: bool
    deviation: float | None = None
    #: 转弯阶段（B3；'' 表示当前没有在执行转弯）
    turn_phase: str = ""
    #: 坡道打滑判定（B3；NORMAL/SLIPPING/STUCK）
    ramp_decision: str = ""
    #: 本段生效巡线限速（B3）
    line_limit_mps: float | None = None


def parse_line_status(text: str) -> LineStatus:
    """解析 `/line_follow/status`（String）。

    优先解析 `#diag#` 之后的结构化 JSON（`line_follow_node` 专为机器读取输出，
    见 `line_follow_node.py:358-374`），失败则回退到前缀 `key=value`。

    **任何解析失败一律返回 `stale=True`**：宁可让段不推进（车原地等），
    也不能因为字符串格式变了就把「线还在」当成完成证据。
    """
    if not isinstance(text, str) or not text.strip():
        return LineStatus(state=LineSensorState.LOST, stale=True, blocked=True)

    payload: dict[str, Any] | None = None
    if "#diag#" in text:
        _, _, tail = text.partition("#diag#")
        try:
            loaded = json.loads(tail.strip())
            if isinstance(loaded, dict):
                payload = loaded
        except (ValueError, TypeError):
            payload = None
    if payload is None:
        payload = {}
        tokens = text.split("#diag#", 1)[0].split()
        for token in tokens:
            key, _, value = token.partition("=")
            if key and value:
                payload[key] = value

    state_name = payload.get("state")
    if not isinstance(state_name, str) or state_name not in _LINE_STATES_BY_NAME:
        return LineStatus(state=LineSensorState.LOST, stale=True, blocked=True)

    def _flag(key: str, *, default: bool) -> bool:
        value = payload.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes"}
        return bool(value)

    deviation = payload.get("dev")
    if isinstance(deviation, str):
        try:
            deviation = float(deviation)
        except ValueError:
            deviation = None
    if not isinstance(deviation, (int, float)) or not math.isfinite(float(deviation)):
        deviation = None

    limit = payload.get("line_limit_mps")
    try:
        limit = None if limit is None else float(limit)
    except (TypeError, ValueError):
        limit = None
    if limit is not None and not math.isfinite(limit):
        limit = None

    return LineStatus(
        state=_LINE_STATES_BY_NAME[state_name],
        stale=_flag("stale", default=True),
        blocked=_flag("blocked", default=True),
        deviation=None if deviation is None else float(deviation),
        turn_phase=str(payload.get("turn_phase") or ""),
        ramp_decision=str(payload.get("ramp_decision") or ""),
        line_limit_mps=limit,
    )


# ---------------------------------------------------------------------------
# 段 → 命令
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchDecision:
    """当前段该做什么：授权谁、要不要发目标、给机构发什么命令。"""

    segment_id: str
    active_source: str
    goal: Pose2D | None
    work_command: str | None
    is_work: bool
    reason: str

    @property
    def drives_chassis(self) -> bool:
        return self.active_source != SOURCE_NONE


def active_source_for(segment: RouteSegmentPlan | None) -> str:
    """本段授权谁驱动底盘；`None`（路线已结束）或未知类型 → `none`。"""
    if segment is None:
        return SOURCE_NONE
    return ROLE_ACTIVE_SOURCE.get(segment.role, SOURCE_NONE)


def goal_for(segment: RouteSegmentPlan) -> Pose2D | None:
    """需要 `/motion/goal` 的段：平移与原地转向。

    巡线段不发目标（由巡线控制器自己走）；作业段不发目标（视觉对准自己动）。
    原地转向段的起点与终点位置相同、朝向不同，所以位姿控制器就是「原地转」。
    """
    if segment.role in (SegmentRole.SHIFT, SegmentRole.TURN):
        return segment.to_pose
    return None


def work_sequence(cargo_plan: CargoPlan) -> tuple[CubeColor, ...]:
    """本次任务按顺序要处理的方块颜色（先橙后紫，与放置顺序一致）。"""
    return (CubeColor.ORANGE,) * cargo_plan.orange + (CubeColor.PURPLE,) * cargo_plan.purple


def work_command_for(
    segment: RouteSegmentPlan, cargo_plan: CargoPlan, work_index: int
) -> str | None:
    """第 `work_index`（从 0 起）次作业该发的命令；次数用尽返回 None。"""
    if segment.work is None:
        return None
    if work_index < 0:
        raise ValueError("work_index cannot be negative")
    sequence = work_sequence(cargo_plan)
    if work_index >= len(sequence):
        return None
    return WORK_COMMANDS[(segment.work, sequence[work_index])]


def decide(
    segment: RouteSegmentPlan | None, cargo_plan: CargoPlan, work_index: int = 0
) -> DispatchDecision:
    """本段的分发决策（mission_manager 每个段只调用一次）。"""
    if segment is None:
        return DispatchDecision(
            segment_id="", active_source=SOURCE_NONE, goal=None, work_command=None,
            is_work=False, reason="路线未在运行：释放底盘授权",
        )
    source = active_source_for(segment)
    is_work = segment.role is SegmentRole.WORK
    command = work_command_for(segment, cargo_plan, work_index) if is_work else None
    return DispatchDecision(
        segment_id=segment.id,
        active_source=source,
        goal=goal_for(segment),
        work_command=command,
        is_work=is_work,
        reason=f"{segment.id}（{segment.role.value}）授权 {source}",
    )


# ---------------------------------------------------------------------------
# B3：路口转弯命令（任务层 → 巡线节点）
# ---------------------------------------------------------------------------
#
# 转弯由**巡线节点**执行（它才有 /line_sensor 与 PD 纠偏状态），任务层只负责
# 「在哪一段、往哪转、要稳几拍」。两者之间用一条 JSON 消息交接：
# 任务层在进入 JUNCTION_TURN 段时下发一次，巡线节点在授权给它的时候执行。
#
# 为什么用 JSON 而不是新增消息类型：与 `/line_follow/status`（结构化 JSON）
# 同一套做法，避免了改消息哈希引发整包重建；解析失败一律返回 None（不猜）。


def turn_command_for(
    segment: RouteSegmentPlan | None, *, turn_rate_radps: float = 0.6
) -> dict[str, Any] | None:
    """本段的转弯命令（非转弯段返回 None）。

    参数来自 B1 的路线登记（方向、稳定拍数、期望路口签名）与 `junction_turn`
    的占位值（角速度/最短转向时间）——**同一份来源**，不另写一套。
    """
    from .junction_turn import TurnParams, turn_params_for_direction
    from .mission_route import ExitKind, TurnDirection

    if segment is None or segment.exit.kind is not ExitKind.JUNCTION_TURN:
        return None
    direction = segment.exit.turn
    if direction is None or direction is TurnDirection.STRAIGHT:
        return None
    params: TurnParams = turn_params_for_direction(
        direction,
        reacquire_samples=segment.exit.settle_samples,
        expect_states=tuple(segment.exit.expected_line_states),
        turn_rate_radps=turn_rate_radps,
        use_yaw_check=segment.exit.require_yaw,
    )
    return {
        "segment_id": segment.id,
        "ref": segment.exit.at_ref,
        "direction": params.direction.value,
        "expect_states": [state.value for state in params.expect_states],
        "junction_samples": params.junction_samples,
        "reacquire_samples": params.reacquire_samples,
        "turn_rate_radps": params.turn_rate_radps,
        "approach_speed_mps": params.approach_speed_mps,
        "min_turn_s": params.min_turn_s,
        "max_turn_s": params.max_turn_s,
        "approach_timeout_s": params.approach_timeout_s,
        "center_tolerance": params.center_tolerance,
        "use_yaw_check": params.use_yaw_check,
        "yaw_fraction": params.yaw_fraction,
        "around_sign": params.around_sign,
    }


def parse_turn_command(text: str):
    """把转弯命令 JSON 还原成 `TurnParams`；任何异常都返回 None（不猜）。"""
    from .junction_turn import TurnParams
    from .mission_route import TurnDirection

    if not isinstance(text, str) or not text.strip():
        return None
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        direction = TurnDirection(str(payload["direction"]))
        expect_states = tuple(LineSensorState(str(item)) for item in payload["expect_states"])
        return TurnParams(
            direction=direction,
            expect_states=expect_states,
            junction_samples=int(payload["junction_samples"]),
            turn_rate_radps=float(payload["turn_rate_radps"]),
            approach_speed_mps=float(payload["approach_speed_mps"]),
            min_turn_s=float(payload["min_turn_s"]),
            max_turn_s=float(payload["max_turn_s"]),
            approach_timeout_s=float(payload["approach_timeout_s"]),
            center_tolerance=float(payload["center_tolerance"]),
            reacquire_samples=int(payload["reacquire_samples"]),
            use_yaw_check=bool(payload["use_yaw_check"]),
            yaw_fraction=float(payload["yaw_fraction"]),
            around_sign=int(payload["around_sign"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# B3：巡线段参数（每段限速）+ 坡道段（C3：限速/加速度/打滑）
# ---------------------------------------------------------------------------
#
# 两个缺口一起补：
# 1. **每段限速此前没被采用**：路线登记表里写了 0.20/0.25/0.15，但巡线节点一直用
#    自己参数里的 `vx_base`，等于全路段一个速度。现在由任务层按段下发。
# 2. **坡道段没有限速/打滑保护**：坡上要更慢、下坡要防冲；`RampController`（C3）
#    早就写好了但零引用，现在由巡线节点在坡道段把 PD 输出穿过它再发布。
#
# 交接方式与转弯命令一致：JSON over `/mission/line`，空串 = 清除（非巡线段）。
#
# ⚠️ 诚实边界（写进代码而不是只写在文档里）：打滑检测用**轮速**里程计，
# 它看不到「轮子空转、车不走」这种真打滑（编码器量的是轮子转了多少）。
# 它能抓到的是「轮速跟不上命令」（动力不足/上不去），这在上坡是最常见的失败。
# 真要抓空转需要 IMU 俯仰/加速度或视觉，而 `imu_valid` 目前为 false。


@dataclass(frozen=True)
class LineCommand:
    """巡线段的参数：本段限速 +（坡道段才有）坡道 profile。"""

    segment_id: str
    max_speed_mps: float
    ramp_profile: RampProfile | None = None

    def __post_init__(self) -> None:
        if not self.segment_id:
            raise ValueError("line command requires segment_id")
        if not math.isfinite(self.max_speed_mps) or self.max_speed_mps <= 0.0:
            raise ValueError("line command max_speed_mps must be positive and finite")
        if self.ramp_profile is not None and not isinstance(self.ramp_profile, RampProfile):
            raise ValueError("ramp_profile must be a RampProfile or None")

    @property
    def is_ramp(self) -> bool:
        return self.ramp_profile is not None

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "segment_id": self.segment_id,
            "max_speed_mps": self.max_speed_mps,
            "ramp": None,
        }
        if self.ramp_profile is not None:
            profile = self.ramp_profile
            payload["ramp"] = {
                "kind": profile.kind.value,
                "max_speed_mps": profile.max_speed_mps,
                "max_accel_mps2": profile.max_accel_mps2,
                "slip_threshold_mps": profile.slip_threshold_mps,
                "slip_retreat_speed_mps": profile.slip_retreat_speed_mps,
                "slip_stop_after_s": profile.slip_stop_after_s,
                "descent_speed_factor": profile.descent_speed_factor,
                "descent_decel_mps2": profile.descent_decel_mps2,
            }
        return json.dumps(payload, ensure_ascii=False)


def line_command_for(segment: RouteSegmentPlan | None) -> LineCommand | None:
    """本段的巡线参数；非巡线/非坡道段返回 None（由调用方下发空串清除）。

    限速取路线登记表的 `max_speed_mps`（该值已被 B1 自检过 ≤ 底盘限幅）；
    坡道段再把 `RampProfile`（C3 占位值）附上。
    """
    if segment is None:
        return None
    if segment.role is SegmentRole.RAMP_UP:
        return LineCommand(
            segment_id=segment.id,
            max_speed_mps=segment.max_speed_mps,
            ramp_profile=RampProfile(
                kind=SegmentKind.RAMP_UP, max_speed_mps=segment.max_speed_mps
            ),
        )
    if segment.role is SegmentRole.RAMP_DOWN:
        return LineCommand(
            segment_id=segment.id,
            max_speed_mps=segment.max_speed_mps,
            ramp_profile=RampProfile(
                kind=SegmentKind.RAMP_DOWN, max_speed_mps=segment.max_speed_mps
            ),
        )
    if segment.role is SegmentRole.LINE:
        return LineCommand(segment_id=segment.id, max_speed_mps=segment.max_speed_mps)
    return None


def parse_line_command(text: str) -> LineCommand | None:
    """把巡线参数 JSON 还原成 `LineCommand`；任何异常都返回 None（不猜）。"""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        ramp = payload.get("ramp")
        profile = None
        if ramp is not None:
            if not isinstance(ramp, dict):
                return None
            profile = RampProfile(
                kind=SegmentKind(str(ramp["kind"])),
                max_speed_mps=float(ramp["max_speed_mps"]),
                max_accel_mps2=float(ramp["max_accel_mps2"]),
                slip_threshold_mps=float(ramp["slip_threshold_mps"]),
                slip_retreat_speed_mps=float(ramp["slip_retreat_speed_mps"]),
                slip_stop_after_s=float(ramp["slip_stop_after_s"]),
                descent_speed_factor=float(ramp["descent_speed_factor"]),
                descent_decel_mps2=float(ramp["descent_decel_mps2"]),
            )
        return LineCommand(
            segment_id=str(payload["segment_id"]),
            max_speed_mps=float(payload["max_speed_mps"]),
            ramp_profile=profile,
        )
    except (KeyError, TypeError, ValueError):
        return None


def effective_line_speed(command: LineCommand | None, node_limit_mps: float) -> float:
    """本段实际生效的巡线速度上限 = min(任务层本段限速, 节点自身上限)。

    取 min 的原因：任务层的限速来自路线登记表（≤ 底盘限幅），节点参数是现场调试
    时更保守的兜底值。两者都是「上限」，所以只能取更小的那个。
    """
    if not math.isfinite(node_limit_mps) or node_limit_mps <= 0.0:
        raise ValueError("node_limit_mps must be positive and finite")
    if command is None:
        return float(node_limit_mps)
    return float(min(command.max_speed_mps, node_limit_mps))


# ---------------------------------------------------------------------------
# 搭建 2 层对放置高度的要求（与 robot.yaml 比对）
# ---------------------------------------------------------------------------


def layer_counts(cargo_plan: CargoPlan) -> tuple[int, ...]:
    """把摆法字符串（如 `2+1`）解析成每层块数，并校验与总块数一致。"""
    parts = [part for part in str(cargo_plan.layout).split("+") if part.strip()]
    try:
        counts = tuple(int(part) for part in parts)
    except ValueError as exc:
        raise ValueError(f"无法解析摆法 {cargo_plan.layout!r}（应形如 2+1）") from exc
    if not counts or any(count <= 0 for count in counts):
        raise ValueError(f"摆法 {cargo_plan.layout!r} 每层块数必须为正")
    if len(counts) != cargo_plan.layers:
        raise ValueError(
            f"摆法 {cargo_plan.layout!r} 有 {len(counts)} 层，但计划写了 {cargo_plan.layers} 层"
        )
    total = cargo_plan.orange + cargo_plan.purple
    if sum(counts) != total:
        raise ValueError(f"摆法 {cargo_plan.layout!r} 合计 {sum(counts)} 块，与载货 {total} 块不符")
    return counts


def layer_height_m(layer_index: int, *, base_height_m: float, cube_size_m: float) -> float:
    """第 `layer_index`（从 0 起 = 第一层）的放置抬升高度。

    第一层放在搭建区台面上（台面高 `base_height_m`，规则 3.1.4 现场确认 100mm），
    之后每加一层再加一个方块高 `cube_size_m`（EVA 泡面方块 100mm，规则 3.1.1）。
    """
    if layer_index < 0:
        raise ValueError("layer_index cannot be negative")
    if base_height_m <= 0.0 or cube_size_m <= 0.0:
        raise ValueError("base_height_m and cube_size_m must be positive")
    return float(base_height_m) + layer_index * float(cube_size_m)


def required_placement_heights(
    cargo_plan: CargoPlan,
    *,
    base_height_m: float = 0.10,
    cube_size_m: float = 0.10,
) -> list[float]:
    """按摆法算出「每一次放置」应有的抬升高度序列（与配置逐项对齐的口径）。

    **口径必须和 `manipulator.select_place_height` 一致**：那条实现是
    `heights[min(placed_layers, len-1)]`，即 `place_heights_m` 是**按第几次放置**
    索引的序列，不是按层索引。所以这里返回的也是「第 k 次放置 → 第 k 个元素」。

    摆法 `2+1`（3 块 2 层）→ `[0.10, 0.10, 0.20]`；
    摆法 `1+1+1`（3 层各 1 块）→ `[0.10, 0.20, 0.30]`。
    """
    counts = layer_counts(cargo_plan)
    sequence: list[float] = []
    for layer_index, count in enumerate(counts):
        height = layer_height_m(
            layer_index, base_height_m=base_height_m, cube_size_m=cube_size_m
        )
        sequence.extend([height] * count)
    return sequence


def placement_heights_issue(
    cargo_plan: CargoPlan,
    configured_heights_m: Sequence[float],
    *,
    base_height_m: float = 0.10,
    cube_size_m: float = 0.10,
) -> str | None:
    """把「计划要的每次放置高度」与机器人配置逐项比对；不一致返回人话原因。

    这是**发现问题**而不是自动改配置：放置高度是机械动作参数，必须由人确认后改。
    """
    configured = [float(value) for value in configured_heights_m]
    try:
        required = required_placement_heights(
            cargo_plan, base_height_m=base_height_m, cube_size_m=cube_size_m
        )
    except ValueError as exc:
        return str(exc)
    if len(configured) < len(required):
        return (
            f"place_heights_m 只有 {len(configured)} 项，但计划要放置 {len(required)} 块"
            f"（需要 {required}）；缺项时 manipulator_client 会一直用最后一项高度"
        )
    mismatches = [
        f"第 {index + 1} 次放置：配置 {configured[index]:.2f} m，计划要求 {want:.2f} m"
        for index, want in enumerate(required)
        if abs(configured[index] - want) > 1e-9
    ]
    if not mismatches:
        return None
    return (
        f"搭建 {cargo_plan.layers} 层（摆法 {cargo_plan.layout}、共 {len(required)} 块）"
        f"要求 place_heights_m 前 {len(required)} 项为 {required}，当前配置为 {configured}；"
        + "；".join(mismatches)
        + f"。假设：搭建区台面 {base_height_m:.2f} m、方块 {cube_size_m:.2f} m。"
        "不改配置会搭成与计划不同的层数（过高会压到已放方块）。"
    )
