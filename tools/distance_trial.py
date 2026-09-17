"""网页直线定距试车：使用实测位姿，不能代替尺量验收。"""
from dataclasses import dataclass
import math


@dataclass
class DistanceTrial:
    start: tuple[float, float, float]
    distance: float
    speed: float
    started_at: float
    heartbeat_at: float
    token: str
    boot_id: int
    progress: float = 0.0
    lateral: float = 0.0
    last_progress: float = 0.0
    last_progress_at: float = 0.0

    def __post_init__(self):
        self.last_progress_at = self.started_at
        if not all(math.isfinite(v) for v in (*self.start, self.distance, self.speed)):
            raise ValueError("位姿、距离和速度必须是有限数值")
        if not 0.05 <= self.distance <= 1.0:
            raise ValueError("首次定距测试范围为 0.05～1.00 米")
        if not 0.02 <= self.speed <= 0.10:
            raise ValueError("定距速度范围为 0.02～0.10 米/秒")

    def step(self, pose, now):
        if not all(math.isfinite(v) for v in pose):
            raise ValueError("位姿数值无效")
        if now - self.heartbeat_at > 0.5:
            raise ValueError("网页心跳中断，已停车")
        if now - self.started_at > 2 * self.distance / self.speed + 5:
            raise ValueError("定距执行超时，已停车")
        x, y, yaw = pose
        x0, y0, yaw0 = self.start
        dx, dy = x - x0, y - y0
        self.progress = dx * math.cos(yaw0) + dy * math.sin(yaw0)
        self.lateral = -dx * math.sin(yaw0) + dy * math.cos(yaw0)
        error = math.atan2(math.sin(yaw0 - yaw), math.cos(yaw0 - yaw))
        if abs(self.lateral) > 0.10 or abs(error) > math.radians(30):
            raise ValueError("偏离直线超过 10 cm 或航向偏差超过 30°，已停车")
        if self.progress < -0.03:
            raise ValueError("反馈方向与前进指令相反，已停车")
        remaining = self.distance - self.progress
        if remaining <= 0.01:
            return (0.0, 0.0, 0.0), True
        if self.progress > self.last_progress + 0.002:
            self.last_progress = self.progress
            self.last_progress_at = now
        elif now - self.last_progress_at > 2.0:
            raise ValueError("连续 2 秒没有前进反馈，已停车")
        # 接近目标减速，起步用时间斜坡；保持起始朝向。
        vx = min(self.speed, max(0.02, remaining * 0.8), max(0.0, now - self.started_at) * 0.1)
        return (vx, 0.0, max(-0.3, min(0.3, error))), False
