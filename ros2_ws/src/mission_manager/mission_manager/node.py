"""任务管理器：把比赛路线（B1 的 RoutePlan）变成真实话题上的命令。

## 两种模式

- **路线模式**（`route_enabled: true`）：走 B1 的 13 段全流程（启动区→巡线右转→
  上坡→取 3 块→下坡→搭建 2 层→撤退）。本文件负责：
  1. 喂观测：`/line_follow/status`（巡线状态）、`/pose`（位姿与段内位移）；
  2. 授权：把当前段决定的来源发到 `/mission/active_source`（唯一授权话题）；
  3. 分发：平移/原地转向段发 `/motion/goal`；作业段发 `/manipulator/command`；
  4. 观测：`/mission/route`（String，JSON）给网页显示段进度。
- **演示模式**（`route_enabled: false`，默认）：保持原有「一橙一紫」流程不变，
  也不发授权话题（避免影响既有 mock/single_cube 链路）。

## 为什么授权要由任务层广播

`/cmd_vel` 有多个发布者（motion_control / line_follow / manipulator_client），
靠时序隐式互斥就是「谁后发谁赢」。任务层最清楚「这一时刻谁该动」，所以由它给出
唯一授权；各运动节点自己遵守（见 `mission_dispatch` 的授权表）。授权只能**禁止**
运动，永远不会放行危险运动——急停/通信丢失/机构故障仍由各节点自己的安全门控优先处理。
"""

from __future__ import annotations

import json
import math
import time

