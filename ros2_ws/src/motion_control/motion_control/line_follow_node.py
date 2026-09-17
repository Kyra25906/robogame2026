"""巡线控制器节点（本轮 A：独立节点 + 仲裁门控）。

订阅 `/line_sensor`（LineSensor，8 路原始值 0..4095）与
`/robot/status`（安全门控），调用 `LineFollowRunner` 输出纠偏命令，经
`CmdVelArbiter` 门控后发布：

- `/cmd_vel`          —— 唯一底盘命令出口（授权者 == active_source 才放行）
- `/line_follow/cmd`  —— 纠偏原始值（恒发，联调观察）
- `/line_follow/status` —— 状态字符串（状态机/偏差/失联标志，联调观察）

本轮 A 的定位（见 docs/field/LINE_TELEMETRY_0x14_INTERFACE_ALIGNMENT）：
- 0x14 巡线遥测**两侧均已实现**（固件 `rpi_protocol.c:1552` 周期上送；上位机
  `robogame_core/serial_protocol.py:29` 解码、`robot_bridge/node.py:1096` 路由并
  发布 `/line_sensor`）；本节点当前仍用 mock 数据源（line_sensor_mock）联调；
- 真车验收时把 `/line_sensor` 的发布者从 mock 切到 robot_bridge 解码结果即可，
  本节点与 LineFollowRunner 不用改；
- 下轮 B 会把 LineFollowRunner 收编进 motion_controller 路段模式，
  `active_source` 授权改由 mission_manager 广播驱动。
"""

from __future__ import annotations

import json
import math
import time

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rcl_interfaces.msg import SetParametersResult
from robogame_core.cmd_vel_arbiter import (
    SOURCE_LINE_FOLLOW,
    ArbiterConfig,
    CmdVelArbiter,
)
from robogame_core.junction_turn import JunctionTurner
from robogame_core.line_follow import LineSensorReading, LineSensorState
from robogame_core.mission_dispatch import (
    LineCommand,
    effective_line_speed,
    parse_line_command,
    parse_turn_command,
)
from robogame_core.models import Velocity2D
from robogame_core.ramp_control import RampController, SlipDecision
from robogame_interfaces.msg import RobotStatus, LineSensor
from std_msgs.msg import String

from motion_control.line_follow_runner import (
    LineFollowOutput,
    LineFollowParams,
    LineFollowRunner,
)


