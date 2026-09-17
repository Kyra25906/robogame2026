#!/usr/bin/env python3
"""单 SSH 窗口的 RoboGame 联调网页服务。"""

from __future__ import annotations

import argparse
import asyncio
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import queue
import signal
import threading
import time
import uuid
from urllib.parse import urlparse

try:
    from distance_trial import DistanceTrial
    from field_console import FieldConsole, load_specs
    from field_dashboard_core import (
        LINE_TICK_GAP_MS,
        DashboardState, LineCalibration, SessionArchive, SafetyStatus,
        action_blockers, clamp_drive, line_chain_health, route_payload,
    )
    from grasp_alignment_sim import GRASP_DEFAULTS, evaluate_detection, simulate_grasp
    from odom_calibration import OdomTrial, analyze_trials, trial_from_result
except ImportError:  # 允许测试以 tools.field_dashboard 导入
    from tools.distance_trial import DistanceTrial
    from tools.field_console import FieldConsole, load_specs
    from tools.field_dashboard_core import (
        LINE_TICK_GAP_MS,
        DashboardState, LineCalibration, SessionArchive, SafetyStatus,
        action_blockers, clamp_drive, line_chain_health, route_payload,
    )
    from tools.grasp_alignment_sim import GRASP_DEFAULTS, evaluate_detection, simulate_grasp
    from tools.odom_calibration import OdomTrial, analyze_trials, trial_from_result

try:
    from arm_selftest import ArmSelftest, stage_names
except ImportError:  # 允许测试以 tools.field_dashboard 导入
    from tools.arm_selftest import ArmSelftest, stage_names


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = Path(__file__).with_name("field_dashboard_web")