import rclpy
from geometry_msgs.msg import Pose2D as Pose2DMsg, Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from robogame_core.mission import (
    MissionConfig,
    MissionMachine,
    MissionState,
    classify_action_result,
)
from robogame_core.mission_dispatch import (
    SOURCE_NONE,
    decide,
    line_command_for,
    parse_line_status,
    turn_command_for,
)
from robogame_core.mission_route import SegmentRole
from robogame_core.models import MissionResult, Pose2D, yaw_from_quaternion
from robogame_core.ramp_control import SlipDecision
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
        ))
        self.route_enabled = bool(self.get_parameter("route_enabled").value)
        self.route_error = ""
        self.cargo_plan = None
        self.placement_warning = ""
        if self.route_enabled:
            self._load_route()

        self.status: RobotStatus | None = None
        self.last_dispatched: MissionState | None = None
        self.last_line_status_time = 0.0
        self.line_status_timeout_s = 0.5
        self.segment_distance_m = 0.0
        self.previous_pose: Pose2D | None = None
        self.published_work_index = -1
        self.published_segment_id = ""
        self.published_retries = 0
        self.last_line_status = None
        self.ramp_stuck_reports = 0
        self.ramp_stuck_retry_reports = 3
        # B3：作业段动作序列（抓/放 + 之间的车体微移）
        self.work_plan: WorkPlan | None = None
        self.work_reference_pose: Pose2D | None = None
        self.published_step_index = -1

        self.state_pub = self.create_publisher(MissionStateMsg, "/mission/state", 10)
        self.cargo_pub = self.create_publisher(CargoState, "/mission/cargo", 10)
        self.goal_pub = self.create_publisher(Pose2DMsg, "/motion/goal", 10)
        self.manip_pub = self.create_publisher(String, "/manipulator/command", 10)
        self.stop_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        # B2：唯一授权话题 + 段进度（网页读取）
        self.authority_pub = self.create_publisher(String, "/mission/active_source", 10)
        self.route_pub = self.create_publisher(String, "/mission/route", 10)
        # B3：转弯命令（进入 JUNCTION_TURN 段时下发一次；空串 = 清除）
        self.turn_pub = self.create_publisher(String, "/mission/turn", 10)
        # B3：每段巡线参数（限速 + 坡道 profile；空串 = 清除）
        self.line_pub = self.create_publisher(String, "/mission/line", 10)
        self.create_subscription(RobotStatus, "/robot/status", self._on_status, 10)
        self.create_subscription(String, "/motion/result", self._on_action_result, 10)
        self.create_subscription(String, "/manipulator/result", self._on_action_result, 10)
        self.create_subscription(String, "/line_follow/status", self._on_line_status, 10)
        self.create_subscription(Odometry, "/pose", self._on_pose, 20)
        self.create_timer(0.05, self._tick)

    # ------------------------------------------------------------------
    # 启动期：加载路线
    # ------------------------------------------------------------------

    def _load_route(self) -> None:
        path = resolve_field_layout_path(str(self.get_parameter("field_layout_path").value))
        try:
            plan = load_route_plan(path)
        except Exception as exc:  # 缺文件 / 结构变化 / 自检失败
            self.route_error = f"{exc}"
            self.get_logger().error(
                f"路线不可用（route_enabled=true）：{self.route_error}；"
                "任务将判失败并保持停车，不会退回演示流程"
            )
            return
        self.machine.route = plan
        self.cargo_plan = plan.cargo_plan
        self.get_logger().info(
            f"已加载比赛路线 {plan.version}：{len(plan.segments)} 段，来源 {path}"
        )

    # ------------------------------------------------------------------
    # 回调
    # ------------------------------------------------------------------

    def _on_status(self, msg: RobotStatus) -> None:
        self.status = msg

    def _on_action_result(self, msg: String) -> None:
        succeeded, failure_result, detail = classify_action_result(msg.data)
        if succeeded:
            # A4/P0-6: INCONCLUSIVE 也走成功推进——进入 mission 级 VERIFY_BUILD
            # 计时兜底，而不是被当作 MECHANISM_ERROR 判死。
            self.machine.tick(action_succeeded=True)
        else:
            # 作业失败：允许重发同一条作业命令（重试语义由 state 机控制）
            self.published_work_index = -1
            self.machine.tick(action_failed=True, failure_result=failure_result,
                              failure_detail=detail)
        self.last_dispatched = None

    def _on_line_status(self, msg: String) -> None:
        parsed = parse_line_status(msg.data)
        self.last_line_status_time = time.monotonic()
        self.last_line_status = parsed
        runner = self.machine.route_runner
        if runner is not None:
            runner.observe_line(parsed.state)
            runner.mark_stale(parsed.stale)
        # B3：坡道打滑卡住 → 按失败重试本段（节点已经停车，这里决定重试还是判死）
        if (
            self.machine.state is MissionState.ROUTE_RUNNING
            and parsed.ramp_decision == SlipDecision.STUCK.value
        ):
            self.ramp_stuck_reports += 1
            if self.ramp_stuck_reports >= self.ramp_stuck_retry_reports:
                self.ramp_stuck_reports = 0
                self.get_logger().warn("坡道打滑卡住：按失败重试本段")
                self.machine.tick(
                    action_failed=True,
                    failure_result=MissionResult.MECHANISM_ERROR,
                    failure_detail="ramp stuck (slip timeout)",
                )
        else:
            self.ramp_stuck_reports = 0

    def _on_pose(self, msg: Odometry) -> None:
        p, q = msg.pose.pose.position, msg.pose.pose.orientation
        pose = Pose2D(p.x, p.y, yaw_from_quaternion(q.x, q.y, q.z, q.w))
        if not all(math.isfinite(v) for v in (pose.x, pose.y, pose.yaw)):
            return
        if self.previous_pose is not None and self.machine.state is MissionState.ROUTE_RUNNING:
            # 段内位移：用位姿增量累加（不走直线也按路径长度算）
            self.segment_distance_m += math.hypot(
                pose.x - self.previous_pose.x, pose.y - self.previous_pose.y
            )
        self.previous_pose = pose
        runner = self.machine.route_runner
        if runner is not None:
            runner.observe_pose(pose)
            runner.observe_distance(self.segment_distance_m)

    # ------------------------------------------------------------------
    # 演示模式分发（原样保留）
    # ------------------------------------------------------------------

    def _waypoint(self, name: str) -> Pose2DMsg:
        values = list(self.get_parameter(name).value)
        return Pose2DMsg(x=values[0], y=values[1], theta=values[2])

    def _dispatch(self) -> None:
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
    # 路线模式分发
    # ------------------------------------------------------------------

    def _release_authority(self) -> None:
        """释放底盘授权（谁都不许动）。终态与异常都走这里。"""
        self.authority_pub.publish(String(data=SOURCE_NONE))
        # 同时清除待执行转弯与巡线参数：否则任务失败后残留的指令会在下次授权时突然执行
        self.turn_pub.publish(String(data=""))
        self.line_pub.publish(String(data=""))

    def _publish_line_command(self, segment) -> None:
        """进入段时下发/清除巡线参数（空串 = 本段不走巡线）。"""
        command = line_command_for(segment)
        self.line_pub.publish(
            String(data="" if command is None else command.to_json())
        )
        if command is not None:
            kind = "巡线" if not command.is_ramp else f"坡道({command.ramp_profile.kind.value})"
            self.get_logger().info(
                f"{segment.id}：下发{kind}参数，限速 {command.max_speed_mps:.2f} m/s"
            )

    def _publish_turn_command(self, segment) -> None:
        """进入段时下发/清除转弯命令（空串 = 本段不需要转弯）。"""
        command = turn_command_for(segment)
        self.turn_pub.publish(
            String(data="" if command is None else json.dumps(command, ensure_ascii=False))
        )
        if command is not None:
            self.get_logger().info(
                f"{segment.id}：下发转弯命令 {command['direction']} @ {command['ref']}"
                f"（角速度 {command['turn_rate_radps']} rad/s，稳定 "
                f"{command['reacquire_samples']} 拍）"
            )

    def _dispatch_route(self) -> None:
        """按当前段下发授权 / 目标 / 作业命令。"""
        runner = self.machine.route_runner
        segment = self.machine.current_segment
        if runner is None or self.cargo_plan is None:
            return
        if segment is None:
            return
        if segment.id != self.published_segment_id:
            self.published_segment_id = segment.id
            self.published_work_index = -1
            self.published_retries = 0
            self.segment_distance_m = 0.0
            self.previous_pose = None
            self._publish_turn_command(segment)
            self._publish_line_command(segment)
            self.get_logger().info(f"进入段 {segment.id}：{segment.label}")
        elif self.machine.retries != self.published_retries:
            # 本段重试：必须重发本段命令——巡线节点会把「打滑卡住」保持零速，
            # 只重置计时是叫不醒它的；重发命令会让它重建转弯器/坡道控制器重新开始。
            self.published_retries = self.machine.retries
            self.get_logger().warn(
                f"{segment.id} 第 {self.machine.retries} 次重试：重发本段命令"
            )
            self._publish_turn_command(segment)
            self._publish_line_command(segment)
        decision = decide(segment, self.cargo_plan, max(runner.observations.work_count, 0))
        # 顺序：先给目标（若有），再授权——两个顺序都安全，因为未授权的运动节点只会发零速
        if decision.goal is not None:
            self.goal_pub.publish(
                Pose2DMsg(x=decision.goal.x, y=decision.goal.y, theta=decision.goal.yaw)
            )
        self.authority_pub.publish(String(data=decision.active_source))
        if segment.role is SegmentRole.WORK and decision.work_command is not None:
            index = runner.observations.work_count
            if index != self.published_work_index:
                self.manip_pub.publish(String(data=decision.work_command))
                self.published_work_index = index
                self.get_logger().info(
                    f"{segment.id}：第 {index + 1} 次作业 → {decision.work_command}"
                )

    def _publish_route_status(self) -> None:
        progress = self.machine.route_progress()
        segment = self.machine.current_segment
        runner = self.machine.route_runner
        plan = self.machine.route
        next_segment = None
        if plan is not None and runner is not None and not runner.is_complete:
            index = runner.segment_index + 1
            if 0 <= index < len(plan.segments):
                next_segment = plan.segments[index].id
        active_source = None
        if segment is not None and self.cargo_plan is not None:
            active_source = decide(segment, self.cargo_plan, 0).active_source
        payload = {
            **progress,
            "route_version": None if plan is None else plan.version,
            "next_segment_id": next_segment,
            "work_count": 0 if runner is None else runner.observations.work_count,
            "work_required": None if segment is None else segment.exit.required_count,
            "active_source": active_source,
            "route_error": self.route_error,
            # B3：巡线节点上报的本段限速/坡道状态（网页显示「现在在坡道哪一步」）
            "line_limit_mps": None
            if self.last_line_status is None
            else self.last_line_status.line_limit_mps,
            "ramp_decision": ""
            if self.last_line_status is None
            else self.last_line_status.ramp_decision,
            "turn_phase": ""
            if self.last_line_status is None
            else self.last_line_status.turn_phase,
        }
        self.route_pub.publish(String(data=json.dumps(payload, ensure_ascii=False)))

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        if self.status is None:
            return
        if self.route_error and self.machine.state not in {
            MissionState.FAILED, MissionState.SAFE_STOP, MissionState.COMPLETE
        }:
            # 路线模式但路线不可用：判失败并保持停车（绝不退回演示流程）
            self.machine.fail(MissionResult.MECHANISM_ERROR, f"route unavailable: {self.route_error}", now)
        if (
            self.machine.state is MissionState.ROUTE_RUNNING
            and now - self.last_line_status_time > self.line_status_timeout_s
        ):
            runner = self.machine.route_runner
            if runner is not None:
                runner.mark_stale(True)  # 巡线状态断流：不许推进任何段
        if self.machine.state is MissionState.WAIT_FOR_COMMUNICATION:
            # A2 / P0-1: 上电握手期 communication_ok=False 正常——等待不判死；
            # 通信就绪后同一 tick 内可继续走 SELF_CHECK（mechanism 就绪即前进）。
            self.machine.tick(now=now, communication_ok=self.status.communication_ok,
                              emergency_stop=self.status.emergency_stop,
                              action_succeeded=not self.status.mechanism_fault)
        elif self.machine.state is MissionState.SELF_CHECK:
            self.machine.tick(now=now, communication_ok=self.status.communication_ok,
                              emergency_stop=self.status.emergency_stop,
                              action_succeeded=not self.status.mechanism_fault)
        elif self.machine.state is MissionState.WAIT_FOR_PHYSICAL_START:
            self.machine.tick(now=now, communication_ok=self.status.communication_ok,
                              emergency_stop=self.status.emergency_stop,
                              physical_start=self.status.physical_start)
        elif self.machine.state is MissionState.VERIFY_BUILD:
            stable_for = now - self.machine.entered_at
            self.machine.tick(now=now, communication_ok=self.status.communication_ok,
                              emergency_stop=self.status.emergency_stop,
                              action_succeeded=stable_for >= self.machine.config.build_stability_s)
        else:
            self.machine.tick(now=now, communication_ok=self.status.communication_ok,
                              emergency_stop=self.status.emergency_stop)

        if self.machine.state is MissionState.ROUTE_RUNNING:
            self._dispatch_route()
            self._publish_route_status()
        elif self.route_enabled and self.machine.state in {
            MissionState.COMPLETE, MissionState.FAILED, MissionState.SAFE_STOP
        }:
            self._release_authority()
            self._dispatch()  # 终态零速
            self._publish_route_status()
        else:
            self._dispatch()

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
