"""巡线运行器（纯逻辑，零 ROS 依赖）。

输入 8 路归一化灰度读数（0..1，1=黑线），输出传感器状态 / 横向偏差 /
纠偏命令。`robogame_core.line_follow.py` 提供无状态纯函数，本模块把它们
组合成**带状态**的运行器（prev_state / lost_count / prev_deviation），
方便节点层（line_follow_node.py）直接调用。

设计说明（供下轮 B「收编进 motion_control 路段模式」复用）：
- 本类是 B 阶段 motion_controller 内部巡线模式直接调用的同一套逻辑；
- 输出只含机体系纠偏量，不发布、不判断场地/路段——选路与路段决策属上层；
- 交叉口/全黑默认「直行通过」，B 阶段由路段层决策覆盖（选哪条支线）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from robogame_core.line_follow import (
    LineSensorReading,
    LineSensorState,
    compute_correction,
    compute_deviation,
    compute_deviation_weighted,
    update_sensor_state,
)


@dataclass(frozen=True)
class LineFollowParams:
    """巡线参数（与 `line_follow.py` 参数表一致，另加失联判据）。"""

    kp: float = 1.0
    kd: float = 0.1
    vx_base: float = 0.2
    threshold: float = 0.5
    edge_threshold: float = 0.3
    lost_threshold: int = 5
    intersection_threshold: int = 6
    dt_s: float = 0.02
    # 传感器读数超过此间隔未更新 → 视为失联，输出停车（安全）
    max_reading_gap_s: float = 0.25

    def __post_init__(self) -> None:
        if self.kp < 0.0 or self.kd < 0.0:
            raise ValueError("kp/kd must be non-negative")
        if self.vx_base < 0.0:
            raise ValueError("vx_base must be non-negative")
        if not 0.0 < self.threshold < 1.0:
            raise ValueError("threshold must be in (0, 1)")
        if self.edge_threshold <= 0.0:
            raise ValueError("edge_threshold must be positive")
        if self.lost_threshold < 1 or self.intersection_threshold < 2:
            raise ValueError("lost/intersection thresholds out of range")
        if self.dt_s <= 0.0 or self.max_reading_gap_s <= 0.0:
            raise ValueError("dt_s/max_reading_gap_s must be positive")


@dataclass(frozen=True)
class LineFollowOutput:
    """一次 update 的输出。deviation 为 nan 表示无法计算方向。"""

    state: LineSensorState
    deviation: float
    vx: float
    wz: float
    lost_count: int
    reading_stale: bool


class LineFollowRunner:
    """持有巡线状态机与 PD 历史的运行器。

    用法::

        runner = LineFollowRunner(LineFollowParams())
        out = runner.update(
            LineSensorReading(channels=[...], raw_values=[...]), now=1.0,
        )

    约定：
    - `now` 为单调时钟秒；`dt` 取相邻两次读数时间差，缺失时用 `dt_s`；
    - 读数间隔超过 `max_reading_gap_s` → 输出 LOST + 零速（不更新算法状态）；
    - LOST → 零速停车；INTERSECTION/ALL_BLACK → 直行通过；其余 → PD 纠偏。
    """

    def __init__(self, params: LineFollowParams) -> None:
        self.params = params
        self.prev_state = LineSensorState.LOST
        self.lost_count = 0
        self.prev_deviation = 0.0
        self.last_reading_time: float | None = None

    def reset(self) -> None:
        """清空状态机与 PD 历史（路段切换/任务重试时调用）。"""
        self.prev_state = LineSensorState.LOST
        self.lost_count = 0
        self.prev_deviation = 0.0
        self.last_reading_time = None

    def update(
        self, reading: LineSensorReading, now: float
    ) -> LineFollowOutput:
        if not math.isfinite(now):
            raise ValueError("now must be finite")
        if len(reading.channels) != 8:
            raise ValueError(f"Expected 8 channels, got {len(reading.channels)}")
        raw = reading.raw_values
        if raw is not None:
            if len(raw) != 8:
                raise ValueError(f"Expected 8 raw values, got {len(raw)}")
            if any(not math.isfinite(v) for v in raw):
                raise ValueError("raw_values must be finite")

        # 失联判定：间隔超限 → 停车（算法状态保持，恢复后从旧状态继续）
        if self.last_reading_time is not None:
            dt = now - self.last_reading_time
            if dt > self.params.max_reading_gap_s:
                self.last_reading_time = now
                return LineFollowOutput(
                    state=LineSensorState.LOST,
                    deviation=float("nan"),
                    vx=0.0,
                    wz=0.0,
                    lost_count=self.lost_count,
                    reading_stale=True,
                )
            if dt <= 0.0:
                dt = self.params.dt_s  # 同刻/时钟回绕：用标称周期
        else:
            dt = self.params.dt_s
        self.last_reading_time = now

        # 二值化（状态机吃 bool；加权偏差吃原始值）
        if raw is None:
            channels = list(reading.channels)
            deviation = compute_deviation(channels)
        else:
            channels = [v >= self.params.threshold for v in raw]
            deviation = compute_deviation_weighted(raw, self.params.threshold)

        state, lost_count = update_sensor_state(
            channels,
            self.prev_state,
            self.lost_count,
            lost_threshold=self.params.lost_threshold,
            intersection_threshold=self.params.intersection_threshold,
            edge_threshold=self.params.edge_threshold,
        )
        self.prev_state = state
        self.lost_count = lost_count

        if state == LineSensorState.LOST:
            vx, wz = 0.0, 0.0
        elif state in (LineSensorState.INTERSECTION, LineSensorState.ALL_BLACK):
            # 交叉口/全黑：默认直行通过；路段层（B 阶段）接管选路后覆盖。
            vx, wz = self.params.vx_base, 0.0
        else:
            vx, wz = compute_correction(
                deviation,
                self.params.kp,
                self.params.kd,
                self.prev_deviation,
                dt,
                self.params.vx_base,
            )

        if not math.isnan(deviation):
            self.prev_deviation = deviation

        return LineFollowOutput(
            state=state,
            deviation=deviation,
            vx=vx,
            wz=wz,
            lost_count=lost_count,
            reading_stale=False,
        )
