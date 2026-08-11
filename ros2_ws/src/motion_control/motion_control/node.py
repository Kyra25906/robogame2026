from __future__ import annotations

import math
import time

import rclpy
from geometry_msgs.msg import Pose2D as Pose2DMsg, Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from robogame_core.models import Pose2D, Velocity2D, control_safety_result
from robogame_core.navigation import (
    ControllerConfig,
    GoToPoseController,
    limit_velocity_rate,
    pose_is_finite,
)
from std_msgs.msg import String
from robogame_interfaces.msg import RobotStatus


def quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


class MotionControllerNode(Node):
    def __init__(self) -> None:
        super().__init__("motion_controller")
        for name, default in {
            "kx": 1.2, "ky": 1.2, "kyaw": 1.8,
            "max_vx": 0.6, "max_vy": 0.5, "max_wz": 1.2,
            "position_tolerance": 0.05, "yaw_tolerance": math.radians(5.0),
            "slow_radius": 0.35,
            "max_ax": 0.8, "max_ay": 0.8, "max_awz": 2.0,
            "max_control_dt_s": 0.1,
            "goal_timeout_s": 15.0, "pose_stale_s": 0.25,
            "status_stale_s": 0.30,
            "min_x": -0.2, "max_x": 7.4, "min_y": -0.2, "max_y": 5.0,
        }.items():
            self.declare_parameter(name, default)
        cfg = ControllerConfig(
            kx=float(self.get_parameter("kx").value), ky=float(self.get_parameter("ky").value),
            kyaw=float(self.get_parameter("kyaw").value), max_vx=float(self.get_parameter("max_vx").value),
            max_vy=float(self.get_parameter("max_vy").value), max_wz=float(self.get_parameter("max_wz").value),
            position_tolerance=float(self.get_parameter("position_tolerance").value),
            yaw_tolerance=float(self.get_parameter("yaw_tolerance").value),
            slow_radius=float(self.get_parameter("slow_radius").value),
        )
        self.controller = GoToPoseController(cfg)
        self.max_ax = float(self.get_parameter("max_ax").value)
        self.max_ay = float(self.get_parameter("max_ay").value)
        self.max_awz = float(self.get_parameter("max_awz").value)
        self.max_control_dt = float(self.get_parameter("max_control_dt_s").value)
        self.status_stale = float(self.get_parameter("status_stale_s").value)
        if min(self.max_ax, self.max_ay, self.max_awz, self.max_control_dt, self.status_stale) <= 0.0:
            raise ValueError("acceleration limits and control/status timeouts must be positive")
        self.pose: Pose2D | None = None
        self.goal: Pose2D | None = None
        self.pose_time = 0.0
        self.goal_time = 0.0
        self.last_control_time = time.monotonic()
        self.last_velocity = Velocity2D(0.0, 0.0, 0.0)
        self.invalid_pose_reported = False
        self.robot_status: RobotStatus | None = None
        self.robot_status_time = 0.0
        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 20)
        self.result_pub = self.create_publisher(String, "/motion/result", 10)
        self.create_subscription(Odometry, "/pose", self._on_pose, 20)
        self.create_subscription(Pose2DMsg, "/motion/goal", self._on_goal, 10)
        self.create_subscription(RobotStatus, "/robot/status", self._on_robot_status, 10)
        self.create_timer(0.02, self._tick)

    def _status_failure(self):
        status = self.robot_status
        status_fresh = status is not None and (
            time.monotonic() - self.robot_status_time <= self.status_stale
        )
        return control_safety_result(
            status_received=status_fresh,
            communication_ok=bool(status and status.communication_ok),
            emergency_stop=bool(status and status.emergency_stop),
            mechanism_fault=bool(status and status.mechanism_fault),
        )

    @staticmethod
    def _status_failure_detail(failure, unavailable_detail: str) -> str:
        if failure.value == "SAFETY_STOP":
            return "emergency stop"
        if failure.value == "MECHANISM_ERROR":
            return "mechanism fault"
        return unavailable_detail

    def _on_robot_status(self, msg: RobotStatus) -> None:
        self.robot_status = msg
        self.robot_status_time = time.monotonic()
        failure = self._status_failure()
        if self.goal is not None and failure is not None:
            detail = self._status_failure_detail(failure, "robot communication unavailable")
            self._finish(f"{failure.value}: {detail}")

    def _on_pose(self, msg: Odometry) -> None:
        p, q = msg.pose.pose.position, msg.pose.pose.orientation
        candidate = Pose2D(p.x, p.y, quaternion_to_yaw(q.x, q.y, q.z, q.w))
        if not pose_is_finite(candidate):
            if not self.invalid_pose_reported:
                self.get_logger().error("ignored pose containing NaN or infinity")
                self.invalid_pose_reported = True
            return
        if self.invalid_pose_reported:
            self.get_logger().info("pose data is finite again")
            self.invalid_pose_reported = False
        self.pose = candidate
        self.pose_time = time.monotonic()

    def _on_goal(self, msg: Pose2DMsg) -> None:
        failure = self._status_failure()
        if failure is not None:
            detail = self._status_failure_detail(
                failure, "robot status missing or communication unavailable"
            )
            self._finish(f"{failure.value}: {detail}")
            return
        candidate = Pose2D(msg.x, msg.y, msg.theta)
        if not pose_is_finite(candidate):
            self._finish("SAFETY_STOP: goal contains NaN or infinity")
            return
        bounds = [float(self.get_parameter(n).value) for n in ("min_x", "max_x", "min_y", "max_y")]
        if not (bounds[0] <= msg.x <= bounds[1] and bounds[2] <= msg.y <= bounds[3]):
            self._finish("SAFETY_STOP: goal outside configured field")
            return
        was_idle = self.goal is None
        self.goal = candidate
        self.goal_time = time.monotonic()
        if was_idle:
            self.last_velocity = Velocity2D(0.0, 0.0, 0.0)
            self.last_control_time = self.goal_time

    def _stop(self) -> None:
        self.cmd_pub.publish(Twist())
        self.last_velocity = Velocity2D(0.0, 0.0, 0.0)
        self.last_control_time = time.monotonic()

    def _finish(self, result: str) -> None:
        self._stop()
        self.goal = None
        self.result_pub.publish(String(data=result))

    def _tick(self) -> None:
        if self.goal is None:
            return
        now = time.monotonic()
        status_failure = self._status_failure()
        if status_failure is not None:
            detail = self._status_failure_detail(
                status_failure, "robot status stale or communication unavailable"
            )
            self._finish(f"{status_failure.value}: {detail}")
            return
        if self.pose is None or now - self.pose_time > float(self.get_parameter("pose_stale_s").value):
            self._finish("LOCALIZATION_ERROR: pose missing or stale")
            return
        if now - self.goal_time > float(self.get_parameter("goal_timeout_s").value):
            self._finish("TIMEOUT: navigation goal")
            return
        if self.controller.at_goal(self.pose, self.goal):
            self._finish("SUCCESS")
            return
        raw_command = self.controller.command(self.pose, self.goal)
        dt = min(max(0.0, now - self.last_control_time), self.max_control_dt)
        self.last_control_time = now
        command = limit_velocity_rate(
            self.last_velocity, raw_command, dt,
            self.max_ax, self.max_ay, self.max_awz,
        )
        self.last_velocity = command
        msg = Twist()
        msg.linear.x, msg.linear.y, msg.angular.z = command.vx, command.vy, command.wz
        self.cmd_pub.publish(msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MotionControllerNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
