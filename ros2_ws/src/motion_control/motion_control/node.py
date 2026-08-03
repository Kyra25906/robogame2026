from __future__ import annotations

import math
import time

import rclpy
from geometry_msgs.msg import Pose2D as Pose2DMsg, Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from robogame_core.models import Pose2D
from robogame_core.navigation import ControllerConfig, GoToPoseController
from std_msgs.msg import String


def quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


class MotionControllerNode(Node):
    def __init__(self) -> None:
        super().__init__("motion_controller")
        for name, default in {
            "kx": 1.2, "ky": 1.2, "kyaw": 1.8,
            "max_vx": 0.6, "max_vy": 0.5, "max_wz": 1.2,
            "position_tolerance": 0.05, "yaw_tolerance": math.radians(5.0),
            "goal_timeout_s": 15.0, "pose_stale_s": 0.25,
            "min_x": -0.2, "max_x": 7.4, "min_y": -0.2, "max_y": 5.0,
        }.items():
            self.declare_parameter(name, default)
        cfg = ControllerConfig(
            kx=float(self.get_parameter("kx").value), ky=float(self.get_parameter("ky").value),
            kyaw=float(self.get_parameter("kyaw").value), max_vx=float(self.get_parameter("max_vx").value),
            max_vy=float(self.get_parameter("max_vy").value), max_wz=float(self.get_parameter("max_wz").value),
            position_tolerance=float(self.get_parameter("position_tolerance").value),
            yaw_tolerance=float(self.get_parameter("yaw_tolerance").value),
        )
        self.controller = GoToPoseController(cfg)
        self.pose: Pose2D | None = None
        self.goal: Pose2D | None = None
        self.pose_time = 0.0
        self.goal_time = 0.0
        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 20)
        self.result_pub = self.create_publisher(String, "/motion/result", 10)
        self.create_subscription(Odometry, "/pose", self._on_pose, 20)
        self.create_subscription(Pose2DMsg, "/motion/goal", self._on_goal, 10)
        self.create_timer(0.02, self._tick)

    def _on_pose(self, msg: Odometry) -> None:
        p, q = msg.pose.pose.position, msg.pose.pose.orientation
        self.pose = Pose2D(p.x, p.y, quaternion_to_yaw(q.x, q.y, q.z, q.w))
        self.pose_time = time.monotonic()

    def _on_goal(self, msg: Pose2DMsg) -> None:
        bounds = [float(self.get_parameter(n).value) for n in ("min_x", "max_x", "min_y", "max_y")]
        if not (bounds[0] <= msg.x <= bounds[1] and bounds[2] <= msg.y <= bounds[3]):
            self.result_pub.publish(String(data="SAFETY_STOP: goal outside configured field"))
            self.goal = None
            return
        self.goal = Pose2D(msg.x, msg.y, msg.theta)
        self.goal_time = time.monotonic()

    def _stop(self) -> None:
        self.cmd_pub.publish(Twist())

    def _finish(self, result: str) -> None:
        self._stop()
        self.goal = None
        self.result_pub.publish(String(data=result))

    def _tick(self) -> None:
        if self.goal is None:
            return
        now = time.monotonic()
        if self.pose is None or now - self.pose_time > float(self.get_parameter("pose_stale_s").value):
            self._finish("LOCALIZATION_ERROR: pose missing or stale")
            return
        if now - self.goal_time > float(self.get_parameter("goal_timeout_s").value):
            self._finish("TIMEOUT: navigation goal")
            return
        if self.controller.at_goal(self.pose, self.goal):
            self._finish("SUCCESS")
            return
        command = self.controller.command(self.pose, self.goal)
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

