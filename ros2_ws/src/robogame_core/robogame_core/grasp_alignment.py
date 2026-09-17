"""抓取对准：把一次视觉检测换算成车体坐标系误差，再给出转向+前进两段命令。

为什么需要这个模块
------------------
真车没有横移（VY 恒 0，电控硬要求），所以「目标偏右就横移过去」这条老路在真车上
不可执行。横向误差只能用两段动作收敛：

    第一段 TURN      原地转向，把目标转到车头正前方（此段前进速度恒为 0）
    第二段 APPROACH  目标在车头轴上后，前进/后退把距离收到抓取距离

为什么接近轴**必须**是车头轴
----------------------------
本模块不提供「接近轴偏角」参数。原因是数学结论，不是省事：
设接近轴与车头成 γ，车只能产生沿车头轴的速度和前向角速度。当 γ=±90°（爪朝侧面）时，
前进既不改横向偏差、旋转在目标位于正侧方时对横向偏差的增益又趋近于零，
于是「偏差超容差 → 禁止前进 → 又转不动偏差」会卡死（等于原地平行泊车的奇异位形）。
因此：**视觉对准只把方块送到车头轴上，侧向够取由云盘/机械臂完成**。
这条约束写成"参数根本不存在"，而不是文档里的一句提醒——参数不存在，就没人能配错。

相机偏角仍然需要，因为眼在手上时相机可能不朝车头；检测量在相机坐标系，
换算到车体坐标系必须用它。注意：对接阶段云盘要指向车头（相机与爪同向），
否则相机看到的不是爪要抓的点（预定位交接，见真车对接设计稿）。

坐标约定（与 cube_perception 保持一致，不要改）
---------------------------------------------
车体坐标系：x 朝前、y 朝左、z 朝上（ROS REP-103）。
相机检测量：``forward_m`` 是相机前方距离；``lateral_right_m`` 为
``(u - width/2) * distance / focal``，即 **正值表示目标在画面右侧**。
输出误差：``along_m`` 目标沿车头轴的距离（正=在车前方），
``cross_m`` 目标横向偏差（**正=偏左**）。角度逆时针为正、弧度制。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

_TWO_PI = 2.0 * math.pi


class AlignmentPhase(str, Enum):
    """当前处于两段动作的哪一段。"""

    TURN = "TURN"
    APPROACH = "APPROACH"
    READY = "READY"


@dataclass(frozen=True)
class TargetInBodyFrame:
    """目标在车体坐标系下的位置。``valid=False`` 时数值不可用于控制。"""

    forward_m: float
    left_m: float
    range_m: float
    bearing_rad: float
    valid: bool
    reject_reason: str = ""


@dataclass(frozen=True)
class TurnAlignmentCommand:
    """抓取前的低速底盘命令。

    故意不提供 ``linear_y`` 字段：真车 VY 恒 0，横向误差只能靠转向收敛，
    这样「命令里带上横移」在类型层面就不可能发生。``linear_y`` 属性恒为 0，
    只是为了方便直接填 ``Twist``。
    """

    linear_x: float
    angular_z: float
    ready_to_grab: bool
    phase: AlignmentPhase

    @property
    def linear_y(self) -> float:
        """真车无横移，恒为 0（保留属性只为填充 Twist 方便）。"""
        return 0.0


def _wrap_angle(angle: float) -> float:
    """把角度折到 (-pi, pi]，避免相机装反/云盘转动时出现假的大偏差。"""
    wrapped = math.fmod(angle + math.pi, _TWO_PI)
    if wrapped <= 0.0:
        wrapped += _TWO_PI
    return wrapped - math.pi


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _require_finite(**values: float) -> None:
    for name, value in values.items():
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")


def target_in_body_frame(
    *,
    forward_m: float,
    lateral_right_m: float,
    camera_yaw_offset_rad: float = 0.0,
) -> TargetInBodyFrame:
    """把一次相机检测换算到车体坐标系。

    非有限输入直接抛 ``ValueError``（属编程/数据错误，不应被静默当成「没看见」）；
    「看得见但不可用」（距离非正）返回 ``valid=False`` + 原因，由调用方按无目标处理。
    """
    _require_finite(
        forward_m=forward_m,
        lateral_right_m=lateral_right_m,
        camera_yaw_offset_rad=camera_yaw_offset_rad,
    )
    if forward_m <= 0.0:
        return TargetInBodyFrame(
            forward_m=0.0, left_m=0.0, range_m=0.0, bearing_rad=0.0,
            valid=False, reject_reason="non_positive_range",
        )

    offset = float(camera_yaw_offset_rad)
    cos_offset, sin_offset = math.cos(offset), math.sin(offset)
    # 相机系 -> 车体系：x_cam = (cos, sin)，y_cam = (-sin, cos)（见模块顶部约定）
    forward_body = cos_offset * float(forward_m) + sin_offset * float(lateral_right_m)
    left_body = sin_offset * float(forward_m) - cos_offset * float(lateral_right_m)
    return TargetInBodyFrame(
        forward_m=forward_body,
        left_m=left_body,
        range_m=math.hypot(float(forward_m), float(lateral_right_m)),
        bearing_rad=_wrap_angle(math.atan2(left_body, forward_body)),
        valid=True,
        reject_reason="",
    )


def calculate_turn_alignment_command(
    *,
    forward_m: float,
    left_m: float,
    target_distance_m: float,
    distance_tolerance_m: float,
    cross_tolerance_m: float,
    kp_distance: float,
    kp_bearing: float,
    max_speed: float,
    max_turn_rate: float,
    min_turn_rate: float = 0.0,
) -> TurnAlignmentCommand:
    """转向优先的两段对准律（输入为车体坐标系下的目标位置）。

    安全不变式：**横向偏差超出容差时 ``linear_x`` 一定为 0**——绝不在目标偏离
    车头轴的情况下向前压，否则会蹭到方块或撞件。这一条有专门测试守住。

    ``min_turn_rate`` 用于克服执行器死区（命令太小底盘不动，会永远转不到位）；
    设为 0 表示不做死区补偿。
    """
    _require_finite(
        forward_m=forward_m,
        left_m=left_m,
        target_distance_m=target_distance_m,
        distance_tolerance_m=distance_tolerance_m,
        cross_tolerance_m=cross_tolerance_m,
        kp_distance=kp_distance,
        kp_bearing=kp_bearing,
        max_speed=max_speed,
        max_turn_rate=max_turn_rate,
        min_turn_rate=min_turn_rate,
    )
    if target_distance_m <= 0.0:
        raise ValueError("target_distance_m must be positive")
    if distance_tolerance_m < 0.0 or cross_tolerance_m < 0.0:
        raise ValueError("alignment tolerances cannot be negative")
    if kp_distance <= 0.0 or kp_bearing <= 0.0:
        raise ValueError("gains must be positive")
    if max_speed <= 0.0 or max_turn_rate <= 0.0:
        raise ValueError("speed and turn rate limits must be positive")
    if min_turn_rate < 0.0 or min_turn_rate > max_turn_rate:
        raise ValueError("min_turn_rate must be within [0, max_turn_rate]")

    def turn_command(bearing: float) -> TurnAlignmentCommand:
        rate = _clamp(kp_bearing * bearing, max_turn_rate)
        if min_turn_rate > 0.0 and 0.0 < abs(rate) < min_turn_rate:
            rate = math.copysign(min_turn_rate, bearing)
        return TurnAlignmentCommand(0.0, rate, False, AlignmentPhase.TURN)

    if forward_m <= 0.0:
        # 目标在正侧方或后方：先原地把它带回车头前方，不做距离判断
        return turn_command(math.copysign(math.pi / 2.0, left_m if left_m != 0.0 else 1.0))

    if abs(left_m) > cross_tolerance_m:
        return turn_command(math.atan2(left_m, forward_m))

    if abs(forward_m - target_distance_m) > distance_tolerance_m:
        error = forward_m - target_distance_m
        return TurnAlignmentCommand(
            _clamp(kp_distance * error, max_speed), 0.0, False, AlignmentPhase.APPROACH
        )

    return TurnAlignmentCommand(0.0, 0.0, True, AlignmentPhase.READY)
