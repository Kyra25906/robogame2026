from __future__ import annotations

import math
from dataclasses import dataclass

from .models import Pose2D, Velocity2D


def normalize_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


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

