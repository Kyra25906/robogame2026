"""C3: 斜坡/高台段控制（RAMP_UP / RAMP_DOWN）——限速、打滑检测、下坡防冲。

背景（执行队列 2026-08-18 C3）：材料区在 200mm 高台 + 1.8x0.8m 的 14° 斜坡
上（规则 3.1.5/3.1.6/3.1.7）。上坡可能打滑（轮子空转、里程计误报前进）、
车头翘起导致视距失真；下坡重力加速可能冲过头停不住。

本模块是纯算法（零 ROS、零硬件，单测用合成数据），提供：
- ``RampProfile``：坡道段参数（坡度、限速、限加速度、打滑阈值）
- ``RampController``：坡道段控制器——
    * 输出受坡道限速与加速度限制（比平地段更保守）
    * 打滑检测：期望速度 vs 实际速度差超阈值 → 降速/停车（乙2 降级，
      不等真实 IMU，用里程计 vs 期望速度）
    * 下坡防冲：下坡段进一步限制加速度与最大速度
- 与 C1 的 ``RouteSegment(kind=RAMP_UP/RAMP_DOWN)`` 联动：控制器从段
  配置读取限速，``current_speed`` 作为速度上限。

现场依赖（非本模块）：真实 IMU pitch（当前 imu_valid=false，电控欠账）、
坡度/阈值整定。IMU 可用后的视距补偿（距离公式加俯仰项）为后续项。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .models import Velocity2D
from .navigation import clamp, limit_velocity_rate
from .route_segment import SegmentKind


@dataclass(frozen=True)
class RampProfile:
    """坡道段参数。默认值是保守首值，现场整定后替换。"""

    kind: SegmentKind = SegmentKind.RAMP_UP
    max_speed_mps: float = 0.25  # 坡道限速（平地段默认 0.6，坡上更保守）
    max_accel_mps2: float = 0.30  # 坡道加速度上限（平地段 0.8，坡上更小）
    # 打滑检测：期望速度与实际速度（里程计）差超过此值视为打滑
    slip_threshold_mps: float = 0.08
    # 打滑后先降速到多少、持续多久判定为「卡住」需停车
    slip_retreat_speed_mps: float = 0.05
    slip_stop_after_s: float = 1.5
    # 下坡防冲：下坡段额外收紧限速与减速度
    descent_speed_factor: float = 0.7  # 下坡限速 = max_speed * factor
    descent_decel_mps2: float = 0.25  # 下坡段最大减速度（防冲）

    def __post_init__(self) -> None:
        if self.kind not in {SegmentKind.RAMP_UP, SegmentKind.RAMP_DOWN}:
            raise ValueError("RampProfile kind must be RAMP_UP or RAMP_DOWN")
        values = (
            self.max_speed_mps,
            self.max_accel_mps2,
            self.slip_threshold_mps,
            self.slip_retreat_speed_mps,
            self.slip_stop_after_s,
            self.descent_speed_factor,
            self.descent_decel_mps2,
        )
        if not all(math.isfinite(v) for v in values):
            raise ValueError("ramp profile values must be finite")
        if (
            self.max_speed_mps <= 0.0
            or self.max_accel_mps2 <= 0.0
            or self.slip_threshold_mps <= 0.0
            or self.slip_retreat_speed_mps <= 0.0
            or self.slip_stop_after_s <= 0.0
            or self.descent_speed_factor <= 0.0
            or self.descent_decel_mps2 <= 0.0
        ):
            raise ValueError("ramp profile limits must be positive")
        if self.slip_retreat_speed_mps >= self.max_speed_mps:
            raise ValueError("slip retreat speed must be below ramp max speed")


class SlipDecision(str, Enum):
    """打滑检测输出。"""

    NORMAL = "NORMAL"  # 正常行驶
    SLIPPING = "SLIPPING"  # 打滑中：降速
    STUCK = "STUCK"  # 打滑超时：停车（卡住，需重试/换角度）


@dataclass
class RampController:
    """坡道段控制器。

    用法（接 C1 的 RouteChain）：
        controller = RampController(RampProfile(kind=SegmentKind.RAMP_UP))
        controller.begin(now=0.0)
        command, decision = controller.step(
            now=now,
            desired=desired_velocity,   # 上层（GoToPose）期望速度
            measured_speed=wheel_speed,  # 里程计实测速度（打滑检测用）
            dt=dt,
        )
    """

    profile: RampProfile = RampProfile()
    current_speed: float = 0.0
    slip_since_s: float | None = None  # 连续打滑开始时刻
    last_step_s: float | None = None

    def begin(self, now: float) -> None:
        """进入坡道段时调用：速度从 0 起步（防上坡猛冲）。"""
        self.current_speed = 0.0
        self.slip_since_s = None
        self.last_step_s = now

    @property
    def effective_max_speed(self) -> float:
        """本段生效限速（下坡额外收紧）。"""
        if self.profile.kind is SegmentKind.RAMP_DOWN:
            return self.profile.max_speed_mps * self.profile.descent_speed_factor
        return self.profile.max_speed_mps

    @property
    def effective_max_accel(self) -> float:
        if self.profile.kind is SegmentKind.RAMP_DOWN:
            # 下坡防冲：加速度上限更小（含减速度）
            return min(self.profile.max_accel_mps2, self.profile.descent_decel_mps2)
        return self.profile.max_accel_mps2

    def _slip_state(self, now: float, dt: float) -> SlipDecision:
        # 需要上层提供 measured_speed；本函数只被 step 调用（有实测值时）。
        return SlipDecision.NORMAL

    def step(
        self,
        *,
        now: float,
        desired: Velocity2D,
        measured_speed: float | None,
        dt: float,
    ) -> tuple[Velocity2D, SlipDecision]:
        """执行一步坡道控制。

        输入：
            desired: 上层期望速度（如 GoToPoseController 输出）
            measured_speed: 里程计实测速度（打滑检测）；None 表示无实测
            dt: 控制周期（秒）
        输出：
            (command, decision)：
            - command: 经坡道限速/加速度/打滑限制后的速度命令
            - decision: NORMAL / SLIPPING / STUCK
        """
        if dt <= 0.0:
            raise ValueError("dt must be positive")
        if now < (self.last_step_s if self.last_step_s is not None else 0.0):
            raise ValueError("now must be monotonic")

        # 1) 打滑检测：期望速度 vs 实测速度（乙2 降级，不等 IMU）
        decision = SlipDecision.NORMAL
        if measured_speed is not None:
            speed_error = abs(desired.vx) - abs(measured_speed)
            slipping = (
                desired.vx != 0.0
                and speed_error > self.profile.slip_threshold_mps
            )
            if slipping:
                if self.slip_since_s is None:
                    self.slip_since_s = now
                if now - self.slip_since_s >= self.profile.slip_stop_after_s:
                    decision = SlipDecision.STUCK
                    # 卡住：停车（由上层决定重试/换角度，G3.4）
                    command = Velocity2D(0.0, 0.0, 0.0)
                    self.current_speed = 0.0
                    self.last_step_s = now
                    return command, decision
                decision = SlipDecision.SLIPPING
            else:
                self.slip_since_s = None

        # 2) 限速：目标速度不得超过坡道限速
        target = Velocity2D(
            clamp(desired.vx, self.effective_max_speed),
            clamp(desired.vy, self.effective_max_speed),
            desired.wz,
        )
        if decision is SlipDecision.SLIPPING:
            # 打滑中：降到保守速度，避免越滑越快
            target = Velocity2D(
                clamp(target.vx, self.profile.slip_retreat_speed_mps),
                clamp(target.vy, self.profile.slip_retreat_speed_mps),
                target.wz,
            )

        # 3) 加速度限制（下坡额外收紧）
        dt_used = dt
        if self.last_step_s is not None:
            dt_used = min(dt, max(0.0, now - self.last_step_s))
        self.last_step_s = now
        command = limit_velocity_rate(
            Velocity2D(self.current_speed, 0.0, desired.wz),
            target,
            dt_used,
            self.effective_max_accel,
            self.effective_max_accel,
            2.0,
        )
        self.current_speed = command.vx
        return command, decision