class EventHub:
    def __init__(self):
        self._clients: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        client: queue.Queue = queue.Queue(maxsize=300)
        with self._lock:
            self._clients.add(client)
        return client

    def unsubscribe(self, client: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(client)

    def publish(self, event: dict) -> None:
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.put_nowait(event)
            except queue.Full:
                try:
                    client.get_nowait()
                    client.put_nowait(event)
                except queue.Empty:
                    pass


class RosFacade:
    """ROS 线程适配；构造失败时网页仍可用于进程和日志诊断。"""

    def __init__(self, controller):
        import rclpy
        from geometry_msgs.msg import Twist
        from nav_msgs.msg import Odometry
        from robogame_interfaces.msg import RobotStatus
        try:
            from robogame_interfaces.msg import LineSensor
        except ImportError:
            LineSensor = None
        from robogame_interfaces.srv import ExecuteMechanism, SetLiftHeight
        try:
            from robogame_interfaces.srv import SetArmJoint
        except ImportError:
            # 树莓派上可能还没因为新增 SetArmJoint.srv 而重新 colcon build。
            # 这时机械臂面板显示“接口未安装”，其余功能照常可用。
            SetArmJoint = None
        from sensor_msgs.msg import Imu
        from std_msgs.msg import String

        self.rclpy = rclpy
        self.Twist = Twist
        self.ExecuteMechanism = ExecuteMechanism
        self.SetLiftHeight = SetLiftHeight
        self.SetArmJoint = SetArmJoint
        rclpy.init(args=None)
        self.node = rclpy.create_node("field_dashboard")
        self.controller = controller
        self.cmd_pub = self.node.create_publisher(Twist, "/cmd_vel", 20)
        self.node.create_subscription(RobotStatus, "/robot/status", self._status, 20)
        self.node.create_subscription(Odometry, "/wheel_odom", lambda msg: self._rate("wheel_odom", msg), 20)
        self.node.create_subscription(Odometry, "/pose", self._pose, 20)
        self.node.create_subscription(Imu, "/imu/data", lambda msg: self._rate("imu", msg), 20)
        if LineSensor is not None:
            self.node.create_subscription(LineSensor, "/line_sensor", self._line, 20)
        else:
            from std_msgs.msg import Float32MultiArray
            self.node.create_subscription(Float32MultiArray, "/line_sensor", self._legacy_line, 20)
            controller.update_telemetry({"line_interface": "legacy"})
        self.node.create_subscription(Twist, "/line_follow/cmd", lambda msg: self._velocity("line_cmd", msg), 20)
        self.node.create_subscription(Twist, "/cmd_vel", lambda msg: self._velocity("published_cmd", msg), 20)
        self.node.create_timer(1.0, self._line_sources)
        self.node.create_subscription(String, "/line_follow/status", lambda msg: self._text("line_status", msg.data), 20)
        self.node.create_subscription(String, "/motion/result", lambda msg: self._text("motion_result", msg.data), 10)
        self.node.create_subscription(String, "/manipulator/result", lambda msg: self._text("manipulator_result", msg.data), 10)
        # B2：任务层段进度（JSON：当前段/阶段/作业计数/授权来源/路线错误）。
        # 网页用它显示「车现在在第几段、下一步是什么」，这是无人干预自主完赛的观察窗。
        self.node.create_subscription(String, "/mission/route", lambda msg: self._text("mission_route", msg.data), 10)
        try:
            from robogame_interfaces.msg import CubeDetectionArray
        except ImportError:
            CubeDetectionArray = None
        if CubeDetectionArray is not None:
            self.node.create_subscription(CubeDetectionArray, "/cubes", self._cubes, 10)
        self.clients = {
            "lift": self.node.create_client(SetLiftHeight, "/lift/set_height"),
            "grab": self.node.create_client(ExecuteMechanism, "/gripper/grab"),
            "release": self.node.create_client(ExecuteMechanism, "/gripper/release"),
            "home": self.node.create_client(ExecuteMechanism, "/mechanism/home"),
            "stop": self.node.create_client(ExecuteMechanism, "/mechanism/stop"),
            "chassis_stop": self.node.create_client(ExecuteMechanism, "/chassis/stop"),
        }
        self.arm_interface_missing = SetArmJoint is None
        if SetArmJoint is not None:
            self.clients["arm_set_joint"] = self.node.create_client(SetArmJoint, "/arm/set_joint")
        self._times: dict[str, list[float]] = {}
        # 巡线链路统计：帧到达时刻、mcu_tick 连续性异常计数、无效帧累计。
        self._line_samples: list[float] = []
        self._line_last_tick: int | None = None
        self._line_tick_anomalies = 0
        self._line_invalid_frames = 0
        self._line_boot_seen: int | None = None
        self._line_last_rx: float | None = None
        self.thread = threading.Thread(target=rclpy.spin, args=(self.node,), daemon=True, name="dashboard-ros")
        self.thread.start()

    def _status(self, msg) -> None:
        self.controller.update_safety(SafetyStatus(
            received_at=time.monotonic(), communication_ok=bool(msg.communication_ok),
            emergency_stop=bool(msg.emergency_stop), physical_start=bool(msg.physical_start),
            mechanism_fault=bool(msg.mechanism_fault), battery_voltage=float(msg.battery_voltage),
            gripper_closed=bool(msg.gripper_closed), cube_present=bool(msg.cube_present),
            calibrating=bool(msg.calibrating), imu_valid=bool(msg.imu_valid), boot_id=int(msg.boot_id),
            error_code=int(msg.error_code), detail=str(msg.detail),
        ))

    def _rate(self, name: str, _msg) -> None:
        now = time.monotonic()
        values = self._times.setdefault(name, [])
        values.append(now)
        values[:] = [value for value in values if now - value <= 2.0]
        hz = 0.0 if len(values) < 2 else (len(values) - 1) / (values[-1] - values[0])
        self.controller.update_telemetry({f"{name}_hz": round(hz, 1), f"{name}_age_s": 0.0})

    def _pose(self, msg) -> None:
        self._rate("pose", msg)
        q = msg.pose.pose.orientation
        yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y*q.y + q.z*q.z))
        self.controller.update_telemetry({
            "pose_yaw": yaw,
            "distance_pose_x": float(msg.pose.pose.position.x),
            "distance_pose_y": float(msg.pose.pose.position.y),
            "odom_feedback_valid": 0 <= msg.twist.covariance[0] < 1000000.0,
            "pose_x": round(float(msg.pose.pose.position.x), 4),
            "pose_y": round(float(msg.pose.pose.position.y), 4),
            "velocity_vx": round(float(msg.twist.twist.linear.x), 4),
            "velocity_vy": round(float(msg.twist.twist.linear.y), 4),
            "velocity_wz": round(float(msg.twist.twist.angular.z), 4),
        })

    def _ensure_line_state(self) -> None:
        """巡线统计状态的惰性初始化。

        不单独依赖 __init__：既有测试会用 `RosFacade.__new__` 绕过构造，
        只为测某一个回调，惰性初始化让两种情况都安全。
        """
        if not hasattr(self, "_line_samples"):
            self._line_samples: list[float] = []
            self._line_last_tick: int | None = None
            self._line_tick_anomalies = 0
            self._line_invalid_frames = 0
            self._line_last_rx: float | None = None

    @staticmethod
    def tick_is_anomalous(previous: int | None, current: int) -> bool:
        """mcu_tick_ms 是 u32 单调计数；判断这一帧是否说明中间丢帧。

        与 robot_bridge 的做法保持一致（同一常量、同一方向）：
        - 倒退（按 u32 环绕比较）→ 异常
        - 前进超过 LINE_TICK_GAP_MS（固件 0x14 为 20ms 一帧，留 4 倍余量）→ 异常
        两者都说明**上游有帧没有到达本面板**。
        """
        if previous is None:
            return False
        if current < previous:
            return True
        return (current - previous) > LINE_TICK_GAP_MS

    def _line(self, msg) -> None:
        self._ensure_line_state()
        now = time.monotonic()
        tick = int(msg.mcu_tick_ms)
        if self.tick_is_anomalous(self._line_last_tick, tick):
            self._line_tick_anomalies += 1
        self._line_last_tick = tick

        if not bool(msg.analog_valid):
            self._line_invalid_frames += 1

        samples = self._line_samples
        samples.append(now)
        samples[:] = [value for value in samples if now - value <= 2.0]
        # 同一毫秒内可能到达多帧（缓冲积压后一次性派发），窗口为 0 时不能相除。
        window = samples[-1] - samples[0]
        hz = 0.0 if len(samples) < 2 or window <= 0 else (len(samples) - 1) / window

        self._line_last_rx = now
        # 标定采集：只有面板处于采集中才记录，平时不占内存。
        target = self.controller.line_calibration.active_target()
        if target is not None:
            self.controller.line_calibration.capture(
                target, [int(v) for v in msg.channels], now=now
            )
        self.controller.update_telemetry({
            "line_sensor": [int(v) for v in msg.channels],
            "line_sensor_age_s": 0.0,
            "line_analog_valid": bool(msg.analog_valid),
            "line_mcu_tick_ms": tick,
            "line_hz": round(hz, 1),
            "line_tick_anomalies": self._line_tick_anomalies,
            "line_invalid_frames": self._line_invalid_frames,
        })
        self._publish_line_chain()

    def _publish_line_chain(self) -> None:
        """把整条巡线链的观测结论算好交给网页（网页只负责显示，不再自己判定）。"""
        self._ensure_line_state()
        state = getattr(self.controller, "state", None)
        telemetry = dict(getattr(state, "telemetry", None) or {})
        self.controller.update_telemetry({
            "line_chain": line_chain_health(
                now=time.monotonic(),
                last_frame_at=self._line_last_rx,
                telemetry=telemetry,
                line_hz=self._line_hz(),
                tick_anomalies=self._line_tick_anomalies,
                sample_times=list(self._line_samples),
            )
        })

    def _line_hz(self) -> float:
        self._ensure_line_state()
        samples = self._line_samples
        if len(samples) < 2:
            return 0.0
        window = samples[-1] - samples[0]
        return 0.0 if window <= 0 else (len(samples) - 1) / window

    def _legacy_line(self, msg) -> None:
        # 旧消息没有 analog_valid/MCU 时间戳，不能伪造新版原始量或有效性。
        self.controller.update_telemetry({"line_legacy_values": [float(v) for v in msg.data],
                                          "line_interface": "legacy"})

    def _velocity(self, key, msg) -> None:
        self.controller.update_telemetry({key: {
            "vx": float(msg.linear.x), "vy": float(msg.linear.y),
            "wz": float(msg.angular.z),
        }})

    def _cubes(self, msg) -> None:
        """最新一帧方块检测：取置信度最高的一条，供抓取对准面板换算。

        检测为空也要上报（count=0），否则面板无法区分「没数据」和「看得见但没有方块」。
        """
        detections = list(getattr(msg, "detections", []) or [])
        values = {"cube_count": len(detections)}
        if detections:
            best = max(detections, key=lambda item: float(item.confidence))
            values.update({
                "cube_forward_m": float(best.distance_m),
                "cube_lateral_right_m": float(best.lateral_m),
                "cube_confidence": float(best.confidence),
                "cube_color": "orange" if int(best.color) == 0 else "purple",
            })
        self.controller.update_telemetry(values)

    def _line_sources(self) -> None:
        self.controller.update_telemetry({
            "line_sources": sorted({info.node_name for info in
                self.node.get_publishers_info_by_topic("/line_sensor")}),
            "cmd_sources": sorted({info.node_name for info in
                self.node.get_publishers_info_by_topic("/cmd_vel")}),
        })
        # 1Hz 心跳：即使 /line_sensor 停了，也要让网页看到年龄增长与断点结论。
        self._publish_line_chain()

    @staticmethod
    def parse_line_diag(text: str) -> dict | None:
        """从 /line_follow/status 里取出巡线控制器发布的逐层门控证据。

        格式：`state=... dev=... #diag#{json}`。取不到就返回 None——
        旧版控制器（不发布 #diag#）仍然可用，只是网页少一层细节。
        """
        marker = "#diag#"
        if not isinstance(text, str) or marker not in text:
            return None
        try:
            payload = json.loads(text.split(marker, 1)[1].strip())
        except (ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _text(self, key: str, value: str) -> None:
        self.controller.update_telemetry({key: value, f"{key}_age_s": 0.0})
        if key == "line_status":
            diag = self.parse_line_diag(value)
            if diag is not None:
                self.controller.update_telemetry({"line_diag": diag})
            self._publish_line_chain()

    def publishers(self) -> list[str]:
        infos = self.node.get_publishers_info_by_topic("/cmd_vel")
        return sorted({info.node_name for info in infos if info.node_name != self.node.get_name()})

    def drive(self, vx: float, vy: float, wz: float) -> None:
        msg = self.Twist()
        msg.linear.x, msg.linear.y, msg.angular.z = vx, vy, wz
        self.cmd_pub.publish(msg)

    def service_status(self) -> dict:
        return {name: client.service_is_ready() for name, client in self.clients.items()}

    def mechanism(self, action: str, timeout_s: float = 3.0, height_m: float | None = None) -> dict:
        client = self.clients[action]
        if not client.wait_for_service(timeout_sec=0.5):
            return {"success": False, "error_code": 9003, "detail": f"service for {action} unavailable"}
        if action == "lift":
            request = self.SetLiftHeight.Request()
            request.height_m = float(height_m)
        else:
            request = self.ExecuteMechanism.Request()
            request.command = {"grab": "GRAB", "release": "RELEASE", "home": "HOME", "stop": "STOP", "chassis_stop": "STOP"}[action]
        request.timeout_s = float(timeout_s)
        future = client.call_async(request)
        done = threading.Event()
        future.add_done_callback(lambda _future: done.set())
        if not done.wait(timeout_s + 0.5):
            return {"success": False, "error_code": 2002, "detail": f"{action} service timeout"}
        try:
            result = future.result()
            return {"success": bool(result.success), "error_code": int(result.error_code), "duration_s": float(result.duration_s), "detail": str(result.detail)}
        except Exception as exc:
            return {"success": False, "error_code": 9003, "detail": str(exc)}

    def arm_set_joint(self, joint: int, angle_deg: float, timeout_s: float) -> dict:
        """0x20 ARM_SET：让一个关节转到指定绝对角度。

        ⚠️ 纯开环：服务返回 success 只代表"命令被接受并走完了流程"，
        不代表舵机真的转到了那个角度（机械臂没有位置反馈）。
        """
        client = self.clients.get("arm_set_joint")
        if client is None:
            return {"success": False, "error_code": 9003,
                    "detail": "SetArmJoint 接口未安装：需在树莓派 colcon build robogame_interfaces"}
        if not client.wait_for_service(timeout_sec=0.5):
            return {"success": False, "error_code": 9003, "detail": "/arm/set_joint 服务不可用"}
        request = self.SetArmJoint.Request()
        request.joint = int(joint)
        request.angle_deg = float(angle_deg)
        request.timeout_s = float(timeout_s)
        future = client.call_async(request)
        done = threading.Event()
        future.add_done_callback(lambda _future: done.set())
        if not done.wait(timeout_s + 0.5):
            return {"success": False, "error_code": 2002, "detail": "arm_set_joint 服务超时"}
        try:
            result = future.result()
            return {"success": bool(result.success), "error_code": int(result.error_code),
                    "duration_s": float(result.duration_s), "detail": str(result.detail)}
        except Exception as exc:
            return {"success": False, "error_code": 9003, "detail": str(exc)}

    def line_reading_is_fresh(self, now: float, *, limit_s: float = 0.25) -> dict:
        """标定前的前置检查：必须有新鲜的 /line_sensor，否则采到的是旧值。"""
        self._ensure_line_state()
        if self._line_last_rx is None:
            return {"ok": False, "reason": "尚未收到 /line_sensor，无法标定"}
        age = now - self._line_last_rx
        if age > limit_s:
            return {"ok": False,
                    "reason": f"巡线读数已过期（{age:.2f}s > {limit_s:.2f}s），无法标定"}
        return {"ok": True, "age_s": age}

    def push_line_calibration(self, white_ref: list[float], black_ref: list[float]) -> dict:
        """把标定基准推给巡线节点，当场生效（不需要重启进程）。

        用参数而不是新话题/服务：巡线节点是现成的 ROS 节点，参数服务已经可用；
        改接口会牵动 robogame_interfaces 与重建，代价与收益不成比例。

        传递方式：4 个 double_array（基准值 + 每路偏移）。
        - white_ref_param / black_ref_param：两组基准的全局均值
        - white_offset / black_offset：每路相对均值的偏移
        节点侧还原为逐路基准，从而逐路归一化（8 路模块各路差异常达数百）。
        """
        from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
        from rcl_interfaces.srv import SetParameters

        white = [float(v) for v in white_ref]
        black = [float(v) for v in black_ref]
        white_base = sum(white) / len(white)
        black_base = sum(black) / len(black)
        payload = {
            "white_ref": white_base,
            "black_ref": black_base,
            "white_offset": [round(v - white_base, 2) for v in white],
            "black_offset": [round(v - black_base, 2) for v in black],
        }

        client = self.node.create_client(SetParameters, "/line_follow_controller/set_parameters")
        if not client.wait_for_service(timeout_sec=1.0):
            return {"ok": False, "detail": "巡线节点未运行（/line_follow_controller 参数服务不可用）",
                    "error_code": 9003}
        request = SetParameters.Request()
        request.parameters = [
            Parameter(
                name=name,
                value=ParameterValue(
                    type=(ParameterType.DOUBLE_ARRAY if isinstance(value, list)
                          else ParameterType.DOUBLE),
                    double_array_value=[float(v) for v in value] if isinstance(value, list) else [],
                    double_value=0.0 if isinstance(value, list) else float(value),
                ),
            )
            for name, value in payload.items()
        ]
        future = client.call_async(request)
        done = threading.Event()
        future.add_done_callback(lambda _future: done.set())
        if not done.wait(2.0):
            return {"ok": False, "detail": "推送标定参数超时", "error_code": 2002}
        try:
            response = future.result()
        except Exception as exc:
            return {"ok": False, "detail": f"推送标定参数失败：{exc}", "error_code": 9003}
        rejected = [r.reason for r in response.results if not r.successful]
        if rejected:
            return {"ok": False, "detail": "巡线节点拒绝了标定参数：" + "；".join(rejected),
                    "error_code": 4}
        return {
            "ok": True,
            "pushed": {name: (round(value, 1) if not isinstance(value, list)
                              else [round(v, 1) for v in value])
                       for name, value in payload.items()},
            "white_ref": [float(v) for v in white],
            "black_ref": [float(v) for v in black],
            "detail": f"已推送到巡线节点：white_ref≈{white_base:.0f}、black_ref≈{black_base:.0f}",
        }

    def close(self) -> None:
        self.drive(0.0, 0.0, 0.0)
        self.node.destroy_node()
        self.rclpy.shutdown()
        self.thread.join(timeout=2.0)


class DashboardController:
    def __init__(self, console: FieldConsole, archive: SessionArchive, hub: EventHub, loop: asyncio.AbstractEventLoop, *, max_drive_speed: float = 0.3, calibration_file: str = "") -> None:
        if not math.isfinite(max_drive_speed) or not 0.02 <= max_drive_speed <= 0.8:
            raise ValueError("网页最大前后速度必须在 0.02～0.80 m/s 内")
        # B4：标定落盘路径（空串 = 用 robogame_core 里的默认路径）
        self.calibration_file = calibration_file
        self.max_drive_speed = max_drive_speed
        self.stopping = False
        self.control_epoch = 0
        self.console, self.archive, self.hub, self.loop = console, archive, hub, loop
        self.state = DashboardState()
        self.lock = threading.RLock()
        self.telemetry_times: dict[str, float] = {}
        self.ros: RosFacade | None = None
        self.ros_error: str | None = None
        self.mechanism_busy = False
        self.distance_trial: DistanceTrial | None = None
        self.distance_result: dict = {"state": "未开始"}
        # 里程计标定：一次完整定距试验 = 一个候选，操作员补录尺量值后成为一条样本
        self.odom_trial_candidate: dict | None = None
        self.odom_trials: list[OdomTrial] = []
        # 黑白标定状态（纯逻辑在 field_dashboard_core.LineCalibration）
        self.line_calibration = LineCalibration()
        self.line_calibration_result: dict | None = None

    def start_ros(self) -> None:
        try:
            self.ros = RosFacade(self)
        except Exception as exc:
            self.ros_error = f"ROS unavailable: {exc}"
            self.record({"type": "diagnostic", "level": "error", "message": self.ros_error})

    def record(self, event: dict, *, telemetry: bool = False) -> None:
        record = self.archive.append("telemetry" if telemetry else "event", event)
        self.hub.publish(record)

    async def process_event(self, event: dict) -> None:
        if event.get("type") == "log":
            self.archive.append_raw(event["name"], event["line"])
        self.record(event)
        if event.get("type") == "process" and event.get("name") == "bridge" and event.get("state") in {"exited", "stopped"}:
            self.zero_and_release("bridge stopped")

    def update_safety(self, status: SafetyStatus) -> None:
        with self.lock:
            self.state.safety = status
        self.record({"type": "telemetry", "topic": "/robot/status", "value": status.__dict__}, telemetry=True)

    def update_telemetry(self, values: dict) -> None:
        with self.lock:
            self.state.telemetry.update(values)
            now = time.monotonic()
            for key in values:
                if not key.endswith("_age_s"):
                    self.telemetry_times[key] = now
        self.record({"type": "telemetry", "values": values}, telemetry=True)

    def grasp_panel(self) -> dict:
        """抓取对准面板：最新检测 -> 接近坐标系误差 -> 底盘命令（与真车同一份纯函数）。

        只解释证据，不下发任何命令；没有检测时明确说"没有数据"，不拿旧值冒充现场。
        """
        with self.lock:
            telemetry = dict(self.state.telemetry)
        panel: dict = {
            "camera_yaw_offset_rad": float(GRASP_DEFAULTS["camera_yaw_offset_rad"]),
            "target_distance_m": float(GRASP_DEFAULTS["target_distance_m"]),
            "cross_tolerance_m": float(GRASP_DEFAULTS["cross_tolerance_m"]),
        }
        if "cube_count" not in telemetry:
            return panel | {
                "source": "no_detection",
                "message": "尚未收到 /cubes 检测：需要相机与 cube_perception；可先跑下方对准仿真",
            }
        panel["detections"] = telemetry.get("cube_count")
        if "cube_forward_m" not in telemetry:
            # 话题在更新但这一帧没有方块：是"没看见"，不是"没数据"，两者不能混为一谈
            return panel | {
                "source": "cubes",
                "message": "本帧未检测到方块（话题在更新）：检查方块是否在视野内、颜色阈值与工作距离",
            }
        panel.update({
            "source": "cubes",
            "forward_m": round(float(telemetry["cube_forward_m"]), 4),
            "lateral_right_m": round(float(telemetry.get("cube_lateral_right_m", 0.0)), 4),
            "confidence": telemetry.get("cube_confidence"),
            "color": telemetry.get("cube_color"),
            "detections": telemetry.get("cube_count"),
        })
        panel.update(evaluate_detection(
            float(telemetry["cube_forward_m"]), float(telemetry.get("cube_lateral_right_m", 0.0))
        ))
        return panel

    def snapshot(self) -> dict:
        with self.lock:
            result = self.state.snapshot(time.monotonic())
            now = time.monotonic()
            for key, received_at in self.telemetry_times.items():
                result["telemetry"][f"{key}_age_s"] = round(max(0.0, now - received_at), 3)
        result.update({"processes": self.console.snapshot(), "ros_error": self.ros_error, "session_path": str(self.archive.path)})
        result["mechanism_busy"] = self.mechanism_busy
        result["distance_result"] = dict(self.distance_result)
        # 里程计标定（B3 现场项）：候选 + 已记录样本的汇总结论
        result["odom_trial_candidate"] = (
            None if self.odom_trial_candidate is None else dict(self.odom_trial_candidate)
        )
        result["odom_calibration"] = analyze_trials(self.odom_trials)
        result["drive_limits"] = {"max_v": self.max_drive_speed, "max_vy": min(self.max_drive_speed, 0.4), "max_w": 0.8}
        result["mechanism_services"] = self.ros.service_status() if self.ros else {}
        result["grasp"] = self.grasp_panel()
        result["line_calibration"] = self.line_calibration_snapshot()
        # B1：全流程路线只读摘要（静态数据，缓存；自检不过时 available=false + 原因）
        result["route"] = route_payload()
        return result

    def line_calibration_snapshot(self) -> dict:
        """标定面板需要的全部状态：进度、判定、已推送结果、下一步提示。"""
        now = time.monotonic()
        durations = self.line_calibration.durations(now)
        verdict = self.line_calibration.verdict(now)
        return {
            "capturing": self.line_calibration.active_target(),
            "white_frames": len(self.line_calibration.white_samples),
            "black_frames": len(self.line_calibration.black_samples),
            "white_seconds": round(durations["white"], 2),
            "black_seconds": round(durations["black"], 2),
            "min_seconds": self.line_calibration.min_seconds,
            "ready": bool(verdict["ok"]),
            "verdict": verdict,
            "applied": self.line_calibration_result,
            "next_step": self._calibration_next_step(verdict),
        }

    def _calibration_next_step(self, verdict: dict) -> str:
        if self.line_calibration.active_target() == "white":
            return "正在采集白底基准：保持不动，松开按钮结束这一组"
        if self.line_calibration.active_target() == "black":
            return "正在采集黑线基准：保持不动，松开按钮结束这一组"
        if not verdict["ok"]:
            return (verdict["problems"] or ["请完成白底与黑线两组标定"])[0]
        if self.line_calibration_result and self.line_calibration_result.get("state") == "已生效":
            return "标定已生效；可以启动巡线，并观察链路表 ② 与偏差方向"
        return "两组基准已采齐，点「应用标定」推送到巡线节点"

    def _require_ros(self) -> RosFacade:
        if self.ros is None:
            raise ValueError(self.ros_error or "ROS 尚未就绪")
        return self.ros

    async def action(self, path: str, body: dict) -> dict:
        parts = [p for p in path.split("/") if p]
        epoch = self.control_epoch
        if self.stopping:
            raise ValueError("正在执行全部停止，请稍后操作")
        if self.mechanism_busy and (
            path in {"/api/control/manual/acquire", "/api/line/start"}
            or (len(parts) == 4 and parts[:2] == ["api", "process"]
                and parts[2] in {"motion", "arm", "line"} and parts[3] == "start")
        ):
            raise ValueError("机构动作执行中，不能启动底盘运动")
        if path == "/api/grasp/simulate":
            # 纯计算：不碰硬件、不发布速度，任何模式下都能跑（网页上直接看收敛过程）
            return simulate_grasp(**{key: float(value) for key, value in body.items()})
        if path == "/api/distance/start":
            ros = self._require_ros()
            with self.lock:
                if self.state.mode != "OBSERVE" or self.mechanism_busy:
                    raise ValueError("请先释放手动控制、停止巡线并等待机构动作结束")
                now = time.monotonic()
                trial = DistanceTrial(self._distance_pose(now), float(body.get("distance_m", 0.5)),
                                      float(body.get("speed_mps", 0.05)), now, now,
                                      uuid.uuid4().hex, self.state.safety.boot_id)
                self.state.mode = "DISTANCE"
                self.odom_trial_candidate = None  # 新的一次试验：旧候选作废
            try:
                for name in ("motion", "arm", "line"):
                    if name in self.console.items and self.console.items[name].running:
                        await self.console.stop(name)
                await asyncio.sleep(0.25)
                with self.lock:
                    self._check_control_epoch(epoch)
                    if self.state.mode != "DISTANCE":
                        raise ValueError("定距准备已取消")
                    conflicts = ros.publishers()
                    if conflicts:
                        raise ValueError("其他速度发布者尚未退出，请稍后重试：" + ", ".join(conflicts))
                    now = time.monotonic()
                    trial.start = self._distance_pose(now)
                    trial.boot_id = self.state.safety.boot_id
                    trial.started_at = trial.heartbeat_at = now
                    trial.last_progress_at = now
                    self.distance_trial = trial
                    self.distance_result = {"state": "执行中", "target_m": trial.distance,
                                            "progress_m": 0.0, "lateral_m": 0.0}
                self.record({"type": "control", "action": "distance_start", "distance_m": trial.distance})
                return {"ok": True, "token": trial.token}
            except Exception:
                self.zero_and_release("定距启动失败")
                raise
        if path == "/api/distance/heartbeat":
            with self.lock:
                if self.distance_trial is None or body.get("token") != self.distance_trial.token:
                    raise ValueError("定距任务已结束或不属于当前页面")
                self.distance_trial.heartbeat_at = time.monotonic()
            return {"ok": True}
        if path == "/api/distance/stop":
            if self.state.mode == "DISTANCE":
                self.zero_and_release("用户停止定距")
            return {"ok": True}
        if path == "/api/odom/record":
            # 把本次定距结果记成一条样本。**尺量可以留空**：这样「里程计自洽性」
            # （里程计等效速度 vs 命令速度，不用尺子）也能累积样本；
            # 只填了尺量才能算「标度」。
            measured = body.get("measured_m")
            if measured is not None and str(measured).strip() == "":
                measured = None
            with self.lock:
                if self.odom_trial_candidate is None:
                    raise ValueError(
                        "没有可记录的定距结果：先在「底盘定距测试」里完整跑一次"
                        "（中途停止/超时的不算样本）"
                    )
                try:
                    trial = trial_from_result(
                        self.odom_trial_candidate,
                        measured_m=None if measured is None else float(measured),
                        note=str(body.get("note", ""))[:120],
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"尺量位移无效：{exc}") from exc
                self.odom_trials.append(trial)
                analysis = analyze_trials(self.odom_trials)
            self.record({"type": "control", "action": "odom_record",
                         "trial": trial.as_dict(), "analysis": analysis})
            return {"ok": True, "analysis": analysis}
        if path == "/api/odom/reset":
            with self.lock:
                cleared = [trial.as_dict() for trial in self.odom_trials]
                self.odom_trials = []
            self.record({"type": "control", "action": "odom_reset", "cleared": cleared})
            return {"ok": True, "cleared": len(cleared)}
        if self.state.mode == "DISTANCE" and (
            path in {"/api/control/manual/acquire", "/api/line/start", "/api/line/stop"}
            or (len(parts) == 4 and parts[:2] == ["api", "process"]
                and parts[2] in {"motion", "arm", "line"})
        ):
            raise ValueError("定距执行中，请先停止定距")
        if len(parts) == 4 and parts[:2] == ["api", "process"] and parts[3] in {"start", "stop"}:
            name = parts[2]
            if parts[3] == "start":
                with self.lock:
                    mode = self.state.mode
                if name in {"motion", "arm", "line"} and mode == "MANUAL":
                    raise ValueError("手动接管期间不能启动其他运动来源")
                if name in {"motion", "arm"} and mode == "LINE":
                    raise ValueError("巡线期间不能启动导航或机械臂对准")
                if name == "line":
                    blockers = action_blockers(self.state.safety, time.monotonic())
                    if blockers:
                        raise ValueError(blockers[0]["message"])
                    for other in ("motion", "arm"):
                        if other in self.console.items and self.console.items[other].running:
                            await self.console.stop(other)
                    with self.lock:
                        self._check_control_epoch(epoch)
                        self.state.mode = "LINE"
                self._check_control_epoch(epoch)
                await self.console.start(name)
                if epoch != self.control_epoch:
                    await self.console.stop(name)
                    raise ValueError("启动期间收到停止请求，已取消启动")
            else:
                if name == "bridge":
                    self.zero_and_release("bridge stop requested")
                await self.console.stop(name)
                if name == "line":
                    with self.lock:
                        self.state.mode = "OBSERVE"
                    if self.ros:
                        self.ros.drive(0.0, 0.0, 0.0)
            return {"ok": True}
        if path == "/api/control/manual/acquire":
            for name in ("motion", "arm", "line"):
                if name in self.console.items and self.console.items[name].running:
                    await self.console.stop(name)
            await asyncio.sleep(0.25)
            ros = self._require_ros()
            with self.lock:
                self._check_control_epoch(epoch)
                if self.state.mode == "DISTANCE":
                    raise ValueError("定距执行中，请先停止定距")
                self.state.acquire_manual(now=time.monotonic(), conflicting_publishers=ros.publishers())
            ros.drive(0.0, 0.0, 0.0)
            self.record({"type": "control", "action": "manual_acquire"})
            return {"ok": True}
        if path == "/api/control/manual/release":
            self.zero_and_release("manual release")
            return {"ok": True}
        if path == "/api/control/drive":
            ros = self._require_ros()
            vx, vy, wz = clamp_drive(float(body.get("vx", 0)), float(body.get("vy", 0)), float(body.get("wz", 0)), max_v=self.max_drive_speed, max_vy=min(self.max_drive_speed, 0.4))
            with self.lock:
                self.state.accept_drive(sequence=int(body["sequence"]), now=time.monotonic())
            ros.drive(vx, vy, wz)
            return {"ok": True, "applied": {"vx": vx, "vy": vy, "wz": wz}}
        if path == "/api/control/stop-all":
            self.stopping = True
            try:
                self.zero_and_release("stop all")
                stopped = {}
                for name in ("motion", "arm", "line"):
                    if name in self.console.items and self.console.items[name].running:
                        try:
                            await self.console.stop(name)
                            stopped[name] = {"success": True}
                        except Exception as exc:
                            stopped[name] = {"success": False, "detail": str(exc)}
                self.zero_and_release("motion sources stopped")
                async def stop_service(action):
                    try:
                        return await asyncio.to_thread(self._require_ros().mechanism, action, 1.0)
                    except Exception as exc:
                        return {"success": False, "detail": str(exc)}
                chassis, mechanism = await asyncio.gather(stop_service("chassis_stop"), stop_service("stop"))
                result = {"ok": chassis["success"] and mechanism["success"] and all(r["success"] for r in stopped.values()),
                          "chassis": chassis, "mechanism": mechanism, "processes": stopped}
                self.record({"type": "control", "action": "stop_all", "result": result})
                return result
            finally:
                self.stopping = False
        if len(parts) == 3 and parts[:2] == ["api", "mechanism"] and parts[2] in {"grab", "release", "home", "stop", "lift"}:
            ros = self._require_ros()
            action = parts[2]
            height = None
            if action == "lift":
                try:
                    height = float(body["height_m"])
                except (KeyError, ValueError, TypeError):
                    raise ValueError("请填写升降高度（米）")
                if not math.isfinite(height) or height < 0:
                    raise ValueError("高度必须是有限的非负数；实际行程需按设备确认")
            if self.mechanism_busy and action != "stop":
                raise ValueError("机构动作执行中，请等待结果或点击机构停止")
            if parts[2] != "stop":
                with self.lock:
                    if self.state.mode != "OBSERVE":
                        raise ValueError("底盘运动模式中不能启动机构动作；请先停止或释放控制")
                blockers = action_blockers(self.state.safety, time.monotonic())
                if blockers:
                    raise ValueError(blockers[0]["message"])
            running = {"action": action, "state": "执行中", "started_at": time.time(), "height_m": height}
            self.state.last_mechanism_result = running
            if action != "stop":
                self.mechanism_busy = True
            self.record({"type": "mechanism", **running})
            try:
                args = (action, 5.0 if action == "home" else 3.0)
                result = await asyncio.to_thread(ros.mechanism, *args, **({"height_m": height} if action == "lift" else {}))
            except Exception as exc:
                result = {"success": False, "detail": str(exc), "error_code": 9003}
            finally:
                if action != "stop":
                    self.mechanism_busy = False
            result = {**running, **result, "finished_at": time.time(), "state": "成功" if result["success"] else "超时" if result.get("error_code") == 2002 else "失败"}
            with self.lock:
                if self.state.last_mechanism_result is running:
                    self.state.last_mechanism_result = result
            self.record({"type": "mechanism", "action": parts[2], "result": result})
            return {"ok": result["success"], "result": result}
        if path in {"/api/arm/set_joint", "/api/arm/selftest"}:
            return await self._arm_action(path, body)
        if path == "/api/line/calibrate/start":
            return self._calibrate_start(body)
        if path == "/api/line/calibrate/capture":
            return await self._calibrate_capture(body)
        if path == "/api/line/calibrate/apply":
            return await self._calibrate_apply(body)
        if path == "/api/line/calibrate/reset":
            self.line_calibration.reset()
            self.line_calibration_result = None
            self.record({"type": "control", "action": "line_calibrate_reset"})
            return {"ok": True}
        if path in {"/api/line/start", "/api/line/stop"}:
            if path.endswith("start"):
                if self.mechanism_busy:
                    raise ValueError("机构动作执行中，不能启动巡线")
                blockers = action_blockers(self.state.safety, time.monotonic())
                if blockers:
                    raise ValueError(blockers[0]["message"])
                self.zero_and_release("line start")
                epoch = self.control_epoch
                for name in ("motion", "arm"):
                    if name in self.console.items and self.console.items[name].running:
                        await self.console.stop(name)
                self._check_control_epoch(epoch)
                await self.console.start("line")
                if epoch != self.control_epoch:
                    await self.console.stop("line")
                    raise ValueError("启动期间收到停止请求，已取消巡线")
                with self.lock:
                    self.state.mode = "LINE"
            else:
                await self.console.stop("line")
                with self.lock:
                    self.state.mode = "OBSERVE"
                self._require_ros().drive(0.0, 0.0, 0.0)
            return {"ok": True}
        raise ValueError(f"unknown action: {path}")

    # ---- 机械臂 ARM_SET 与联调自检 ---------------------------------------
    # 逐条检查的逻辑主体在 tools/arm_selftest.py（可脱离 ROS 单测），
    # 这里只做接线、互斥与前置条件。

    def _selftest_runner(self) -> ArmSelftest:
        # self.ros 可能为 None（ROS 未就绪）：这时自检会返回一张"ROS 客户端未就绪"
        # 的失败表，而不是抛异常，页面能把原因显示出来。
        ros = self.ros

        async def wait_flag(attr: str, want: bool, timeout_s: float):
            deadline = time.monotonic() + timeout_s
            actual = None
            while time.monotonic() < deadline:
                with self.lock:
                    actual = bool(getattr(self.state.safety, attr))
                if actual is want:
                    return True, str(actual)
                await asyncio.sleep(0.05)
            return False, str(actual)

        def claim_busy() -> bool:
            if self.mechanism_busy:
                return False
            self.mechanism_busy = True
            return True

        return ArmSelftest(
            ros=ros,
            safety=lambda: self.state.safety,
            mode=lambda: self.state.mode,
            wait_flag=wait_flag,
            claim_busy=claim_busy,
            release_busy=lambda: setattr(self, "mechanism_busy", False),
            now=time.monotonic,
        )

    async def _arm_action(self, path: str, body: dict) -> dict:
        if path == "/api/arm/selftest":
            stage = str(body.get("stage", ""))
            if not stage:
                # 只列清单、不执行：让网页从后端拿阶段列表，避免两处硬编码。
                return {"ok": True, "stages": stage_names(), "steps": [], "passed": False}
            result = await self._selftest_runner().run(stage)
            self.record({"type": "arm_selftest", "stage": stage,
                         "passed": result["passed"], "steps": result["steps"]})
            # HTTP 层始终算“请求成功”，判定放在 passed 里，
            # 这样页面能把失败的那几步照样渲染出来。
            return {"ok": True, **result}

        try:
            joint = int(body["joint"])
            angle_deg = float(body["angle_deg"])
        except (KeyError, ValueError, TypeError):
            raise ValueError("请填写关节编号和角度（度）")
        if not 0 <= joint <= 4:
            raise ValueError("关节编号必须是 0～4（0=腰/云盘, 1=肩, 2=肘, 3=腕, 4=爪）")
        if not math.isfinite(angle_deg):
            raise ValueError("角度必须是有限数")
        timeout_s = float(body.get("timeout_s", 8.0))
        if not math.isfinite(timeout_s) or timeout_s <= 0.0:
            raise ValueError("timeout_s 必须是正的有限数")

        with self.lock:
            if self.state.mode != "OBSERVE":
                raise ValueError("底盘运动模式中不能下发机械臂命令；请先停止或释放控制（C-4）")
        if self.mechanism_busy:
            raise ValueError("机构动作执行中，请等待结果或点击机构停止")
        blockers = action_blockers(self.state.safety, time.monotonic())
        if blockers:
            raise ValueError(blockers[0]["message"])

        self.mechanism_busy = True
        try:
            result = await asyncio.to_thread(
                self._require_ros().arm_set_joint, joint, angle_deg, timeout_s
            )
        except Exception as exc:
            result = {"success": False, "error_code": 9003, "detail": str(exc)}
        finally:
            self.mechanism_busy = False
        self.record({"type": "arm", "action": "set_joint", "joint": joint,
                     "angle_deg": angle_deg, "result": result})
        return {"ok": bool(result.get("success")), "result": result}

    def _calibrate_start(self, body: dict) -> dict:
        """开始一次基准采集（白底或黑线）。必须先把车停稳，读数必须是新鲜的。"""
        target = str(body.get("target", ""))
        if target not in {"white", "black"}:
            raise ValueError("标定目标必须是 white 或 black")
        ros = self._require_ros()
        fresh = ros.line_reading_is_fresh(time.monotonic())
        if not fresh["ok"]:
            raise ValueError(fresh["reason"])
        if self.state.mode == "MANUAL":
            raise ValueError("手动接管期间不能标定，请先释放控制")
        self.line_calibration.begin_capture(target)
        self.line_calibration_result = None
        label = "白底" if target == "white" else "黑线"
        self.record({"type": "control", "action": "line_calibrate_start", "target": target})
        return {"ok": True, "target": target,
                "detail": f"正在采集{label}基准，请保持不动并把车压稳"}

    async def _calibrate_capture(self, body: dict) -> dict:
        """结束采集并给出这一组的判定（还没提交参数）。"""
        target = str(body.get("target", ""))
        if self.line_calibration.active_target() != target:
            raise ValueError("当前没有在采集这一组基准")
        self.line_calibration.end_capture()
        now = time.monotonic()
        durations = self.line_calibration.durations(now)
        samples = (self.line_calibration.white_samples if target == "white"
                   else self.line_calibration.black_samples)
        label = "白底" if target == "white" else "黑线"
        if not samples:
            raise ValueError(f"{label}没有采到任何帧：确认 /line_sensor 正在更新")
        if durations[target] < self.line_calibration.min_seconds:
            raise ValueError(
                f"{label}采样时间不足（{durations[target]:.1f}s < "
                f"{self.line_calibration.min_seconds:.1f}s），请按住按钮久一点"
            )
        self.record({"type": "control", "action": "line_calibrate_capture",
                     "target": target, "frames": len(samples)})
        return {"ok": True, "target": target, "frames": len(samples),
                "seconds": round(durations[target], 2),
                "detail": f"{label}已采 {len(samples)} 帧（{durations[target]:.1f}s）"}

    async def _calibrate_apply(self, body: dict) -> dict:
        """校验两组基准 → 推给巡线节点（当场生效）→ **落盘**（上电后仍然有效）。

        落盘这一步是「上电后无人干预」的前提：只推给运行中的节点，断电重启就丢，
        节点回到只有方向意义的默认基准，巡线直接走不起来。写盘失败必须如实报错——
        这正是最危险的一种「看起来成功」。
        """
        now = time.monotonic()
        verdict = self.line_calibration.begin_apply(now)
        ros = self._require_ros()
        pushed = await asyncio.to_thread(
            ros.push_line_calibration, verdict["white_ref"], verdict["black_ref"]
        )
        if not pushed["ok"]:
            self.line_calibration_result = {"state": "失败", **pushed}
            self.record({"type": "control", "action": "line_calibrate_apply",
                         "result": pushed})
            raise ValueError(pushed["detail"])
        try:
            saved_to = self._save_calibration(
                pushed["white_ref"], pushed["black_ref"]
            )
        except Exception as exc:
            self.line_calibration_result = {"state": "已推送未落盘", **verdict, **pushed,
                                            "save_error": str(exc)}
            self.record({"type": "control", "action": "line_calibrate_apply",
                         "result": self.line_calibration_result})
            raise ValueError(
                f"已推送给节点，但**写盘失败**：{exc}——重启后标定会丢失，上电自主会失效"
            )
        self.line_calibration_result = {"state": "已生效并落盘", "saved_to": saved_to,
                                        **verdict, **pushed}
        self.controller_calibration_note = pushed.get("detail", "")
        self.record({"type": "control", "action": "line_calibrate_apply",
                     "result": self.line_calibration_result})
        return {"ok": True, "saved_to": saved_to, "result": self.line_calibration_result}

    def _save_calibration(self, white_ref, black_ref) -> str:
        """把标定写到盘上（路径来自 --calibration-file，空则用默认路径）。"""
        from robogame_core.line_calibration import DEFAULT_CALIBRATION_FILE, LineCalibration

        calibration = LineCalibration(
            white_ref=tuple(float(v) for v in white_ref),
            black_ref=tuple(float(v) for v in black_ref),
            source="网页面板标定",
        )
        target = self.calibration_file or DEFAULT_CALIBRATION_FILE
        saved = calibration.save(target)
        self.calibration_saved = str(saved)
        return str(saved)

    def zero_and_release(self, reason: str) -> None:
        with self.lock:
            self.control_epoch += 1
            if self.distance_trial is not None:
                self.distance_result.update(state="已停止", detail=reason)
                self.distance_trial = None
                # 中断的定距试验不能当标定样本（可能只走了一半）：
                # 清掉候选，避免操作员把上一次中断的结果当成本次数据记录。
                self.odom_trial_candidate = None
            if self.ros:
                self.ros.drive(0.0, 0.0, 0.0)
            self.state.release_manual()
        self.record({"type": "control", "action": "zero", "reason": reason})

    def _check_control_epoch(self, epoch):
        if epoch != self.control_epoch or self.stopping:
            raise ValueError("准备期间收到停止请求，操作已取消")

    def _distance_pose(self, now: float) -> tuple[float, float, float]:
        blockers = action_blockers(self.state.safety, now)
        if blockers:
            raise ValueError(blockers[0]["message"])
        if now - self.state.safety.received_at > 0.3:
            raise ValueError("定距要求机器人状态在 0.3 秒内更新")
        keys = ("distance_pose_x", "distance_pose_y", "pose_yaw", "odom_feedback_valid")
        if any(k not in self.telemetry_times or now - self.telemetry_times[k] > 0.3 for k in keys):
            raise ValueError("位姿未就绪或超过 0.3 秒未更新，请在进程区启动 localization")
        if self.state.telemetry.get("odom_feedback_valid") is not True:
            raise ValueError("轮速反馈不可用，不能执行定距")
        pose = tuple(float(self.state.telemetry[k]) for k in keys[:3])
        if not all(math.isfinite(v) for v in pose):
            raise ValueError("位姿数值无效")
        return pose

    def distance_tick(self, now: float) -> None:
        with self.lock:
            trial = self.distance_trial
            if trial is None:
                return
            try:
                pose = self._distance_pose(now)
                if trial.boot_id != self.state.safety.boot_id:
                    raise ValueError("下位机重启，定距已终止")
                command, done = trial.step(pose, now)
                self.distance_result.update(progress_m=round(trial.progress, 4), lateral_m=round(trial.lateral, 4))
                self._require_ros().drive(*command)
                if done:
                    self.distance_trial = None
                    self.state.release_manual()
                    elapsed = max(0.0, now - trial.started_at)
                    self.distance_result.update(
                        state="里程计目标已到达",
                        detail="请尺量实际位移并填到「尺量距离」，"
                               "反馈到达不代表实地精度已验证",
                        elapsed_s=round(elapsed, 3),
                        odom_m=round(trial.progress, 4),
                        commanded_speed_mps=trial.speed,
                        odom_speed_mps=round(trial.progress / elapsed, 4) if elapsed > 0 else None,
                    )
                    # 一次完整试验 = 一个标定候选（尺量值随后由网页补录）
                    self.odom_trial_candidate = {
                        "target_m": trial.distance,
                        "odom_m": trial.progress,
                        "elapsed_s": elapsed,
                        "commanded_speed_mps": trial.speed,
                        "completed": True,
                    }
                    self.record({"type": "control", "action": "distance_complete", **self.distance_result})
            except Exception as exc:
                self.zero_and_release(str(exc))

    async def watchdog(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            self.distance_tick(time.monotonic())
            with self.lock:
                expired = self.state.manual_expired(time.monotonic())
            if expired:
                self.zero_and_release("manual heartbeat timeout")


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "RoboGameDashboard/1"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        self.app.record({"type": "http", "message": fmt % args})

    def _json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/snapshot":
            return self._json(self.app.snapshot())
        if path == "/api/events":
            return self._events()
        target = WEB_ROOT / ("index.html" if path == "/" else path.lstrip("/"))
        if not target.is_file() or WEB_ROOT not in target.resolve().parents:
            return self.send_error(404)
        mime = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}.get(target.suffix, "application/octet-stream")
        data = target.read_bytes()
        self.send_response(200); self.send_header("Content-Type", mime + "; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def _events(self):
        client = self.app.hub.subscribe()
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache"); self.send_header("Connection", "keep-alive"); self.end_headers()
        try:
            while True:
                try:
                    event = client.get(timeout=10)
                    payload = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.app.hub.unsubscribe(client)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            path = urlparse(self.path).path
            future = asyncio.run_coroutine_threadsafe(self.app.action(path, body), self.app.loop)
            # 联调自检会顺序等几个机构的完整动作（抓→放→复位），12 s 不够。
            result = future.result(timeout=90 if path == "/api/arm/selftest" else 12)
            self._json(result, 200 if result.get("ok", True) else 409)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json({"ok": False, "error": str(exc)}, 409)
        except Exception as exc:
            self._json({"ok": False, "error": f"request failed: {exc}"}, 500)


async def run(args) -> None:
    loop = asyncio.get_running_loop()
    archive = SessionArchive(args.output)
    hub = EventHub()
    console = FieldConsole(load_specs(args.config), event_sink=None)
    app = DashboardController(console, archive, hub, loop, max_drive_speed=args.max_drive_speed,
                              calibration_file=args.calibration_file)
    console.event_sink = app.process_event
    app.start_ros()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    server.daemon_threads = True  # SSE 浏览器长连接不能阻塞服务重启。
    server.app = app
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="dashboard-http")
    thread.start()
    app.record({"type": "server", "state": "started", "url": f"http://{args.host}:{args.port}"})
    print(f"联调网页已启动: http://{args.host}:{args.port}")
    print(f"会话日志: {archive.path}")
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError): pass
    watchdog = asyncio.create_task(app.watchdog())
    try:
        await stop.wait()
    finally:
        watchdog.cancel()
        app.zero_and_release("server shutdown")
        server.shutdown(); server.server_close()
        if app.ros: app.ros.close()
        await console.close()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--max-drive-speed", type=float, default=0.3,
                        help="网页前后最大速度 m/s；0.8 仅用于已升级固件，横移不超过 0.4，默认兼容旧固件 0.3")
    parser.add_argument("--calibration-file", default="",
                        help="巡线黑白标定落盘路径（面板「应用标定」会写这里；"
                             "节点启动时用同一个路径加载）。留空则用默认路径 ~/robogame_line_calibration.json")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("field_console.json"))
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "field_console")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
