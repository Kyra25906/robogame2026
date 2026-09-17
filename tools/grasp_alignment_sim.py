"""抓取对准的闭环仿真与面板计算：网页上"看得见"两段律怎么收敛。

为什么有这个东西
----------------
真车调试最费时间的是"我改了这个参数，车会怎么动"。这个模块用与真车**同一份**
纯函数（``robogame_core.grasp_alignment``，不是重写一遍）在网页里跑一段闭环：

    位置/朝向 → 相机测量（前向 + 画面横向）→ 车体系误差 → 转向/前进命令
    → 按单轨模型积分 → 下一拍

于是"偏 10 cm 起步会不会收敛""什么时候才允许往前压"不用上车、不开终端就能看到。

模型边界（刻意与真车约束一致，别当成真车证据）
- 单轨积分：``x += vx·dt·cos(yaw)``，``y += vx·dt·sin(yaw)``，``yaw += wz·dt``；
- **横向速度恒 0**（真车 VY 恒 0），横向误差只能靠转向 + 前进收敛；
- 相机测量由真实几何算出（含相机偏角），不是把误差直接喂给控制律；
- 没有打滑、惯性、延迟、视觉噪声——这些必须真车验收。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

_CORE_SRC = Path(__file__).resolve().parents[1] / "ros2_ws" / "src" / "robogame_core"
if str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

from robogame_core.grasp_alignment import (  # noqa: E402
    calculate_turn_alignment_command,
    target_in_body_frame,
)

# 与 manipulator_client 节点默认值保持一致（tests 里有交叉断言，改一处必须改两处）
LAW_DEFAULTS: dict[str, float] = {
    "target_distance_m": 0.24,
    "distance_tolerance_m": 0.025,
    "cross_tolerance_m": 0.018,
    "kp_distance": 0.8,
    "kp_bearing": 1.2,
    "max_speed": 0.18,
    "max_turn_rate": 0.6,
    "min_turn_rate": 0.0,
    "camera_yaw_offset_rad": 0.0,
}

SIM_DEFAULTS: dict[str, float] = {
    "target_x_m": 0.24,
    "target_y_m": -0.10,
    "start_x_m": 0.0,
    "start_y_m": 0.0,
    "start_yaw_rad": 0.0,
    "dt_s": 0.05,
    "max_steps": 400,
}

# 面板一次性拿到的全部默认值（控制律 + 仿真）
GRASP_DEFAULTS: dict[str, float] = {**LAW_DEFAULTS, **SIM_DEFAULTS}

_KNOWN_KEYS = set(GRASP_DEFAULTS)


def _merge(overrides: dict) -> dict:
    unknown = sorted(set(overrides) - _KNOWN_KEYS)
    if unknown:
        raise ValueError(f"unknown grasp parameters: {', '.join(unknown)}")
    values = dict(GRASP_DEFAULTS)
    values.update(overrides)
    return values


def _law(values: dict) -> dict:
    return {key: float(values[key]) for key in LAW_DEFAULTS
            if key != "camera_yaw_offset_rad"}


def evaluate_detection(forward_m: float, lateral_right_m: float, **overrides) -> dict:
    """面板用：把一帧检测换算成误差 + 命令（与真车同一份函数）。"""
    values = _merge(overrides)
    frame = target_in_body_frame(
        forward_m=forward_m,
        lateral_right_m=lateral_right_m,
        camera_yaw_offset_rad=values["camera_yaw_offset_rad"],
    )
    if not frame.valid:
        return {"valid": False, "reject_reason": frame.reject_reason}
    command = calculate_turn_alignment_command(
        forward_m=frame.forward_m, left_m=frame.left_m, **_law(values)
    )
    return {
        "valid": True,
        "reject_reason": "",
        "along_m": round(frame.forward_m, 4),
        "cross_m": round(frame.left_m, 4),
        "range_m": round(frame.range_m, 4),
        "bearing_rad": round(frame.bearing_rad, 4),
        "phase": command.phase.value,
        "linear_x": round(command.linear_x, 4),
        "linear_y": 0.0,
        "angular_z": round(command.angular_z, 4),
        "ready_to_grab": command.ready_to_grab,
    }


def simulate_grasp(**overrides) -> dict:
    """从给定起点跑到"可以抓"或步数上限，返回全过程轨迹与安全统计。"""
    values = _merge(overrides)
    dt_s = float(values["dt_s"])
    max_steps = int(values["max_steps"])
    if dt_s <= 0.0:
        raise ValueError("dt_s must be positive")
    if max_steps <= 0:
        raise ValueError("max_steps must be positive")

    target_x = float(values["target_x_m"])
    target_y = float(values["target_y_m"])
    camera_yaw = float(values["camera_yaw_offset_rad"])
    cross_tolerance = float(values["cross_tolerance_m"])
    law = _law(values)

    x = float(values["start_x_m"])
    y = float(values["start_y_m"])
    yaw = float(values["start_yaw_rad"])

    trace: list[dict] = []
    phase_counts: dict[str, int] = {}
    safety_violations = 0
    converged = False
    stop_reason = "max_steps"

    for _ in range(max_steps):
        dx, dy = target_x - x, target_y - y
        forward_body = dx * math.cos(yaw) + dy * math.sin(yaw)
        left_body = -dx * math.sin(yaw) + dy * math.cos(yaw)
        # 真实检测器报的是**斜距**（由方块像素尺寸反推）和画面横向偏移，不是前向投影：
        # 目标在正侧方时前向投影为 0，但斜距依然为正，检测仍然有效。
        axis_cam = forward_body * math.cos(camera_yaw) + left_body * math.sin(camera_yaw)
        left_cam = -forward_body * math.sin(camera_yaw) + left_body * math.cos(camera_yaw)
        forward_cam = math.hypot(axis_cam, left_cam)
        lateral_right = -left_cam

        frame = target_in_body_frame(
            forward_m=forward_cam,
            lateral_right_m=lateral_right,
            camera_yaw_offset_rad=camera_yaw,
        )
        if not frame.valid:
            stop_reason = "invalid_detection"
            break

        command = calculate_turn_alignment_command(
            forward_m=frame.forward_m, left_m=frame.left_m, **law
        )
        if abs(frame.left_m) > cross_tolerance and command.linear_x != 0.0:
            safety_violations += 1
        phase_counts[command.phase.value] = phase_counts.get(command.phase.value, 0) + 1
        trace.append({
            "step": len(trace),
            "t_s": round(len(trace) * dt_s, 3),
            "x_m": round(x, 4), "y_m": round(y, 4), "yaw_rad": round(yaw, 4),
            "forward_m": round(forward_cam, 4),
            "lateral_right_m": round(lateral_right, 4),
            "along_m": round(frame.forward_m, 4), "cross_m": round(frame.left_m, 4),
            "bearing_rad": round(frame.bearing_rad, 4),
            "linear_x": round(command.linear_x, 4), "linear_y": 0.0,
            "angular_z": round(command.angular_z, 4),
            "phase": command.phase.value,
        })

        if command.ready_to_grab:
            converged = True
            stop_reason = "ready"
            break

        x += command.linear_x * dt_s * math.cos(yaw)
        y += command.linear_x * dt_s * math.sin(yaw)
        yaw += command.angular_z * dt_s
        if math.hypot(target_x - x, target_y - y) > 5.0:
            stop_reason = "diverged"
            break

    last = trace[-1] if trace else {}
    return {
        "ok": True,
        "converged": converged,
        "stop_reason": stop_reason,
        "steps": len(trace),
        "duration_s": round(len(trace) * dt_s, 3),
        "phase_counts": phase_counts,
        "safety_violations": safety_violations,
        "lateral_velocity_always_zero": all(item["linear_y"] == 0.0 for item in trace),
        "final": {
            "x_m": last.get("x_m"), "y_m": last.get("y_m"), "yaw_rad": last.get("yaw_rad"),
            "along_m": last.get("along_m"), "cross_m": last.get("cross_m"),
        },
        "target": {"x_m": target_x, "y_m": target_y},
        "params": {key: round(float(value), 6) for key, value in values.items()},
        "trace": trace,
    }
