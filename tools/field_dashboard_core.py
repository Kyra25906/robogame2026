"""联调网页的无 ROS 核心：安全门控、控制状态和证据归档。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import lru_cache
import json
import math
from pathlib import Path
import re
import threading
import time
from typing import Any


STATUS_STALE_S = 0.30
DRIVE_STALE_S = 0.30

# ---- 巡线链路（0x14 → 轮子）观测阈值 ----
# 固件按 50 Hz（20 ms）上送 0x14；低于 20 Hz 说明丢帧严重。
LINE_HZ_WARN = 20.0
# 读数年龄：超过 0.25 s 说明至少丢了 ~12 帧。
LINE_AGE_WARN_S = 0.25
# 固件 mcu_tick_ms 是 u32，且 0x14 每 20 ms 采样一次：
# 间隔超过该值说明中间必然丢帧；负增长说明帧被重排或下位机重启。
LINE_TICK_GAP_MS = 80
# 采样频率下限（Hz），低于此值无法支撑 50 Hz 上送，D 项会吃到阶梯值。
LINE_CALIB_MIN_HZ = 10.0
# 黑白原始值至少要有这么大差异，二值化才有意义。
LINE_CALIB_MIN_SPAN = 300
# 一次标定至少要采到这么多帧，避免拿单帧噪声当基准。
LINE_CALIB_MIN_FRAMES = 5
# 两次标定允许的最大间隔（秒）；超时说明中间的采样可能已经不连贯。
LINE_CALIB_MAX_SPAN_S = 120.0


@dataclass
class SafetyStatus:
    received_at: float | None = None
    communication_ok: bool = False
    emergency_stop: bool = False
    physical_start: bool = False
    mechanism_fault: bool = False
    gripper_closed: bool = False
    cube_present: bool = False
    calibrating: bool = False
    imu_valid: bool = False
    boot_id: int = 0
    battery_voltage: float = 0.0
    error_code: int = 0
    detail: str = "尚未收到 /robot/status"


def action_blockers(status: SafetyStatus, now: float) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if status.received_at is None:
        return [{"code": "NO_STATUS", "message": "尚未收到 /robot/status", "evidence": "received_at=null"}]
    age = now - status.received_at
    if age > STATUS_STALE_S:
        blockers.append({"code": "STATUS_STALE", "message": "机器人状态已过期", "evidence": f"age={age:.3f}s > {STATUS_STALE_S:.3f}s"})
    if not status.communication_ok:
        blockers.append({"code": "COMMUNICATION", "message": "STM32 通信不可用", "evidence": "communication_ok=false"})
    if status.emergency_stop:
        blockers.append({"code": "EMERGENCY_STOP", "message": "急停已触发", "evidence": "emergency_stop=true"})
    if not status.physical_start:
        blockers.append({"code": "PHYSICAL_START", "message": "物理启动未授权", "evidence": "physical_start=false"})
    if status.mechanism_fault:
        blockers.append({"code": "MECHANISM_FAULT", "message": "机构报告故障", "evidence": "mechanism_fault=true"})
    return blockers


def clamp_drive(vx: float, vy: float, wz: float, *, max_v: float = 0.30, max_w: float = 0.80, max_vy: float | None = None) -> tuple[float, float, float]:
    lateral_limit = max_v if max_vy is None else max_vy
    values = (vx, vy, wz, max_v, max_w, lateral_limit)
    if any(not math.isfinite(value) for value in values) or max_v <= 0 or max_w <= 0 or lateral_limit <= 0:
        raise ValueError("drive values and limits must be finite; limits must be positive")
    return (
        max(-max_v, min(max_v, vx)),
        max(-lateral_limit, min(lateral_limit, vy)),
        max(-max_w, min(max_w, wz)),
    )


def line_chain_health(
    *,
    now: float,
    last_frame_at: float | None,
    telemetry: dict[str, Any],
    line_hz: float | None = None,
    tick_anomalies: int | None = None,
    sample_times: list[float] | None = None,
) -> dict[str, Any]:
    """把"传感器 → 算法 → 出口"整条巡线链的观测证据与断点判定放在一处。

    输入全部是**已经观测到的事实**（ROS 话题到达情况），不推测硬件状态：
    - `last_frame_at`：最后一次收到 /line_sensor 的本地单调时刻
    - `telemetry`：面板累计的遥测字典（含 line_analog_valid / line_diag 等）
    - `line_hz` / `tick_anomalies`：由 field_dashboard.RosFacade 统计

    输出：
    - `state`：未开始 / 断点(传感器) / 断点(0x14) / 断点(模拟量) / 断点(mcu tick)
              / 断点(算法出口) / 正常 / 观测中
    - `blockers`：每条 {layer, code, message, evidence, fix}
    - `calibration`：黑白分离度与采样率评估（决定能否支撑 50 Hz 纠偏）
    - `chain`：逐段布尔事实，供网页表格直接渲染
    """
    values = telemetry if isinstance(telemetry, dict) else {}
    stale = last_frame_at is None or (now - last_frame_at) > LINE_AGE_WARN_S
    age = None if last_frame_at is None else max(0.0, now - last_frame_at)

    diag = values.get("line_diag")
    if not isinstance(diag, dict):
        diag = None

    chain = {
        "frame_arrived": last_frame_at is not None,
        "frame_fresh": not stale,
        "analog_valid": values.get("line_analog_valid"),
        "mcu_tick_ms": values.get("line_mcu_tick_ms"),
        "line_hz": None if line_hz is None else round(float(line_hz), 1),
        "tick_anomalies": tick_anomalies,
        "controller_status": values.get("line_status"),
        "controller_diag": diag,
        "algorithm_cmd": values.get("line_cmd"),
        "published_cmd": values.get("published_cmd"),
    }

    blockers: list[dict[str, Any]] = []

    if last_frame_at is None:
        blockers.append({
            "layer": "sensor",
            "code": "NO_FRAME",
            "message": "尚未收到 /line_sensor",
            "evidence": "line_sensor 从未到达本面板",
            "fix": "查进程区 bridge 是否运行；再查 STM32 是否在发 0x14（固件 RPI_SendLineTelemetry）",
        })
    elif stale:
        blockers.append({
            "layer": "frames",
            "code": "FRAME_STALE",
            "message": "巡线帧已过期",
            "evidence": f"age={age:.3f}s > {LINE_AGE_WARN_S:.2f}s"
                        + (f"，到达率 {line_hz:.1f}Hz" if line_hz else ""),
            "fix": "先看 mcu tick 是否在增长（断在 STM32/串口）还是完全不动（断在 bridge/进程）",
        })

    if tick_anomalies:
        blockers.append({
            "layer": "mcu_tick",
            "code": "TICK_ANOMALY",
            "message": "MCU 采样时间戳有跳变或倒退，帧被丢弃",
            "evidence": f"累计异常 {tick_anomalies} 次"
                        f"（固件 50Hz 上送，间隔 > {LINE_TICK_GAP_MS}ms 或倒退即计入）",
            "fix": "与 bridge 日志里的 'rejected LINE mcu_tick_ms' 警告对照；检查串口/负载",
        })

    if last_frame_at is not None and values.get("line_analog_valid") is False:
        blockers.append({
            "layer": "analog",
            "code": "ANALOG_INVALID",
            "message": "帧在到达，但模拟量无效（探头掉线或最近帧不是 $A）",
            "evidence": f"analog_valid=false，控制器累计丢弃 "
                        f"{values.get('line_invalid_frames', '?')} 帧",
            "fix": "查探头供电/接线与 UART7 使能命令 $0,1,1#；Keil Watch 看 line_online 与 line_analog_frame_latest",
        })

    if diag is not None and diag.get("blocked"):
        reasons = diag.get("reasons") or []
        blockers.append({
            "layer": "controller",
            "code": "CONTROLLER_BLOCKED",
            "message": "巡线控制器被状态门控拦住，没有输出速度",
            "evidence": "；".join(str(r) for r in reasons) or "blocked=true",
            "fix": "逐条排查：/robot/status 是否新鲜、communication_ok、mechanism_fault、读数是否过期",
        })

    if last_frame_at is not None and not stale and diag is None:
        blockers.append({
            "layer": "controller",
            "code": "NO_CONTROLLER_STATUS",
            "message": "读数正常，但 /line_follow/status 未收到或已过期",
            "evidence": "控制器状态缺失，无法判断算法是否在输出",
            "fix": "在进程区启动巡线（line_follow_controller）",
        })

    algorithm = values.get("line_cmd")
    published = values.get("published_cmd")
    if algorithm and published:
        wants_motion = any(abs(float(v)) > 1e-6 for v in algorithm.values())
        zero_published = all(abs(float(v)) <= 1e-6 for v in published.values())
        if wants_motion and zero_published:
            blockers.append({
                "layer": "output",
                "code": "ZERO_PUBLISHED",
                "message": "算法要求运动，但 /cmd_vel 观测为零",
                "evidence": f"line_cmd={algorithm} 而 published_cmd={published}",
                "fix": "门控或仲裁未放行；另注意 /cmd_vel 是共享话题，其他发布者也可能覆盖",
            })

    # 标定评估：只用观测到的原始值，不做任何硬件推断。
    calibration: dict[str, Any] = {"status": "unknown", "detail": "尚无可评估的读数"}
    channels = values.get("line_sensor")
    if channels:
        values_int = [int(v) for v in channels]
        span = max(values_int) - min(values_int)
        if span < LINE_CALIB_MIN_SPAN:
            calibration = {
                "status": "suspect",
                "span": span,
                "detail": (
                    f"黑白分离度仅 {span}（建议 ≥ {LINE_CALIB_MIN_SPAN}）："
                    "黑线压住与白底压住时各看一次，若差值一直很小，"
                    "先查模块高度/供电，再谈阈值"
                ),
            }
        else:
            calibration = {
                "status": "ok",
                "span": span,
                "detail": f"当前帧黑白分离度 {span}",
            }
        if sample_times and len(sample_times) >= 2:
            window = sample_times[-1] - sample_times[0]
            rate = (len(sample_times) - 1) / window if window > 0 else 0.0
            calibration["sample_hz"] = round(rate, 1)
            if rate < LINE_CALIB_MIN_HZ:
                calibration["status"] = "suspect"
                calibration["detail"] += (
                    f"；模块采样率仅 {rate:.1f}Hz（< {LINE_CALIB_MIN_HZ}Hz），"
                    "无法支撑 50Hz 纠偏，D 项会吃到阶梯值"
                )

    if last_frame_at is None:
        state = "未开始"
    elif stale:
        state = "断点(0x14/进程)"
    elif values.get("line_analog_valid") is not True:
        state = "断点(模拟量)"
    elif tick_anomalies:
        state = "断点(mcu tick)"
    elif any(b["layer"] in {"controller", "output"} for b in blockers):
        state = "断点(算法出口)"
    elif diag is None:
        state = "观测中"
    else:
        state = "正常"

    return {
        "state": state,
        "age_s": None if age is None else round(age, 3),
        "blockers": blockers,
        "calibration": calibration,
        "chain": chain,
    }


@dataclass
class LineCalibration:
    """黑白标定的**状态与判定**（纯逻辑，无 ROS 依赖，可直接单测）。

    为什么需要它：巡线二值化默认假设"黑≈0、白≈4095"。真实 8 路模块常见输出
    是 [200, 900] 这种窄区间，此时 `v/4095` 全部落在阈值同侧——八路全判成"白"，
    算法永远不纠偏，而网页上读数看着一切正常。标定就是把**实测**的黑白基准
    变成 `white_ref`/`black_ref` 参数推给巡线节点。

    用法::

        calib = LineCalibration()
        calib.capture("white", [210, 215, ...], now=1.0)
        calib.capture("black", [820, 830, ...], now=8.0)
        verdict = calib.verdict(now=9.0)      # 通过/不通过 + 为什么
        verdict = calib.begin_apply(now=9.0)  # 未通过时抛错，不允许推参数
    """

    # 单次标定（白底或黑线）至少要按住这么久
    min_seconds: float = 1.0
    white_samples: list[list[int]] = field(default_factory=list)
    black_samples: list[list[int]] = field(default_factory=list)
    white_started_at: float | None = None
    black_started_at: float | None = None
    white_last_at: float | None = None
    black_last_at: float | None = None
    # 显式的"正在采集"标志。不要用"某组有数据、另一组没有"去推断：
    # 那样点完白底之后会一直采样，把后续任意帧都算进白底基准。
    capturing: str | None = None

    def reset(self) -> None:
        self.white_samples = []
        self.black_samples = []
        self.white_started_at = None
        self.black_started_at = None
        self.white_last_at = None
        self.black_last_at = None
        self.capturing = None

    def begin_capture(self, target: str) -> None:
        """开始采集一次基准（对应面板上按钮按下）。"""
        if target not in {"white", "black"}:
            raise ValueError(f"unknown calibration target: {target!r}")
        self.capturing = target

    def end_capture(self) -> None:
        """结束采集（按钮松开）。"""
        self.capturing = None

    def active_target(self) -> str | None:
        return self.capturing

    def capture(self, target: str, channels: list[int], *, now: float) -> None:
        """记录一帧。target 为 "white"/"black"，表示把这一帧当作对应基准。"""
        if target == "white":
            if self.white_started_at is None:
                self.white_started_at = now
            self.white_last_at = now
            self.white_samples.append([int(v) for v in channels])
        elif target == "black":
            if self.black_started_at is None:
                self.black_started_at = now
            self.black_last_at = now
            self.black_samples.append([int(v) for v in channels])
        else:
            raise ValueError(f"unknown calibration target: {target!r}")

    @staticmethod
    def _mean(samples: list[list[int]]) -> list[float]:
        count = len(samples)
        return [sum(frame[i] for frame in samples) / count for i in range(8)]

    def durations(self, now: float) -> dict[str, float]:
        """每组的**实际采样跨度**（首帧到末帧），不是"距现在多久"。

        用距现在会把"按住 0.3 秒、然后等了 3 秒"算成 3.3 秒，
        于是采样太短的标定会被误判为通过。
        """
        def span(started: float | None, last: float | None) -> float:
            if started is None or last is None:
                return 0.0
            return max(0.0, last - started)

        return {
            "white": span(self.white_started_at, self.white_last_at),
            "black": span(self.black_started_at, self.black_last_at),
        }

    def verdict(self, now: float) -> dict[str, Any]:
        """标定是否可用；不可用时给出**具体**原因与下一步动作。"""
        durations = self.durations(now)
        problems: list[str] = []

        if not self.white_samples:
            problems.append("未采到白底读数：把车放在白底上按住「标定白底」")
        elif durations["white"] < self.min_seconds:
            problems.append(
                f"白底采样时间不足（{durations['white']:.1f}s < {self.min_seconds:.1f}s）"
            )
        if not self.black_samples:
            problems.append("未采到黑线读数：把车压在黑线上按住「标定黑线」")
        elif durations["black"] < self.min_seconds:
            problems.append(
                f"黑线采样时间不足（{durations['black']:.1f}s < {self.min_seconds:.1f}s）"
            )
        if problems:
            return {"ok": False, "code": "INCOMPLETE", "problems": problems,
                    "detail": "；".join(problems)}

        if (self.white_started_at is not None and self.black_started_at is not None
                and abs(self.black_started_at - self.white_started_at) > LINE_CALIB_MAX_SPAN_S):
            return {"ok": False, "code": "TOO_SLOW", "problems": ["两次标定间隔过长，采样可能不连贯"],
                    "detail": "两次标定间隔过长，请重新标定"}

        if (len(self.white_samples) < LINE_CALIB_MIN_FRAMES
                or len(self.black_samples) < LINE_CALIB_MIN_FRAMES):
            return {
                "ok": False, "code": "FEW_FRAMES",
                "problems": [
                    f"采样帧数不足（白 {len(self.white_samples)}、黑 {len(self.black_samples)}，"
                    f"至少各 {LINE_CALIB_MIN_FRAMES}）"
                ],
                "detail": "采样帧数不足，请按住按钮久一点",
            }

        white = self._mean(self.white_samples)
        black = self._mean(self.black_samples)
        span = max(abs(w - b) for w, b in zip(white, black))
        if span < LINE_CALIB_MIN_SPAN:
            return {
                "ok": False, "code": "NO_CONTRAST",
                "problems": [
                    f"黑白分离度只有 {span:.0f}（建议 ≥ {LINE_CALIB_MIN_SPAN}）："
                    "模块分辨不出黑线，先查探头高度、供电与接线，而不是调阈值"
                ],
                "detail": f"黑白分离度不足（{span:.0f}）",
                "span": round(span, 1),
            }

        # 基准极性：上位机归一化用 (raw - white_ref) / (black_ref - white_ref)，
        # 与 docs 里 (raw - white_min)/(black_max - white_min) 同一个方向。
        # 因此要求 **black_ref > white_ref**（黑线读数更大：反光弱→模拟值小是另一种模块，
        # 这时用户按提示标签贴反了两组采样）。
        # 若多数通道 white > black，说明黑白两组接反了，必须交换——
        # 否则归一化会把整条算法反向。
        inverted = sum(white[i] > black[i] for i in range(8))
        note = ""
        if inverted > 4:
            white, black = black, white
            note = f"检测到黑白基准接反（{inverted}/8 路反相），已自动交换"

        span = max(w - b for w, b in zip(white, black))
        return {
            "ok": True, "code": "OK", "problems": [],
            "detail": f"黑白分离度 {abs(span):.0f}，可用于二值化",
            "span": round(abs(span), 1),
            "white_ref": [round(v, 1) for v in white],
            "black_ref": [round(v, 1) for v in black],
            "threshold": 0.5,
            "white_frames": len(self.white_samples),
            "black_frames": len(self.black_samples),
            "note": note,
        }

    def begin_apply(self, now: float) -> dict[str, Any]:
        """提交守卫：没通过校验就不允许推参数。"""
        verdict = self.verdict(now)
        if not verdict["ok"]:
            raise ValueError(verdict["detail"])
        return verdict


@dataclass
class DashboardState:
    safety: SafetyStatus = field(default_factory=SafetyStatus)
    mode: str = "OBSERVE"
    manual_last_seen: float | None = None
    manual_sequence: int = -1
    telemetry: dict[str, Any] = field(default_factory=dict)
    last_mechanism_result: dict[str, Any] | None = None

    def acquire_manual(self, *, now: float, conflicting_publishers: list[str]) -> None:
        blockers = action_blockers(self.safety, now)
        if blockers:
            raise ValueError(blockers[0]["message"])
        if conflicting_publishers:
            raise ValueError("存在其他 /cmd_vel 发布者: " + ", ".join(conflicting_publishers))
        self.mode = "MANUAL"
        self.manual_last_seen = now
        self.manual_sequence = -1

    def accept_drive(self, *, sequence: int, now: float) -> None:
        if self.mode != "MANUAL":
            raise ValueError("当前不在手动接管模式")
        blockers = action_blockers(self.safety, now)
        if blockers:
            raise ValueError(blockers[0]["message"])
        if sequence <= self.manual_sequence:
            raise ValueError("手动控制序号重复或倒退")
        self.manual_sequence = sequence
        self.manual_last_seen = now

    def manual_expired(self, now: float) -> bool:
        return self.mode == "MANUAL" and (
            self.manual_last_seen is None or now - self.manual_last_seen > DRIVE_STALE_S
        )

    def release_manual(self) -> None:
        self.mode = "OBSERVE"
        self.manual_last_seen = None

    def snapshot(self, now: float) -> dict[str, Any]:
        safety = asdict(self.safety)
        safety["age_s"] = None if self.safety.received_at is None else max(0.0, now - self.safety.received_at)
        return {
            "mode": self.mode,
            "safety": safety,
            "blockers": action_blockers(self.safety, now),
            "telemetry": dict(self.telemetry),
            "last_mechanism_result": self.last_mechanism_result,
        }


class SessionArchive:
    def __init__(self, root: Path, *, now: datetime | None = None):
        stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S_%f")
        self.path = root / stamp
        self.raw_path = self.path / "raw"
        self.raw_path.mkdir(parents=True, exist_ok=False)
        self._lock = threading.Lock()
        self._write_json(self.path / "session.json", {"started_at": datetime.now().astimezone().isoformat(), "format_version": 1})

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def append(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        record = {"wall_time": datetime.now().astimezone().isoformat(), "monotonic_s": time.monotonic(), **payload}
        target = self.path / ("telemetry.jsonl" if kind == "telemetry" else "events.jsonl")
        with self._lock, target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def append_raw(self, process: str, line: str) -> None:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in process)
        with self._lock, (self.raw_path / f"{safe}.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now().astimezone().isoformat()} {line}\n")


# ---------------------------------------------------------------------------
# B1：全流程路线只读摘要（给网页显示「这趟要跑哪 13 段、每段怎么算走完」）
# ---------------------------------------------------------------------------

#: 场地图（survey 段 = 黑线节点/边/停车点，现场实测）
FIELD_LAYOUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "ros2_ws/src/robogame_bringup/config/field_layout.yaml"
)

#: 机器人公共配置（用于核对「计划要搭 2 层」与 manipulator_client 的放置高度）
ROBOT_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "ros2_ws/src/robogame_bringup/config/robot.yaml"
)

_SURVEY_SECTIONS = ("line_nodes", "line_edges", "stops")


def load_survey(layout_path: Path | None = None) -> dict[str, dict]:
    """读场地图 survey 段。

    实现放在 `robogame_core.route_loader`（ROS 节点、网页面板、测试共用同一份，
    避免「网页显示的场地」与「车实际跑的场地」来自两个解析器）。
    """
    from robogame_core.route_loader import load_survey as _load_survey

    return _load_survey(layout_path if layout_path is not None else FIELD_LAYOUT_PATH)


def configured_place_heights(config_path: Path | None = None) -> list[float]:
    """从 robot.yaml 读 `manipulator_client.place_heights_m`（手写解析，容忍无 PyYAML）。"""
    path = ROBOT_CONFIG_PATH if config_path is None else config_path
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^\s*place_heights_m:\s*\[([^\]]*)\]", text, re.MULTILINE)
    if not match:
        return []
    return [float(item) for item in match.group(1).split(",") if item.strip()]


def build_route_payload(
    layout_path: Path | None = None, *, config_path: Path | None = None
) -> dict[str, Any]:
    """B1 全流程路线的只读摘要（网页面板用）。

    任何一步失败都返回 `available: False` + 原因，**不猜**：路线数据是「要开真车」
    的东西，读不到/自检不过时必须显式暴露，而不是显示一份看起来正常的表。
    """
    try:
        from robogame_core.mission_dispatch import placement_heights_issue
        from robogame_core.mission_route import build_route_plan
    except ImportError as exc:  # robogame_core 未安装/未 source 工作空间
        return {"available": False, "reason": f"robogame_core 未就绪：{exc}"}
    try:
        survey = load_survey(layout_path)
        plan = build_route_plan(survey)
    except Exception as exc:  # 缺文件/结构变化/自检失败
        return {"available": False, "reason": f"路线数据不可用：{exc}"}
    placement_warning = None
    try:
        configured = configured_place_heights(config_path)
        if configured:
            placement_warning = placement_heights_issue(plan.cargo_plan, configured)
    except Exception as exc:  # 读不到配置不算致命，但要说明为什么没有这项检查
        placement_warning = f"未能核对放置高度：{exc}"
    return {
        "available": True,
        "version": plan.version,
        "source_note": plan.source_note,
        "start": {"x": plan.start_pose.x, "y": plan.start_pose.y, "yaw": plan.start_pose.yaw},
        "segments": plan.summary(),
        "placement_warning": placement_warning,
        "turns": [
            {
                "segment_id": spec.segment_id,
                "ref": spec.ref,
                "direction": spec.direction.value,
                "target_yaw_rad": spec.target_yaw_rad,
                "passthrough_junctions": spec.passthrough_junctions,
            }
            for spec in plan.turn_registry()
        ],
        "passthrough": [
            {"segment_id": segment_id, "ref": ref}
            for segment_id, ref in plan.passthrough_registry()
        ],
        "cargo": {
            "orange": plan.cargo_plan.orange,
            "purple": plan.cargo_plan.purple,
            "layers": plan.cargo_plan.layers,
            "layout": plan.cargo_plan.layout,
            "note": plan.cargo_plan.note,
        },
        "speed_limits": {
            "max_vx": plan.speed_limits.max_vx,
            "max_vy": plan.speed_limits.max_vy,
            "max_wz": plan.speed_limits.max_wz,
        },
    }


@lru_cache(maxsize=4)
def route_payload(layout_path: Path | None = None) -> dict[str, Any]:
    """`build_route_payload` 的缓存包装（路线是静态数据，snapshot 每秒都会被取）。"""
    return build_route_payload(layout_path)
