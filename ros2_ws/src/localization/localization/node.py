from __future__ import annotations

import copy
import math
import time

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from robogame_interfaces.msg import RobotStatus
from sensor_msgs.msg import Imu
from tf2_ros import TransformBroadcaster

from localization.quality import (
    choose_yaw_rate,
    detect_pose_jump,
    detect_yaw_divergence,
    odometry_is_finite,
)


class LocalizationNode(Node):
    """Publish validated wheel odometry with gated IMU yaw-rate fusion.

    IMU yaw rate is used only when RobotStatus reports ``imu_valid=true``
    and the latest IMU sample is fresh and finite. Otherwise the node falls
    back to wheel odometry. Non-finite poses, all-zero quaternions, and
    physically implausible pose jumps are rejected before publication.
    """

    def __init__(self) -> None:
        super().__init__("localization")
        self.declare_parameter("imu_stale_s", 0.2)
        self.declare_parameter("max_speed_mps", 3.0)
        self.declare_parameter("divergence_threshold", 0.5)
        for name in ("imu_stale_s", "max_speed_mps", "divergence_threshold"):
            value = float(self.get_parameter(name).value)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be positive and finite")

        self.publisher = self.create_publisher(Odometry, "/pose", 20)
        self.tf_broadcaster = TransformBroadcaster(self)
        self.create_subscription(Odometry, "/wheel_odom", self._on_odom, 20)
        self.create_subscription(Imu, "/imu/data", self._on_imu, 20)
        self.create_subscription(RobotStatus, "/robot/status", self._on_status, 10)

        self.latest_imu: Imu | None = None
        self.latest_imu_time: float | None = None
        self._imu_valid = False
        self._imu_measurement_valid = False
        self._last_boot_id: int | None = None
        self.imu_was_active = False
        self.prev_pose_x: float | None = None
        self.prev_pose_y: float | None = None
        self.prev_pose_time: float | None = None

    def _on_imu(self, msg: Imu) -> None:
        self.latest_imu = msg
        self.latest_imu_time = time.monotonic()
        # sensor_msgs/Imu uses covariance[0] == -1 to mark this measurement
        # unavailable. robot_bridge sets it when the MCU IMU sample is stale.
        self._imu_measurement_valid = msg.angular_velocity_covariance[0] >= 0.0

    def _on_status(self, msg: RobotStatus) -> None:
        self._imu_valid = bool(msg.imu_valid)
        if not msg.communication_ok:
            return
        boot_id = int(msg.boot_id)
        if self._last_boot_id is not None and boot_id != self._last_boot_id:
            self.get_logger().warn(
                f"MCU boot_id changed {self._last_boot_id} -> {boot_id}; "
                "accepting the next odometry sample as a new pose origin"
            )
            self.prev_pose_x = None
            self.prev_pose_y = None
            self.prev_pose_time = None
            self.latest_imu = None
            self.latest_imu_time = None
            self._imu_measurement_valid = False
            self.imu_was_active = False
        self._last_boot_id = boot_id

    def _on_odom(self, msg: Odometry) -> None:
        fused = copy.deepcopy(msg)
        fused.header.frame_id = "map"
        now = time.monotonic()
        imu_age = (
            now - self.latest_imu_time
            if self.latest_imu_time is not None
            else None
        )
        imu_wz = (
            self.latest_imu.angular_velocity.z
            if self.latest_imu is not None
            else None
        )
        wheel_wz = float(msg.twist.twist.angular.z)
        stale_after = float(self.get_parameter("imu_stale_s").value)
        wz, used_imu = choose_yaw_rate(
            wheel_wz,
            imu_wz,
            imu_age,
            stale_after,
            status_valid=self._imu_valid and self._imu_measurement_valid,
        )
        fused.twist.twist.angular.z = wz

        if used_imu and not self.imu_was_active:
            self.get_logger().info("IMU valid and fresh; using IMU yaw rate")
            self.imu_was_active = True
        elif not used_imu and self.imu_was_active:
            reason = "RobotStatus imu_valid=false"
            if self._imu_valid and self._imu_measurement_valid:
                age_text = "missing" if imu_age is None else f"{imu_age:.3f}s"
                reason = f"IMU missing, stale, or non-finite (age={age_text})"
            elif self._imu_valid:
                reason = "latest IMU measurement unavailable"
            self.get_logger().warn(
                f"{reason}; falling back to wheel odometry"
            )
            self.imu_was_active = False

        active_imu_wz = imu_wz if used_imu else None
        divergence, is_divergent = detect_yaw_divergence(
            wheel_wz,
            active_imu_wz,
            threshold=float(self.get_parameter("divergence_threshold").value),
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

        curr_x = float(fused.pose.pose.position.x)
        curr_y = float(fused.pose.pose.position.y)
        if self.prev_pose_x is not None and self.prev_pose_time is not None:
            dt = now - self.prev_pose_time
            _, speed, is_jump = detect_pose_jump(
                self.prev_pose_x,
                self.prev_pose_y,
                curr_x,
                curr_y,
                dt,
                max_speed=float(self.get_parameter("max_speed_mps").value),
            )
            if is_jump:
                self.get_logger().warn(
                    f"Rejecting /pose: jump detected speed={speed:.3f} m/s > "
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
