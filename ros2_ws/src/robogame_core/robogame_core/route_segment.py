"""C1: 路段链模型（RouteChain）——把「一跳到路点」拆成「路段序列」。

背景（执行队列 2026-08-18 C1）：任务状态机的 ``GO_TO_*`` 目前只发一个终点
``/motion/goal``，motion_control 直线飞过去。真实场地有黑线（巡线）、斜坡/
高台（RAMP）、多段路径——一段路可能需要先巡线、再上坡、再直行。没有路段
模型，C2（标签矫正）/C3（斜坡）/C4（巡线）都接不进任务流。

本模块是纯算法（零 ROS、零硬件），提供：
- ``SegmentKind``：路段类型（WAYPOINT / LINE_FOLLOW / RAMP_UP / RAMP_DOWN）
- ``RouteSegment``：单段——进入条件、控制模式、限速、退出条件
- ``RouteChain``：路段序列的推进与仲裁

C2/C3/C4 将各自实现段类型的控制逻辑（GoToPoseController / 巡线 / 坡道
限速），通过 ``RouteChain`` 统一调度。与现有 ``GoToPoseController`` 兼容
（它是 WAYPOINT 段的控制模式，RouteChain 不替换它）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SegmentKind(str, Enum):
    """路段类型。控制模式与限速随类型变化。"""

    WAYPOINT = "WAYPOINT"  # 常规路点：GoToPoseController
    LINE_FOLLOW = "LINE_FOLLOW"  # 巡线（C4 接入）
    RAMP_UP = "RAMP_UP"  # 上坡（C3 接入：限速 + 打滑检测）
    RAMP_DOWN = "RAMP_DOWN"  # 下坡（C3 接入：限速 + 防冲）


class ChainStatus(str, Enum):
    """路段链整体状态。"""

    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RouteSegment:
    """一条路段。

    kind: 路段类型（决定控制模式与限速语义）
    max_speed_mps: 本段最大线速度（0.0 = 沿用链默认，不覆盖）
    max_yaw_radps: 本段最大角速度（0.0 = 沿用链默认）
    entry_check: 进入条件（可调用对象，返回 True 才允许进入本段；
        None = 无前置条件，到段即进）
    exit_check: 退出条件（可调用对象，返回 True 表示本段完成；
        None = 不支持自动退出，由上层显式 advance）
    label: 可读名称（日志/排障）
    """

    kind: SegmentKind = SegmentKind.WAYPOINT
    max_speed_mps: float = 0.0
    max_yaw_radps: float = 0.0
    entry_check: object | None = None  # () -> bool
    exit_check: object | None = None  # () -> bool
    label: str = ""

    def __post_init__(self) -> None:
        if self.max_speed_mps < 0.0 or self.max_yaw_radps < 0.0:
            raise ValueError("segment speed limits cannot be negative")
        if self.entry_check is not None and not callable(self.entry_check):
            raise ValueError("entry_check must be callable or None")
        if self.exit_check is not None and not callable(self.exit_check):
            raise ValueError("exit_check must be callable or None")


@dataclass
class RouteChain:
    """路段序列的推进与仲裁。

    用法：
        chain = RouteChain([
            RouteSegment(SegmentKind.WAYPOINT, label="到材料区"),
            RouteSegment(SegmentKind.RAMP_UP, max_speed_mps=0.3, label="上坡"),
            RouteSegment(SegmentKind.WAYPOINT, label="到搭建区"),
        ])
        chain.start()
        while not chain.is_complete:
            segment = chain.current          # 当前段（None 若未开始/已完成）
            speed = chain.current_speed      # 本段生效限速
            if chain.tick():                 # 调用一次；返回 True = 发生段切换
                ...
            # 异常时 chain.degrade() 降级 / chain.fail(...)
    """

    segments: list[RouteSegment] = field(default_factory=list)
    default_speed_mps: float = 0.6
    default_yaw_radps: float = 1.2
    status: ChainStatus = ChainStatus.NOT_STARTED
    current_index: int = -1
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError("route chain requires at least one segment")
        if self.default_speed_mps <= 0.0 or self.default_yaw_radps <= 0.0:
            raise ValueError("chain default speed limits must be positive")

    @property
    def is_complete(self) -> bool:
        return self.status is ChainStatus.COMPLETE

    @property
    def failed(self) -> bool:
        return self.status is ChainStatus.FAILED

    @property
    def current(self) -> RouteSegment | None:
        if self.status is not ChainStatus.RUNNING:
            return None
        return self.segments[self.current_index]

    @property
    def current_speed(self) -> float:
        """当前段生效限速（段未覆盖则用链默认）。"""
        segment = self.current
        if segment is None:
            return self.default_speed_mps
        return segment.max_speed_mps if segment.max_speed_mps > 0.0 else self.default_speed_mps

    @property
    def current_yaw_limit(self) -> float:
        segment = self.current
        if segment is None:
            return self.default_yaw_radps
        return segment.max_yaw_radps if segment.max_yaw_radps > 0.0 else self.default_yaw_radps

    def start(self) -> None:
        """从第一段开始。必须先于任何 tick。"""
        if self.status is not ChainStatus.NOT_STARTED:
            raise ValueError("route chain can only be started once")
        self.current_index = 0
        self.status = ChainStatus.RUNNING
        self.detail = f"entered segment 0: {self._label(0)}"

    def tick(self) -> bool:
        """推进一次：检查当前段退出条件，满足则切到下一段。

        返回 True 表示发生段切换（调用方应刷新命令来源）。
        链全部走完时置 COMPLETE。异常请用 fail()/degrade() 而非 tick 吞掉。
        """
        if self.status is not ChainStatus.RUNNING:
            return False
        segment = self.segments[self.current_index]
        if segment.entry_check is not None and not segment.entry_check():
            # 进入条件未满足：停留在当前段之前（不推进）。
            return False
        if segment.exit_check is not None and not segment.exit_check():
            return False
        if self.current_index + 1 < len(self.segments):
            self.current_index += 1
            self.detail = (
                f"entered segment {self.current_index}: {self._label(self.current_index)}"
            )
            return True
        self.status = ChainStatus.COMPLETE
        self.detail = "route chain complete"
        return True

    def degrade(self, reason: str) -> None:
        """降级：放弃后续段，把当前段当作 WAYPOINT 处理（调用方决定）。

        用于巡线 LOST、坡道打滑等异常——链不判死，由上层降级策略接管
        （对应 G3.2/G3.4 的降级路径）。此处只记录状态，不改变段定义。
        """
        if self.status is not ChainStatus.RUNNING:
            return
        self.detail = f"degraded: {reason} (at segment {self.current_index})"

    def fail(self, result_detail: str) -> None:
        """链级失败：整条路线不可继续（如定位丢失）。"""
        self.status = ChainStatus.FAILED
        self.detail = f"route chain failed: {result_detail}"

    def reset(self) -> None:
        """复位到未开始（用于新一轮任务循环，G1.2 多轮隔离）。"""
        self.status = ChainStatus.NOT_STARTED
        self.current_index = -1
        self.detail = ""

    def _label(self, index: int) -> str:
        segment = self.segments[index]
        return segment.label or f"{segment.kind.value}[{index}]"
