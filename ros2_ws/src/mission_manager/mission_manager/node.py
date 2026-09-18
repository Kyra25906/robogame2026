"""任务管理器节点：**只做 ROS 适配**——订阅 → 调 `MissionRun` → 发布命令。

运行逻辑（段切换、重试重发、作业步骤、授权、目标、限速/坡道/转弯命令、网页载荷）
全部在 `robogame_core.mission_run` 里（纯逻辑、可离线端到端预演）。
这样真车上未验证的代码只剩「话题接线」这一层，且它由
`tests/test_mission_manager_route.py` 的结构断言 + `tools/integration_audit.py`
的跨文件一致性检查守住。

两种模式：
- **路线模式**（`route_enabled: true`）：跑 B1 的 13 段全流程；
- **演示模式**（默认）：保持原有「一橙一紫」流程，也不广播授权话题。
"""

from __future__ import annotations

import json
import math
import time

import rclpy
from geometry_msgs.msg import Pose2D as Pose2DMsg, Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from robogame_core.mission import MissionConfig, MissionMachine, MissionState
from robogame_core.mission_dispatch import parse_line_status
from robogame_core.mission_route import SegmentRole
from robogame_core.mission_run import (
    Command,
    CommandKind,
    MissionRun,
    calibration_readiness,
)
from robogame_core.models import Pose2D, yaw_from_quaternion
from robogame_core.route_loader import load_route_plan, resolve_field_layout_path
from robogame_interfaces.msg import CargoState, MissionState as MissionStateMsg, RobotStatus
from std_msgs.msg import String


