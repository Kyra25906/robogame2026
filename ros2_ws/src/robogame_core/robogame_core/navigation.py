from __future__ import annotations

import math
from dataclasses import dataclass

from .models import Pose2D, Velocity2D


def normalize_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def pose_is_finite(pose: Pose2D) -> bool:
    """Return False before invalid numeric data can reach a controller."""
    return all(math.isfinite(value) for value in (pose.x, pose.y, pose.yaw))


def pose_inside_bounds(
    pose: Pose2D, *, min_x: float, max_x: float, min_y: float, max_y: float
) -> bool:
    """Return whether a pose lies inside the configured field bounds."""
    return (
        min_x <= pose.x <= max_x
        and min_y <= pose.y <= max_y
        and all(math.isfinite(v) for v in (pose.x, pose.y))
    )


def pose_in_own_half(
    pose: Pose2D,
    *,
    own_half_x_max: float,
    min_x: float,
    min_y: float,
    max_y: float,
) -> bool:
    """C6（P2-1）: 目标是否在本方半场。

    规则 3.2.1 S4：越线要执行异常处理、情节恶劣可罚下。本方半场 =
    x <= own_half_x_max（假设对方半场在 +x 侧），同时不越场地整体边界。
    越界返回 False，调用方应拒绝目标并报告（异常处理）。
    """
    return (
        pose.x <= own_half_x_max
        and pose_inside_bounds(pose, min_x=min_x, max_x=own_half_x_max,
                               min_y=min_y, max_y=max_y)
    )


def move_toward(current: float, target: float, max_delta: float) -> float:
    """Move one scalar toward a target without changing faster than max_delta."""
    if max_delta < 0.0:
        raise ValueError("max_delta cannot be negative")
    return current + clamp(target - current, max_delta)


def limit_velocity_rate(
    previous: Velocity2D,
    target: Velocity2D,
    dt: float,
    max_ax: float,
    max_ay: float,
    max_awz: float,
) -> Velocity2D:
    """Apply independent acceleration limits to a body-frame velocity command."""
    if max_ax <= 0.0 or max_ay <= 0.0 or max_awz <= 0.0:
        raise ValueError("velocity rate limits must be positive")
    if dt <= 0.0:
        return previous
    return Velocity2D(
        move_toward(previous.vx, target.vx, max_ax * dt),
        move_toward(previous.vy, target.vy, max_ay * dt),
        move_toward(previous.wz, target.wz, max_awz * dt),
    )


@dataclass(frozen=True)
class ControllerConfig:
    kx: float = 1.2
    ky: float = 1.2
    kyaw: float = 1.8
    max_vx: float = 0.6
    max_vy: float = 0.5
    max_wz: float = 1.2
    position_tolerance: float = 0.05
    yaw_tolerance: float = math.radians(5.0)
    slow_radius: float = 0.35

    def __post_init__(self) -> None:
        values = (
            self.kx, self.ky, self.kyaw,
            self.max_vx, self.max_vy, self.max_wz,
            self.position_tolerance, self.yaw_tolerance, self.slow_radius,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("controller settings must be finite")
        if min(self.kx, self.ky, self.kyaw) < 0.0:
            raise ValueError("controller gains cannot be negative")
        if min(
            self.max_vx, self.max_vy, self.max_wz,
            self.position_tolerance, self.yaw_tolerance, self.slow_radius,
        ) <= 0.0:
            raise ValueError("controller limits and tolerances must be positive")


class GoToPoseController:
    def __init__(self, config: ControllerConfig | None = None) -> None:
        self.config = config or ControllerConfig()

    def errors(self, pose: Pose2D, target: Pose2D) -> tuple[float, float, float]:
        dx = target.x - pose.x
        dy = target.y - pose.y
        cos_yaw = math.cos(pose.yaw)
        sin_yaw = math.sin(pose.yaw)
        ex = cos_yaw * dx + sin_yaw * dy
        ey = -sin_yaw * dx + cos_yaw * dy
        return ex, ey, normalize_angle(target.yaw - pose.yaw)

    def at_goal(self, pose: Pose2D, target: Pose2D) -> bool:
        ex, ey, eyaw = self.errors(pose, target)
        return math.hypot(ex, ey) <= self.config.position_tolerance and abs(eyaw) <= self.config.yaw_tolerance

    def command(self, pose: Pose2D, target: Pose2D) -> Velocity2D:
        ex, ey, eyaw = self.errors(pose, target)
        distance = math.hypot(ex, ey)
        if self.at_goal(pose, target):
            return Velocity2D(0.0, 0.0, 0.0)
        scale = min(1.0, max(0.2, distance / self.config.slow_radius))
        return Velocity2D(
            clamp(self.config.kx * ex, self.config.max_vx * scale),
            clamp(self.config.ky * ey, self.config.max_vy * scale),
            clamp(self.config.kyaw * eyaw, self.config.max_wz),
        )


@dataclass
class OdometryIntegrator:
    pose: Pose2D = Pose2D(0.0, 0.0, 0.0)
    imu_yaw_weight: float = 0.15

    def update(self, body_velocity: Velocity2D, dt: float, imu_wz: float | None = None) -> Pose2D:
        if dt <= 0.0 or dt > 0.5:
            return self.pose
        wz = body_velocity.wz if imu_wz is None else (
            (1.0 - self.imu_yaw_weight) * body_velocity.wz + self.imu_yaw_weight * imu_wz
        )
        mid_yaw = self.pose.yaw + 0.5 * wz * dt
        dx = (math.cos(mid_yaw) * body_velocity.vx - math.sin(mid_yaw) * body_velocity.vy) * dt
        dy = (math.sin(mid_yaw) * body_velocity.vx + math.cos(mid_yaw) * body_velocity.vy) * dt
        self.pose = Pose2D(self.pose.x + dx, self.pose.y + dy, normalize_angle(self.pose.yaw + wz * dt))
        return self.pose
