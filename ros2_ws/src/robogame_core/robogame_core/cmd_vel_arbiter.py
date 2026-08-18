"""C6（P2-2）: /cmd_vel 多来源仲裁（纯算法，零 ROS 依赖）。

背景（执行队列 2026-08-18 C6 / P2-2）：/cmd_vel 有多个发布者——
motion_control（导航控制器）、manipulator_client（视觉对准）、
mission_manager（急停/终态零速）。当前靠时序隐式互斥，谁后发谁赢，
没有仲裁：导航还在走，对准突然发横向速度，两股命令打架。

方案（执行队列建议）：「明确职责时段 + 急停最高优先级不变」。本模块提供
集中仲裁纯函数：给定「当前授权者 + 各来源最新命令 + 急停状态」，仲裁输出
唯一 /cmd_vel。授权者由 mission_manager 按任务状态广播（后续接入）。

规则（优先级从高到低）：
1. 急停（emergency_stop=true）→ 永远输出零速（最高优先级，不绕过）；
2. 来源等于当前授权者 → 采用该命令；
3. 其他来源 → 忽略（非授权者不得发运动命令）；
4. 授权者命令过期（超过 stale_s 未更新）→ 输出零速（防悬挂）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import Velocity2D

# 运动命令来源标识（与节点名对应）
SOURCE_NAVIGATE = "motion_control"
SOURCE_ALIGN = "manipulator_client"
SOURCE_MISSION = "mission_manager"


@dataclass(frozen=True)
class ArbiterConfig:
    """仲裁器参数。"""

    # 合法来源集合（防止未知来源被误采）
    allowed_sources: frozenset[str] = frozenset(
        {SOURCE_NAVIGATE, SOURCE_ALIGN, SOURCE_MISSION}
    )
    # 授权者命令超过此秒数未更新 → 视为过期，输出零速
    stale_s: float = 0.5

    def __post_init__(self) -> None:
        if not math.isfinite(self.stale_s) or self.stale_s <= 0.0:
            raise ValueError("stale_s must be positive and finite")


@dataclass
class CmdVelArbiter:
    """维护各来源最新命令并仲裁。

    用法：
        arbiter = CmdVelArbiter()
        arbiter.update(SOURCE_NAVIGATE, Velocity2D(0.3, 0, 0), now=1.0)
        cmd = arbiter.output(active_source=SOURCE_NAVIGATE, now=1.05,
                             emergency_stop=False)
        # cmd == Velocity2D(0.3, 0, 0)
    """

    config: ArbiterConfig = field(default_factory=ArbiterConfig)
    # source -> (Velocity2D, timestamp)
    latest: dict[str, tuple[Velocity2D, float]] = field(default_factory=dict)

    def update(
        self, source: str, command: Velocity2D, *, now: float
    ) -> None:
        """记录某来源的最新命令。非允许来源抛 ValueError。"""
        if source not in self.config.allowed_sources:
            raise ValueError(f"unknown cmd_vel source: {source}")
        if not math.isfinite(now):
            raise ValueError("now must be finite")
        self.latest[source] = (command, now)

    def output(
        self, *, active_source: str, now: float, emergency_stop: bool
    ) -> Velocity2D:
        """仲裁输出唯一 /cmd_vel。

        急停最高优先级；非授权者忽略；授权者过期输出零速。
        """
        if emergency_stop:
            return Velocity2D(0.0, 0.0, 0.0)
        if active_source not in self.config.allowed_sources:
            raise ValueError(f"unknown active source: {active_source}")
        entry = self.latest.get(active_source)
        if entry is None:
            return Velocity2D(0.0, 0.0, 0.0)
        command, timestamp = entry
        if now - timestamp > self.config.stale_s:
            return Velocity2D(0.0, 0.0, 0.0)
        return command


def arbitrate_cmd_vel(
    *,
    active_source: str,
    sources: dict[str, tuple[Velocity2D, float]],
    now: float,
    emergency_stop: bool = False,
    stale_s: float = 0.5,
) -> Velocity2D:
    """纯函数形式：不用实例化，直接仲裁。

    与 CmdVelArbiter.output 等价，方便无状态测试。
    """
    arbiter = CmdVelArbiter(ArbiterConfig(stale_s=stale_s))
    for source, (command, timestamp) in sources.items():
        arbiter.update(source, command, now=timestamp)
    return arbiter.output(
        active_source=active_source, now=now, emergency_stop=emergency_stop
    )