class LineFollowNode(Node):
    def __init__(self) -> None:
        super().__init__("line_follow_controller")
        for name, default in {
            "kp": 1.0,
            "kd": 0.1,
            "vx_base": 0.2,
            "threshold": 0.5,
            "edge_threshold": 0.3,
            "lost_threshold": 5,
            "intersection_threshold": 6,
            "dt_s": 0.02,
            "max_reading_gap_s": 0.25,
            "cmd_stale_s": 0.5,
            "status_stale_s": 0.3,
            "active_source": SOURCE_LINE_FOLLOW,
            # B2：是否要求 /mission/active_source 授权才允许驱动底盘。
            # 默认 False = 独立巡线联调（没有任务管理器时也能跑）；
            # 比赛配置（robot.yaml）置 True，由 mission_manager 广播唯一授权。
            "require_authorization": False,
            "authorization_stale_s": 0.5,
            # 黑白标定基准（现场面板「黑白标定」按钮推送）。
            # 约定（全项目统一，见 docs/line_follow/CALIBRATION_AND_HARDWARE.md:53-58）：
            #   normalized = (raw - white_ref) / (black_ref - white_ref)
            #   1.0 = 黑线，0.0 = 白底  =>  必须 black_ref > white_ref。
            # 面板校验器按同一约定工作（tools/field_dashboard_core.py:396-406，
            # 多数通道 white > black 时会判定“接反”并自动交换），0x14 接口文档也
            # 引用同一公式（docs/field/LINE_TELEMETRY_0x14_INTERFACE_ALIGNMENT_2026-08-19.md:29）。
            # 2026-08-19 修正：这里原先默认 white=4095 / black=0，方向与上面三处相反，
            # 导致 line_sensor_mock 的合成长线被判成白底：LOST 变成 ALL_BLACK 继续前进、
            # all_black 反而停车，且 sine 摆动下转向输出恒为 0（验收会假通过）。
            # 默认值只保证“方向对”；具体数值仍须现场标定后用面板替换。
            "white_ref": 0.0,
            "black_ref": 4095.0,
            "white_offset": [0.0] * 8,
            "black_offset": [0.0] * 8,
        }.items():
            self.declare_parameter(name, default)

        self.runner = LineFollowRunner(LineFollowParams(
            kp=float(self.get_parameter("kp").value),
            kd=float(self.get_parameter("kd").value),
            vx_base=float(self.get_parameter("vx_base").value),
            threshold=float(self.get_parameter("threshold").value),
            edge_threshold=float(self.get_parameter("edge_threshold").value),
            lost_threshold=int(self.get_parameter("lost_threshold").value),
            intersection_threshold=int(
                self.get_parameter("intersection_threshold").value
            ),
            dt_s=float(self.get_parameter("dt_s").value),
            max_reading_gap_s=float(
                self.get_parameter("max_reading_gap_s").value
            ),
        ))
        self.arbiter = CmdVelArbiter(ArbiterConfig(
            stale_s=float(self.get_parameter("cmd_stale_s").value),
        ))
        self.active_source = str(self.get_parameter("active_source").value)
        # B2：任务层广播的授权（唯一有权驱动底盘的来源）。没收到过消息时保持
        # 参数默认值，这样独立巡线联调（无 mission_manager）行为不变。
        self.require_authorization = bool(
            self.get_parameter("require_authorization").value
        )
        self.authorization_stale_s = float(
            self.get_parameter("authorization_stale_s").value
        )
        self.granted_source: str | None = None
        self.granted_time = 0.0
        self.was_driving = False
        # B3：路口转弯（命令来自任务层；执行与状态上报都在本节点）
        self.turner: JunctionTurner | None = None
        self.turn_phase = ""
        self.turn_reason = ""
        self.last_turn_result = ""
        self.last_yaw: float | None = None
        # B3：每段巡线参数（限速）与坡道控制（C3）。任务层进入段时下发一次。
        self.line_command: LineCommand | None = None
        self.effective_vx_limit = float(self.get_parameter("vx_base").value)
        self.ramp: RampController | None = None
        self.ramp_decision = SlipDecision.NORMAL.value
        self.measured_speed_mps: float | None = None
        self.measured_speed_time = 0.0
        self.measured_speed_stale_s = 0.2
        self.last_publish_at: float | None = None
        self.ramp_stuck_logged = False
        self.status_stale_s = float(
            self.get_parameter("status_stale_s").value
        )
        self.robot_status: RobotStatus | None = None
        self.robot_status_time = 0.0
        self.last_output: LineFollowOutput | None = None
        self.calibration = self._read_calibration()
        # 参数可在运行时被现场面板改写（标定），改完立即生效，不必重启节点。
        self.add_on_set_parameters_callback(self._on_set_parameters)
        # 帧计数：valid = 真正喂给算法的帧；invalid = analog_valid=0 被丢弃的帧。
        # 两者一起看才能区分"探头掉线"与"树莓派收不到帧"。
        self.valid_frames = 0
        self.invalid_frames = 0

        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.raw_cmd_pub = self.create_publisher(Twist, "/line_follow/cmd", 10)
        self.status_pub = self.create_publisher(String, "/line_follow/status", 10)
        self.create_subscription(
            LineSensor, "/line_sensor", self._on_line_sensor, 10
        )
        self.create_subscription(
            RobotStatus, "/robot/status", self._on_robot_status, 10
        )
        self.create_subscription(
            String, "/mission/active_source", self._on_authorization, 10
        )
        self.create_subscription(String, "/mission/turn", self._on_turn_command, 10)
        self.create_subscription(String, "/mission/line", self._on_line_command, 10)
        # 轮速（打滑检测用）。⚠️ 轮速里程计看不到「轮子空转车不走」，
        # 只抓得到「轮速跟不上命令」——上坡最常见的失败正是后者。
        self.create_subscription(Odometry, "/wheel_odom", self._on_wheel_odom, 20)
        self.create_timer(0.02, self._tick)

    # ------------------------------------------------------------------
    # 黑白标定
    # ------------------------------------------------------------------

    def _read_calibration(self) -> dict:
        """读取标定基准，还原成**逐路** white_ref[8] / black_ref[8]。

        面板推的是"全局基准 + 每路偏移"：8 路模块各路差异常达数百，
        只用一个全局基准会让某些通道永远判不出黑线。逐路归一化后
        每路的白底都映射到 0、黑线都映射到 1。
        """
        white_base = float(self.get_parameter("white_ref").value)
        black_base = float(self.get_parameter("black_ref").value)
        white_offset = [float(v) for v in self.get_parameter("white_offset").value]
        black_offset = [float(v) for v in self.get_parameter("black_offset").value]
        if len(white_offset) != 8:
            white_offset = [0.0] * 8
        if len(black_offset) != 8:
            black_offset = [0.0] * 8
        return {
            "white_ref": [white_base + offset for offset in white_offset],
            "black_ref": [black_base + offset for offset in black_offset],
            "white_base": white_base,
            "black_base": black_base,
        }

    def _calibration_is_usable(self, white: list[float], black: list[float]) -> tuple[bool, str]:
        worst = min(abs(b - w) for w, b in zip(white, black))
        if worst < 1.0:
            return False, f"存在黑白基准几乎相同的通道（最小差 {worst:.0f}），无法二值化；请重新标定"
        return True, ""

    def _on_set_parameters(self, parameters) -> "SetParametersResult":
        """现场面板推标定时会走到这里；非法组合直接拒绝并说明原因。

        先在副本上算出候选基准再校验，任何一项不合法就整体拒绝，
        不会留下"half-applied"的标定状态。
        """
        names = {param.name for param in parameters}
        if not names & {"white_ref", "black_ref", "white_offset", "black_offset"}:
            return SetParametersResult(successful=True)
        candidate = dict(self.calibration)
        for param in parameters:
            if param.name == "white_ref":
                candidate["white_base"] = float(param.value)
            elif param.name == "black_ref":
                candidate["black_base"] = float(param.value)
            elif param.name == "white_offset":
                candidate["white_ref"] = [candidate["white_base"] + float(v) for v in param.value]
            elif param.name == "black_offset":
                candidate["black_ref"] = [candidate["black_base"] + float(v) for v in param.value]
        white = candidate.get("white_ref")
        black = candidate.get("black_ref")
        if not isinstance(white, list) or len(white) != 8:
            return SetParametersResult(successful=False, reason="white_ref 需要 8 路基准")
        if not isinstance(black, list) or len(black) != 8:
            return SetParametersResult(successful=False, reason="black_ref 需要 8 路基准")
        usable, why = self._calibration_is_usable(white, black)
        if not usable:
            return SetParametersResult(successful=False, reason=why)
        self.calibration = candidate
        self.get_logger().info(
            "标定生效：white_ref="
            + ",".join(f"{v:.0f}" for v in white)
            + " black_ref="
            + ",".join(f"{v:.0f}" for v in black)
        )
        return SetParametersResult(successful=True)

    def _normalize(self, channels) -> list[float]:
        """把原始值映射到 0..1：0=该路白底基准，1=该路黑线基准。

        与 docs 的 (raw - white_min)/(black_max - white_min) 同一方向；
        越界钳位，避免标定后个别通道漂移把偏差算飞。
        """
        white = self.calibration["white_ref"]
        black = self.calibration["black_ref"]
        return [
            min(1.0, max(0.0, (float(raw) - w) / (b - w))) if b != w else 0.0
            for raw, w, b in zip(channels, white, black)
        ]

    # ------------------------------------------------------------------
    # 回调
    # ------------------------------------------------------------------

    def _on_robot_status(self, msg: RobotStatus) -> None:
        self.robot_status = msg
        self.robot_status_time = time.monotonic()

    def _on_authorization(self, msg: String) -> None:
        """任务层广播的底盘授权（唯一来源）。"""
        self.granted_source = str(msg.data).strip()
        self.granted_time = time.monotonic()

    def _authorized_source(self, now: float) -> str:
        """本节点输出时使用哪个授权来源。

        - 未开启 `require_authorization`：用参数 `active_source`（独立联调行为不变）；
        - 开启但没有（或过期）授权消息：返回 `""` → 仲裁找不到该来源 → 输出零速。
          这是 fail-safe：授权断流时车停下，而不是按旧授权继续跑。
        """
        if not self.require_authorization:
            return self.active_source
        if self.granted_source is None:
            return ""
        if (now - self.granted_time) > self.authorization_stale_s:
            return ""
        return self.granted_source

    def _on_line_command(self, msg: String) -> None:
        """任务层下发的巡线段参数（限速 + 坡道 profile）；空串 = 清除。"""
        command = parse_line_command(msg.data)
        self.line_command = command
        if command is None:
            self.effective_vx_limit = float(self.get_parameter("vx_base").value)
            self.ramp = None
            self.ramp_decision = SlipDecision.NORMAL.value
            return
        # 本段生效限速 = min(任务层本段限速, 节点自身上限)；节点参数是现场兜底上限
        self.effective_vx_limit = effective_line_speed(
            command, float(self.get_parameter("vx_base").value)
        )
        self.ramp_stuck_logged = False
        if command.ramp_profile is None:
            self.ramp = None
            self.ramp_decision = SlipDecision.NORMAL.value
            self.get_logger().info(
                f"{command.segment_id}：本段巡线限速 {self.effective_vx_limit:.2f} m/s"
            )
            return
        self.ramp = RampController(command.ramp_profile)
        self.ramp.begin(time.monotonic())
        self.ramp_decision = SlipDecision.NORMAL.value
        self.get_logger().info(
            f"{command.segment_id}：坡道段 {command.ramp_profile.kind.value}，"
            f"限速 {self.effective_vx_limit:.2f} m/s（坡道生效上限 "
            f"{self.ramp.effective_max_speed:.2f}），"
            f"打滑阈值 {command.ramp_profile.slip_threshold_mps} m/s"
        )

    def _on_wheel_odom(self, msg: Odometry) -> None:
        speed = float(msg.twist.twist.linear.x)
        if math.isfinite(speed):
            self.measured_speed_mps = speed
            self.measured_speed_time = time.monotonic()

    def _apply_line_limits(self, out: LineFollowOutput, now: float) -> LineFollowOutput:
        """按本段限速与坡道 profile 处理 PD 输出（坡道段穿过 C3 的 RampController）。"""
        dt = 0.02
        if self.last_publish_at is not None:
            dt = max(1e-3, min(0.5, now - self.last_publish_at))
        self.last_publish_at = now
        limited_vx = min(float(out.vx), self.effective_vx_limit)
        if self.ramp is None:
            self.ramp_decision = SlipDecision.NORMAL.value
            return LineFollowOutput(
                state=out.state, deviation=out.deviation,
                vx=limited_vx, wz=out.wz,
                lost_count=out.lost_count, reading_stale=out.reading_stale,
            )
        measured = self.measured_speed_mps
        if measured is None or (now - self.measured_speed_time) > self.measured_speed_stale_s:
            measured = None  # 轮速过期：不做打滑判定（宁可不判，也不要误判停车）
        command, decision = self.ramp.step(
            now=now,
            desired=Velocity2D(limited_vx, 0.0, float(out.wz)),
            measured_speed=measured,
            dt=dt,
        )
        self.ramp_decision = decision.value
        if decision is SlipDecision.STUCK and not self.ramp_stuck_logged:
            self.ramp_stuck_logged = True
            self.get_logger().error(
                "坡道打滑超时：已停车，交给任务层决定重试或判失败"
            )
        return LineFollowOutput(
            state=out.state, deviation=out.deviation,
            vx=float(command.vx), wz=float(command.wz),
            lost_count=out.lost_count, reading_stale=out.reading_stale,
        )

    def _on_line_sensor(self, msg: LineSensor) -> None:
        if not msg.analog_valid:
            # analog_valid=0 表示模块掉线或最近帧不是 $A：**不能**用旧值纠偏，
            # 但也不能静默——现场需要看到"帧在来、模拟量无效"这个断点。
            self.invalid_frames += 1
            if self.invalid_frames == 1 or self.invalid_frames % 50 == 0:
                self.get_logger().warn(
                    f"analog_valid=false，已丢弃 {self.invalid_frames} 帧"
                    "（探头掉线或最近帧不是 $A）"
                )
            return
        values = self._normalize(msg.channels)
        if len(values) != 8:
            self.get_logger().error(
                f"/line_sensor expected 8 values, got {len(values)}; ignored"
            )
            return
        if any(not math.isfinite(v) for v in values):
            self.get_logger().error("/line_sensor contains NaN/inf; ignored")
            return
        try:
            self.last_output = self.runner.update(
                LineSensorReading(
                    channels=[v >= self.runner.params.threshold for v in values],
                    raw_values=values,
                ),
                time.monotonic(),
            )
            self.valid_frames += 1
        except ValueError as exc:
            self.get_logger().error(f"line reading rejected: {exc}")

    # ------------------------------------------------------------------
    # 周期发布
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        out = self._effective_output(now)
        # B3：先按本段限速/坡道 profile 处理（含打滑降速），
        # 再由转弯命令接管（转弯时原地转，与限速无关）。
        out = self._apply_line_limits(out, now)
        turn = self._turn_output(out, now)
        self._publish(turn if turn is not None else out, now)

    # ------------------------------------------------------------------
    # B3：路口转弯（命令来自任务层 /mission/turn，见 mission_dispatch）
    # ------------------------------------------------------------------

    def _on_turn_command(self, msg: String) -> None:
        """任务层下发的转弯命令；空串 = 清除待执行转弯。"""
        params = parse_turn_command(msg.data)
        if params is None:
            if self.turner is not None:
                self.get_logger().info("收到清除指令：放弃待执行的转弯")
            self.turner = None
            self.last_turn_result = ""
            return
        self.turner = JunctionTurner(params)
        self.last_turn_result = ""
        self.get_logger().info(
            f"收到转弯命令：{params.direction.value}（{params.expect_states}），"
            f"角速度 {params.turn_rate_radps} rad/s，最短 {params.min_turn_s:.2f}s，"
            f"稳定 {params.reacquire_samples} 拍"
        )

    def _turn_output(self, out: LineFollowOutput, now: float) -> LineFollowOutput | None:
        """若正在转弯：返回接管用的输出；否则 None（用常规巡线输出）。

        只在**本节点被授权驱动底盘**时推进转弯计时——否则车不是我们在开，
        推进转弯状态等于凭空消耗它的超时预算。
        """
        turner = self.turner
        if turner is None:
            return None
        authorized = self._authorized_source(now) == SOURCE_LINE_FOLLOW
        command = turner.update(
            line_state=out.state,
            deviation=None if math.isnan(out.deviation) else out.deviation,
            now=now,
            yaw=self._yaw(),
            stale=out.reading_stale,
        ) if authorized else None
        if command is None:
            # 未授权：停住等待（不动计时），并如实上报「等待授权」
            return LineFollowOutput(
                state=out.state, deviation=out.deviation,
                vx=0.0, wz=0.0, lost_count=out.lost_count, reading_stale=out.reading_stale,
            )
        self.turn_phase = command.phase.value
        self.turn_reason = command.reason
        if command.done:
            self.last_turn_result = f"DONE: {command.reason}"
            self.get_logger().info(f"转弯完成：{command.reason}")
            self.turner = None
            self.turn_phase = ""
        elif command.failed:
            # 不回退 PD：保持零速等任务层重发或判失败
            self.last_turn_result = f"FAILED: {command.reason}"
            self.get_logger().error(f"转弯失败：{command.reason}（保持零速，不恢复巡线）")
        return LineFollowOutput(
            state=out.state, deviation=out.deviation,
            vx=command.vx, wz=command.wz,
            lost_count=out.lost_count, reading_stale=out.reading_stale,
        )

    def _yaw(self) -> float | None:
        """航向（可选确认用）。没有 IMU/定位时返回 None（默认不依赖它）。"""
        return self.last_yaw

    def _effective_output(self, now: float) -> LineFollowOutput:
        """传感器失联时输出停车（安全），否则用最近一次算法输出。"""
        last = self.last_output
        if last is None:
            return LineFollowOutput(
                state=LineSensorState.LOST, deviation=float("nan"),
                vx=0.0, wz=0.0, lost_count=0, reading_stale=True,
            )
        runner_time = self.runner.last_reading_time
        if (
            runner_time is not None
            and (now - runner_time) > self.runner.params.max_reading_gap_s
        ):
            return LineFollowOutput(
                state=LineSensorState.LOST, deviation=float("nan"),
                vx=0.0, wz=0.0,
                lost_count=last.lost_count, reading_stale=True,
            )
        return last

    def _safety_blocked(self) -> bool:
        """通信/状态门控：无状态、过期、通信不可用、机构故障 → 停车。

        返回值只回答"能不能走"。**为什么不能走**由 `_gate_report()` 给出，
        两者分开是为了让网页能显示具体断点，而不是只看到一个零速。
        """
        status = self.robot_status
        if status is None:
            return True
        if (time.monotonic() - self.robot_status_time) > self.status_stale_s:
            return True
        if not status.communication_ok:
            return True
        if status.mechanism_fault:
            return True
        return False

    def _gate_report(self, now: float) -> dict:
        """逐个门控的判定证据（供 /line_follow/status 与网页显示）。

        每一层对应一个真实的否决点：
        1. status_ready  —— /robot/status 是否新鲜（超时则整条链不可信）
        2. communication —— STM32 通信是否可用
        3. mechanism     —— 机构是否报故障
        4. reading_stale —— 巡线读数是否过期（含 analog_valid=0 的情况）
        5. arbitration   —— 仲裁是否放行当前来源
        """
        status = self.robot_status
        reasons: list[str] = []
        if status is None:
            status_ready = False
            communication_ok = False
            mechanism_fault = False
            reasons.append("尚未收到 /robot/status")
        else:
            status_ready = (now - self.robot_status_time) <= self.status_stale_s
            communication_ok = bool(status.communication_ok)
            mechanism_fault = bool(status.mechanism_fault)
            if not status_ready:
                reasons.append(
                    f"/robot/status 已过期 {(now - self.robot_status_time):.2f}s"
                    f" > {self.status_stale_s:.2f}s"
                )
            if not communication_ok:
                reasons.append("STM32 通信不可用 (communication_ok=false)")
            if mechanism_fault:
                reasons.append("机构报故障 (mechanism_fault=true)")

        last = self.last_output
        reading_stale = True if last is None else bool(last.reading_stale)
        if last is None:
            reasons.append("尚未收到有效巡线读数")
        elif reading_stale:
            reasons.append("巡线读数过期或模拟量无效")

        blocked = bool(reasons)
        return {
            "status_ready": status_ready,
            "communication_ok": communication_ok,
            "mechanism_fault": mechanism_fault,
            "reading_stale": reading_stale,
            "active_source": self.active_source,
            # B2：授权信息与安全门控分开报告——「没授权停车」不是故障，
            # 但现场排障时必须能一眼看出是授权没给还是安全门控拦了。
            "authorization_required": bool(self.require_authorization),
            "authorized_source": self._authorized_source(now),
            "invalid_frames": self.invalid_frames,
            "blocked": blocked,
            "reasons": reasons,
        }

    def _publish(self, out: LineFollowOutput, now: float) -> None:
        # 原始纠偏（恒发，联调观察）
        raw = Twist()
        raw.linear.x = float(out.vx)
        raw.angular.z = float(out.wz)
        self.raw_cmd_pub.publish(raw)

        # 状态字符串（联调观察）
        state_name = out.state.value if out.state is not None else "LOST"
        gates = self._gate_report(now)
        status_msg = String()
        status_msg.data = (
            f"state={state_name} dev={out.deviation:.3f} "
            f"lost={out.lost_count} stale={out.reading_stale} "
            f"valid_frames={self.valid_frames} invalid_frames={self.invalid_frames} "
            f"blocked={gates['blocked']}"
            f" turn={self.turn_phase or 'none'}"
            # 结构化部分：现场面板解析这一段做逐层显示（#diag# 之后到行尾）。
            " #diag#"
            + json.dumps(
                {
                    "state": state_name,
                    "dev": None if math.isnan(out.deviation) else round(out.deviation, 4),
                    "lost": int(out.lost_count),
                    "stale": bool(out.reading_stale),
                    "valid_frames": int(self.valid_frames),
                    "invalid_frames": int(self.invalid_frames),
                    "out_vx": round(float(out.vx), 4),
                    "out_wz": round(float(out.wz), 4),
                    # B3：转弯状态（网页显示「现在在转弯的哪个阶段」）
                    "turn_phase": self.turn_phase or None,
                    "turn_reason": self.turn_reason or None,
                    "turn_pending": self.turner is not None,
                    "turn_last_result": self.last_turn_result or None,
                    # B3：本段巡线限速与坡道状态（网页显示「现在在坡道哪一步」）
                    "line_limit_mps": round(self.effective_vx_limit, 3),
                    "line_segment": None if self.line_command is None else self.line_command.segment_id,
                    "ramp_kind": None
                    if self.ramp is None
                    else self.ramp.profile.kind.value,
                    "ramp_decision": self.ramp_decision,
                    "measured_speed_mps": None
                    if self.measured_speed_mps is None
                    else round(self.measured_speed_mps, 3),
                    **gates,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        self.status_pub.publish(status_msg)

        # 仲裁门控 /cmd_vel（先安全、再授权；两者都不放行时输出零速）
        authorized = self._authorized_source(now)
        blocked = self._safety_blocked()
        if not blocked and not out.reading_stale:
            self.arbiter.update(
                SOURCE_LINE_FOLLOW,
                Velocity2D(float(out.vx), 0.0, float(out.wz)),
                now=now,
            )
        if authorized in self.arbiter.config.allowed_sources:
            cmd = self.arbiter.output(
                active_source=authorized,
                now=now,
                emergency_stop=blocked,
            )
        else:
            # 未授权 / 授权断流：仲裁器不接受未知来源，这里显式输出零速。
            cmd = Velocity2D(0.0, 0.0, 0.0)
        twist = Twist()
        twist.linear.x = cmd.vx
        twist.linear.y = cmd.vy
        twist.angular.z = cmd.wz
        moving = abs(cmd.vx) > 1e-9 or abs(cmd.wz) > 1e-9
        if authorized not in self.arbiter.config.allowed_sources and not self.was_driving:
            # 从未在驱动 / 已经归零过：保持沉默。持续发零速会与真正在驱动的
            # 其他节点抢 /cmd_vel（谁后发谁赢），把对方的运动打断。
            return
        if not moving:
            self.was_driving = False
        else:
            self.was_driving = True
        self.cmd_pub.publish(twist)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = LineFollowNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
