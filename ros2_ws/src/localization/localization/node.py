from __future__ import annotations

import copy
import time

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import Imu
from tf2_ros import TransformBroadcaster

from localization.quality import (
    choose_yaw_rate,
    detect_pose_jump,
    detect_yaw_divergence,
    odometry_is_finite,
)


class LocalizationNode(Node):
    """Initial sprint localization: wheel pose with IMU yaw-rate observability.

    Replace this node with robot_localization after the single-cube gate passes.
    AprilTag corrections intentionally remain a future configurable input.
    """

    def __init__(self) -> None:
        super().__init__("localization")
        self.declare_parameter("imu_stale_s", 0.2)
        self.declare_parameter("max_speed_mps", 3.0)
        self.declare_parameter("divergence_threshold", 0.5)
        self.publisher = self.create_publisher(Odometry, "/pose", 20)
        self.tf_broadcaster = TransformBroadcaster(self)
        self.create_subscription(Odometry, "/wheel_odom", self._on_odom, 20)
        self.create_subscription(Imu, "/imu/data", self._on_imu, 20)
        self.latest_imu: Imu | None = None
        self.latest_imu_time: float | None = None
        self.imu_was_active: bool = False
        # === B: 位姿跳变检测状态 ===
        self.prev_pose_x: float | None = None
        self.prev_pose_y: float | None = None
        self.prev_pose_time: float | None = None

    def _on_imu(self, msg: Imu) -> None:
        self.latest_imu = msg
        self.latest_imu_time = time.monotonic()

    def _on_odom(self, msg: Odometry) -> None:
        fused = copy.deepcopy(msg)
        fused.header.frame_id = "map"

        imu_age = (
            time.monotonic() - self.latest_imu_time
            if self.latest_imu_time is not None
            else None
        )
        imu_wz = (
            self.latest_imu.angular_velocity.z
            if self.latest_imu is not None
            else None
        )

        stale_after = self.get_parameter("imu_stale_s").value
        wz, used_imu = choose_yaw_rate(
            fused.twist.twist.angular.z, imu_wz, imu_age, stale_after
        )
        fused.twist.twist.angular.z = wz

        if used_imu and not self.imu_was_active:
            self.get_logger().info("IMU recovered, using IMU yaw rate")
            self.imu_was_active = True
        elif not used_imu and self.imu_was_active:
            self.get_logger().warn(
                f"IMU stale (age={imu_age:.3f}s > {stale_after}s), "
                f"falling back to wheel odometry"
            )
            self.imu_was_active = False

        # === A: 轮速与 IMU 角速度分歧检测 ===
        wheel_wz = float(msg.twist.twist.angular.z)
        active_imu_wz = imu_wz if (used_imu and imu_wz is not None) else None
        divergence, is_divergent = detect_yaw_divergence(
            wheel_wz,
            active_imu_wz,
            threshold=self.get_parameter("divergence_threshold").value,
        )
        if is_divergent:
            self.get_logger().warn(
                f"Yaw rate divergence: wheel={wheel_wz:.4f} "
                f"imu={active_imu_wz:.4f} diff={divergence:.4f} "
                f"> threshold={self.get_parameter('divergence_threshold').value}"
            )

        values = [
            fused.pose.pose.position.x,
            fused.pose.pose.position.y,
            fused.pose.pose.position.z,
            fused.pose.pose.orientation.x,
            fused.pose.pose.orientation.y,
            fused.pose.pose.orientation.z,
            fused.pose.pose.orientation.w,
            fused.twist.twist.linear.x,
            fused.twist.twist.linear.y,
            fused.twist.twist.angular.z,
        ]

        if not odometry_is_finite(values):
            self.get_logger().warn(
                "Rejecting /pose: non-finite value in odometry input"
            )
            return

        qx = fused.pose.pose.orientation.x
        qy = fused.pose.pose.orientation.y
        qz = fused.pose.pose.orientation.z
        qw = fused.pose.pose.orientation.w
        if qx == 0.0 and qy == 0.0 and qz == 0.0 and qw == 0.0:
            self.get_logger().warn("Rejecting /pose: all-zero quaternion")
            return

        # === B: 位姿跳变检测 ===
        curr_x = float(fused.pose.pose.position.x)
        curr_y = float(fused.pose.pose.position.y)
        now = time.monotonic()

        if self.prev_pose_x is not None and self.prev_pose_time is not None:
            dt = now - self.prev_pose_time
            _, speed, is_jump = detect_pose_jump(
                self.prev_pose_x,
                self.prev_pose_y,
                curr_x,
                curr_y,
                dt,
                max_speed=self.get_parameter("max_speed_mps").value,
            )
            if is_jump:
                self.get_logger().warn(
                    f"Rejecting /pose: jump detected "
                    f"speed={speed:.3f} m/s > "
                    f"max={self.get_parameter('max_speed_mps').value} m/s"
                )
                return

        self.prev_pose_x = curr_x
        self.prev_pose_y = curr_y
        self.prev_pose_time = now

        self.publisher.publish(fused)
        transform = TransformStamped()
        transform.header = fused.header
        transform.child_frame_id = "base_link"
        transform.transform.translation.x = fused.pose.pose.position.x
        transform.transform.translation.y = fused.pose.pose.position.y
        transform.transform.translation.z = fused.pose.pose.position.z
        transform.transform.rotation = fused.pose.pose.orientation
        self.tf_broadcaster.sendTransform(transform)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = LocalizationNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