class MissionManagerNode(Node):
    def __init__(self) -> None:
        super().__init__("mission_manager")
        defaults = {
            "orange_target": 1, "purple_target": 0, "max_retries": 2,
            "state_timeout_s": 20.0, "build_stability_s": 3.0,
            "startup_wait_timeout_s": 15.0,
            "orange_waypoint": [1.0, 0.5, 0.0], "purple_waypoint": [1.5, 1.0, 0.0],
            "build_waypoint": [0.5, 1.5, 1.57], "retreat_waypoint": [0.5, 1.2, 1.57],
            # --- B2 路线模式 ---
            "route_enabled": False,
            "field_layout_path": "",
            # B4：失败降级阶梯（重试耗尽后 跳过/撤退/安全停车）
            "degrade_on_failure": True,
            # B4：比赛总时长上限（秒）。规则 3.2.1：6 分钟，计时结束后动作无效。
            "match_time_limit_s": 360.0,
            # B4：多趟循环（1 = 只跑一趟）。默认 1：真车计时未测，且「第二座建筑
            # 落点」「机构层计数复位」都还没有结论——打开前先看工作留痕文档。
            "rounds": 1,
            # 巡线状态断流多久算过期（过期不许推进段）
            "line_status_timeout_s": 0.5,
            # B4 开赛门：要求巡线标定可用才允许开赛（正式场地配置里打开）
            "require_line_calibration": False,
        }
        for name, value in defaults.items():
            self.declare_parameter(name, value)
        self.machine = MissionMachine(MissionConfig(
            orange_target=int(self.get_parameter("orange_target").value),
            purple_target=int(self.get_parameter("purple_target").value),
            max_retries=int(self.get_parameter("max_retries").value),
            state_timeout_s=float(self.get_parameter("state_timeout_s").value),
            build_stability_s=float(self.get_parameter("build_stability_s").value),
            startup_wait_timeout_s=float(self.get_parameter("startup_wait_timeout_s").value),
            degrade_on_failure=bool(self.get_parameter("degrade_on_failure").value),
            match_time_limit_s=float(self.get_parameter("match_time_limit_s").value),
            require_line_calibration=bool(
                self.get_parameter("require_line_calibration").value
            ),
        ))
        self.route_enabled = bool(self.get_parameter("route_enabled").value)
        self.route_error = ""
        self.cargo_plan = None
        self.run: MissionRun | None = None
        if self.route_enabled:
            self._load_route()

        self.status: RobotStatus | None = None
        self.last_dispatched: MissionState | None = None
        # B4 开赛门用的最近一次巡线状态（`line_status_time <= 0` = 没收到过）
        self.line_status = None
        self.line_status_time = 0.0

        self.state_pub = self.create_publisher(MissionStateMsg, "/mission/state", 10)
        self.cargo_pub = self.create_publisher(CargoState, "/mission/cargo", 10)
        self.goal_pub = self.create_publisher(Pose2DMsg, "/motion/goal", 10)
        self.manip_pub = self.create_publisher(String, "/manipulator/command", 10)
        self.stop_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        # B2：唯一授权话题 + 段进度（网页读取）
        self.authority_pub = self.create_publisher(String, "/mission/active_source", 10)
        self.route_pub = self.create_publisher(String, "/mission/route", 10)
        # B3：转弯命令 + 每段巡线参数（空串 = 清除）
        self.turn_pub = self.create_publisher(String, "/mission/turn", 10)
        self.line_pub = self.create_publisher(String, "/mission/line", 10)
        self.create_subscription(RobotStatus, "/robot/status", self._on_status, 10)
        self.create_subscription(String, "/motion/result", self._on_motion_result, 10)
        self.create_subscription(String, "/manipulator/result", self._on_manipulator_result, 10)
        self.create_subscription(String, "/line_follow/status", self._on_line_status, 10)
        self.create_subscription(Odometry, "/pose", self._on_pose, 20)
        self.create_timer(0.05, self._tick)

    # ------------------------------------------------------------------
    # 启动期：加载路线
    # ------------------------------------------------------------------

    def _load_route(self) -> None:
        path = resolve_field_layout_path(str(self.get_parameter("field_layout_path").value))
        rounds = int(self.get_parameter("rounds").value)
        try:
            plan = load_route_plan(path, rounds=rounds)
        except Exception as exc:  # 缺文件 / 结构变化 / 自检失败
            self.route_error = f"{exc}"
            self.get_logger().error(
                f"路线不可用（route_enabled=true）：{self.route_error}；"
                "任务将判失败并保持停车，不会退回演示流程"
            )
            return
        self.machine.route = plan
        self.cargo_plan = plan.cargo_plan
        self.run = MissionRun(
            machine=self.machine,
            plan=plan,
            cargo_plan=self.cargo_plan,
            line_status_timeout_s=float(
                self.get_parameter("line_status_timeout_s").value
            ),
        )
        self.get_logger().info(
            f"已加载比赛路线 {plan.version}：{len(plan.segments)} 段（{plan.rounds} 趟），"
            f"来源 {path}"
        )

    # ------------------------------------------------------------------
    # 回调
    # ------------------------------------------------------------------

    def _on_status(self, msg: RobotStatus) -> None:
        self.status = msg

    def _on_motion_result(self, msg: String) -> None:
        self._handle_result(msg, "motion")

    def _on_manipulator_result(self, msg: String) -> None:
        self._handle_result(msg, "manipulator")

    def _handle_result(self, msg: String, source: str) -> None:
        """动作结果统一入口（`source` 区分 /motion/result 与 /manipulator/result）。"""
        if self.run is not None:
            self.run.handle_result(source, msg.data, time.monotonic())
        else:
            from robogame_core.mission import classify_action_result

            succeeded, failure_result, detail = classify_action_result(msg.data)
            if succeeded:
                self.machine.tick(action_succeeded=True)
            else:
                self.machine.tick(action_failed=True, failure_result=failure_result,
                                  failure_detail=detail)
        self.last_dispatched = None

    def _on_line_status(self, msg: String) -> None:
        status = parse_line_status(msg.data)
        now = time.monotonic()
        # 演示模式也要记：开赛门（B4/R13）在两种模式下语义必须一致，
        # 否则同一个参数换条流程就变了意思。记录本身没有副作用。
        self.line_status = status
        self.line_status_time = now
        if self.run is None:
            return
        self.run.observe_line_status(status, now)

    def _on_pose(self, msg: Odometry) -> None:
        if self.run is None:
            return
        p, q = msg.pose.pose.position, msg.pose.pose.orientation
        pose = Pose2D(p.x, p.y, yaw_from_quaternion(q.x, q.y, q.z, q.w))
        if not all(math.isfinite(v) for v in (pose.x, pose.y, pose.yaw)):
            return
        self.run.observe_pose(pose, time.monotonic())

    # ------------------------------------------------------------------
    # 演示模式分发（原样保留）
    # ------------------------------------------------------------------

    def _waypoint(self, name: str) -> Pose2DMsg:
        values = list(self.get_parameter(name).value)
        return Pose2DMsg(x=values[0], y=values[1], theta=values[2])

    def _dispatch_demo(self) -> None:
        state = self.machine.state
        if state == self.last_dispatched:
            return
        if state is MissionState.GO_TO_ORANGE:
            self.goal_pub.publish(self._waypoint("orange_waypoint"))
        elif state is MissionState.GO_TO_PURPLE:
            self.goal_pub.publish(self._waypoint("purple_waypoint"))
        elif state is MissionState.GO_TO_BUILD:
            self.goal_pub.publish(self._waypoint("build_waypoint"))
        elif state is MissionState.RETREAT:
            self.goal_pub.publish(self._waypoint("retreat_waypoint"))
        elif state in {MissionState.PICK_ORANGE, MissionState.PICK_PURPLE,
                       MissionState.PLACE_ORANGE, MissionState.PLACE_PURPLE}:
            self.manip_pub.publish(String(data=state.value))
        elif state in {MissionState.SAFE_STOP, MissionState.FAILED, MissionState.COMPLETE}:
            self.stop_pub.publish(Twist())
        self.last_dispatched = state

    # ------------------------------------------------------------------
    # 命令发布（纯逻辑给的命令 → 话题）
    # ------------------------------------------------------------------

    def _publish_command(self, command: Command) -> None:
        if command.kind is CommandKind.AUTHORITY:
            self.authority_pub.publish(String(data=command.text))
        elif command.kind is CommandKind.GOAL:
            pose = command.pose
            self.goal_pub.publish(Pose2DMsg(x=pose.x, y=pose.y, theta=pose.yaw))
        elif command.kind is CommandKind.MANIPULATOR:
            self.manip_pub.publish(String(data=command.text))
        elif command.kind is CommandKind.LINE:
            self.line_pub.publish(String(data=command.text))
        elif command.kind is CommandKind.TURN:
            self.turn_pub.publish(String(data=command.text))
        elif command.kind is CommandKind.STOP:
            self.stop_pub.publish(Twist())

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        if self.status is None:
            return
        status = self.status
        if self.run is not None:
            if self.route_error and self.machine.state not in {
                MissionState.FAILED, MissionState.SAFE_STOP, MissionState.COMPLETE
            }:
                # 路线模式但路线不可用：判失败并保持停车（绝不退回演示流程）
                from robogame_core.models import MissionResult

                self.machine.fail(
                    MissionResult.MECHANISM_ERROR,
                    f"route unavailable: {self.route_error}",
                    now,
                )
            for command in self.run.tick(
                now,
                communication_ok=status.communication_ok,
                emergency_stop=status.emergency_stop,
                physical_start=status.physical_start,
                mechanism_fault=status.mechanism_fault,
            ):
                self._publish_command(command)
            for note in self.run.notes:
                self.get_logger().info(note)
            self.route_pub.publish(String(data=json.dumps(
                self.run.payload(now, route_error=self.route_error), ensure_ascii=False
            )))
            if self.machine.state in (
                MissionState.COMPLETE, MissionState.FAILED, MissionState.SAFE_STOP
            ):
                self.stop_pub.publish(Twist())
        else:
            # 演示模式：保持原有行为（含终态零速）
            if self.machine.state is MissionState.WAIT_FOR_PHYSICAL_START:
                self.machine.tick(
                    now=now, communication_ok=status.communication_ok,
                    emergency_stop=status.emergency_stop,
                    physical_start=status.physical_start,
                    # 开赛门在两种模式下语义一致（同一个判据函数、同一份配置）
                    line_calibration_ready=calibration_readiness(
                        self.line_status, self.line_status_time, now,
                        float(self.get_parameter("line_status_timeout_s").value),
                    ),
                )
            elif self.machine.state in (MissionState.WAIT_FOR_COMMUNICATION,
                                        MissionState.SELF_CHECK):
                self.machine.tick(now=now, communication_ok=status.communication_ok,
                                  emergency_stop=status.emergency_stop,
                                  action_succeeded=not status.mechanism_fault)
            elif self.machine.state is MissionState.VERIFY_BUILD:
                stable_for = now - self.machine.entered_at
                self.machine.tick(now=now, communication_ok=status.communication_ok,
                                  emergency_stop=status.emergency_stop,
                                  action_succeeded=stable_for >= self.machine.config.build_stability_s)
            else:
                self.machine.tick(now=now, communication_ok=status.communication_ok,
                                  emergency_stop=status.emergency_stop)
            self._dispatch_demo()

        stamp = self.get_clock().now().to_msg()
        state = MissionStateMsg(stamp=stamp, state=self.machine.state.value,
                                result=self.machine.result.value, retry_count=self.machine.retries,
                                detail=self.machine.detail)
        self.state_pub.publish(state)
        cargo = CargoState(stamp=stamp, orange_onboard=self.machine.cargo.orange,
                           purple_onboard=self.machine.cargo.purple,
                           total_onboard=self.machine.cargo.total, valid=self.machine.cargo.valid)
        self.cargo_pub.publish(cargo)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MissionManagerNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
