"""失败降级阶梯（纯逻辑）：段失败重试耗尽之后怎么办。

## 为什么不能「一失败就停」

6 分钟的无人干预比赛里，一次抓取失败就原地停下等于放弃剩余时间；但「继续乱走」
更糟——可能撞进对方半场、压坏已搭的建筑、或者卡在坡道上。所以降级要**按位置与
朝向的可信度分层**：

| 失败的段 | 我们知道什么 | 降级动作 |
|---|---|---|
| 巡线 / 坡道 / 路口转向 | 位置或朝向**不可信**（线丢了 / 没转过去） | `SAFE_STOP`：停车、释放授权 |
| 车体微移（SHIFT） | 精细位置不可信（放置/抓取都要毫米级） | `SAFE_STOP` |
| 取块（PICK） | 位置可信（就在 W02），框里**已有 ≥1 块** | `SKIP_TO_BUILD`：跳过剩余取块，去搭建（已有块仍计分） |
| 取块（PICK） | 位置可信，但**一块都没抓到** | `RETREAT`：没什么可搭，退到撤退位（比堵在材料区好） |
| 放置（PLACE） | 位置可信（就在 W04），但放置失败 | `RETREAT`：退开，避免挡住搭建区 |

**这条阶梯只是策略**，不是规则要求；它只在「重试次数用尽」时生效，且永远不会让车
在没有位置依据的情况下继续运动。策略可关（`MissionConfig.degrade_on_failure`）。

## 与「异常处理」的关系

规则 3.2.3 的异常处理需要人工介入；本模块的目标恰恰是**避免走到那一步**：能自己
安全收场的，就自己收场（退开、停下），并把原因写清楚给人看。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .mission_route import RoutePlan, RouteSegmentPlan, SegmentRole, WorkKind
from .models import Cargo


class RecoveryAction(str, Enum):
    """降级动作。"""

    SKIP_TO_BUILD = "SKIP_TO_BUILD"  # 跳到搭建段（已有块仍然计分）
    RETREAT = "RETREAT"  # 跳到撤退段（安全收场）
    SAFE_STOP = "SAFE_STOP"  # 停车并释放授权（位置/朝向不可信）


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    target_segment_id: str | None
    reason: str


def _build_segment_id(plan: RoutePlan) -> str | None:
    for segment in plan.segments:
        if segment.role is SegmentRole.WORK and segment.work is WorkKind.PLACE:
            return segment.id
    return None


def _retreat_segment_id(plan: RoutePlan) -> str | None:
    """撤退段 = 路线最后一段（路线的收尾设计如此，见 mission_route 的 S13）。"""
    return plan.retreat_segment_id


def recovery_for_failure(
    plan: RoutePlan | None,
    segment: RouteSegmentPlan | None,
    cargo: Cargo,
) -> RecoveryDecision:
    """重试耗尽后该做什么。任何不确定情形都收敛到 `SAFE_STOP`。"""
    if segment is None:
        return RecoveryDecision(
            RecoveryAction.SAFE_STOP, None,
            "当前段未知：无法判断位置是否可信，停车并释放授权",
        )
    if plan is None:
        return RecoveryDecision(
            RecoveryAction.SAFE_STOP, None, "没有路线信息：停车并释放授权"
        )

    if segment.role in (
        SegmentRole.LINE, SegmentRole.RAMP_UP, SegmentRole.RAMP_DOWN, SegmentRole.TURN,
        SegmentRole.SHIFT,
    ):
        return RecoveryDecision(
            RecoveryAction.SAFE_STOP, None,
            f"{segment.id}（{segment.role.value}）失败：位置或朝向不可信，"
            "不许在无依据的情况下继续运动",
        )

    if segment.work is WorkKind.PICK:
        build = _build_segment_id(plan)
        if cargo.total >= 1 and build is not None:
            return RecoveryDecision(
                RecoveryAction.SKIP_TO_BUILD, build,
                f"取块失败但框里已有 {cargo.total} 块：跳过剩余取块直接去搭建"
                "（已抓到的块仍然计分）",
            )
        retreat = _retreat_segment_id(plan)
        return RecoveryDecision(
            RecoveryAction.RETREAT, retreat,
            "取块失败且框里没有块：没什么可搭，退到撤退位安全收场",
        )

    if segment.work is WorkKind.PLACE:
        retreat = _retreat_segment_id(plan)
        return RecoveryDecision(
            RecoveryAction.RETREAT, retreat,
            "放置失败：退到撤退位，避免继续挡在搭建区（已放置的块仍然计分）",
        )

    return RecoveryDecision(
        RecoveryAction.SAFE_STOP, None,
        f"{segment.id}：没有为该类型定义降级动作，收敛到安全停车",
    )
