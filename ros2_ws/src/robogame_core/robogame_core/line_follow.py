from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


# 8 路传感器的归一化位置（-1=最左，+1=最右）
SENSOR_POSITIONS = tuple((i - 3.5) / 3.5 for i in range(8))


class LineSensorState(Enum):
    """巡线传感器状态。"""
    ON_LINE = "ON_LINE"
    LEFT_EDGE = "LEFT_EDGE"
    RIGHT_EDGE = "RIGHT_EDGE"
    LOST = "LOST"
    INTERSECTION = "INTERSECTION"
    ALL_BLACK = "ALL_BLACK"


@dataclass
class LineSegment:
    """场地黑线段。"""
    start_x: float
    start_y: float
    start_theta: float
    end_x: float
    end_y: float
    end_theta: float
    line_width: float


@dataclass
class LineSensorReading:
    """八路灰度传感器读数。"""
    channels: list[bool]
    raw_values: list[float] | None = None

    def __post_init__(self) -> None:
        if len(self.channels) != 8:
            raise ValueError(f"Expected 8 channels, got {len(self.channels)}")

    @property
    def active_count(self) -> int:
        return sum(1 for c in self.channels if c)

    @property
    def is_all_active(self) -> bool:
        return all(self.channels)

    @property
    def is_none_active(self) -> bool:
        return not any(self.channels)


def compute_deviation(channels: list[bool], method: str = "centroid") -> float:
    """计算归一化横向偏差。

    约定：偏差 > 0 表示线在右侧，偏差 < 0 表示线在左侧。

    Returns:
        -1.0 .. +1.0，nan 表示无法计算（无激活或全激活）
    """
    if len(channels) != 8:
        raise ValueError(f"Expected 8 channels, got {len(channels)}")

    active_count = sum(1 for c in channels if c)

    if active_count == 0 or active_count == 8:
        return float('nan')

    total = sum(SENSOR_POSITIONS[i] for i in range(8) if channels[i])
    return total / active_count


def compute_deviation_weighted(raw_values: list[float], threshold: float = 0.5) -> float:
    """使用原始灰度值计算加权平均偏差。

    Args:
        raw_values: 8 路归一化值 0..1（1=黑线）
        threshold: 二值化阈值，低于此值权重为 0

    Returns:
        归一化偏差 -1..1，nan 表示无法计算
    """
    if len(raw_values) != 8:
        raise ValueError(f"Expected 8 values, got {len(raw_values)}")

    weighted_sum = 0.0
    total_weight = 0.0

    for i in range(8):
        w = max(0.0, raw_values[i] - threshold)
        weighted_sum += SENSOR_POSITIONS[i] * w
        total_weight += w

    if total_weight < 1e-9:
        return float('nan')

    return weighted_sum / total_weight


def update_sensor_state(
    channels: list[bool],
    prev_state: LineSensorState,
    lost_count: int,
    lost_threshold: int = 5,
    intersection_threshold: int = 6,
    edge_threshold: float = 0.3,
) -> tuple[LineSensorState, int]:
    """更新传感器状态机。

    Returns:
        (new_state, new_lost_count)
    """
    if len(channels) != 8:
        raise ValueError(f"Expected 8 channels, got {len(channels)}")

    active_count = sum(1 for c in channels if c)

    if active_count == 0:
        new_lost_count = lost_count + 1
        if new_lost_count >= lost_threshold:
            return (LineSensorState.LOST, new_lost_count)
        return (prev_state, new_lost_count)

    new_lost_count = 0

    if active_count == 8:
        return (LineSensorState.ALL_BLACK, new_lost_count)

    if active_count >= intersection_threshold:
        return (LineSensorState.INTERSECTION, new_lost_count)

    deviation = compute_deviation(channels)

    if math.isnan(deviation):
        return (LineSensorState.LOST, new_lost_count)

    if deviation < -edge_threshold:
        return (LineSensorState.LEFT_EDGE, new_lost_count)
    elif deviation > edge_threshold:
        return (LineSensorState.RIGHT_EDGE, new_lost_count)
    else:
        return (LineSensorState.ON_LINE, new_lost_count)


def compute_correction(
    deviation: float,
    kp: float,
    kd: float,
    prev_deviation: float,
    dt: float,
    vx_base: float = 0.2,
) -> tuple[float, float]:
    """PD 纠偏控制器。

    输出机体坐标系的纠偏量，不直接发布 cmd_vel。

    Returns:
        (vx, wz)
        - vx: 前进速度 m/s
        - wz: 角速度 rad/s，正=左转，负=右转
    """
    if math.isnan(deviation):
        return (0.0, 0.0)

    derivative = (deviation - prev_deviation) / dt if dt > 0 else 0.0

    wz = -(kp * deviation + kd * derivative)
    vx = vx_base

    return (vx, wz)

