from __future__ import annotations

import copy
import time

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from robogame_interfaces.msg import RobotStatus
from sensor_msgs.msg import Imu
from tf2_ros import TransformBroadcaster


class LocalizationNode(Node):
    """Wheel odometry fused with IMU yaw-rate when the IMU is valid and fresh.

    When RobotStatus reports imu_valid=false (calibrating, faulted, or
    unavailable) or the last IMU message is older than imu_stale_s, the
    node degrades to pure wheel odometry without IMU substitution.
    """

    def __init__(self) -> None:
        super().__init__("localization")
        self.declare_parameter("imu_stale_s", 0.2)
        self.publisher = self.create_publisher(Odometry, "/pose", 20)
        self.tf_broadcaster = TransformBroadcaster(self)
        self.create_subscription(Odometry, "/wheel_odom", self._on_odom, 20)
        self.create_subscription(Imu, "/imu/data", self._on_imu, 20)
        self.create_subscription(RobotStatus, "/robot/status", self._on_status, 10)
        self.latest_imu: Imu | None = None
        self._last_imu_time = 0.0
        self._imu_valid = False

    def _on_imu(self, msg: Imu) -> None:
        self.latest_imu = msg
        self._last_imu_time = time.monotonic()

    def _on_status(self, msg: RobotStatus) -> None:
        self._imu_valid = bool(msg.imu_valid)

    def _imu_usable(self, now: float) -> bool:
        """IMU data is usable only when it is both valid and fresh."""
        if not self._imu_valid:
            return False
        if self.latest_imu is None:
            return False
        stale_s = float(self.get_parameter("imu_stale_s").value)
        return (now - self._last_imu_time) <= stale_s

    def _on_odom(self, msg: Odometry) -> None:
        fused = copy.deepcopy(msg)
        fused.header.frame_id = "map"
        if self._imu_usable(time.monotonic()):
            fused.twist.twist.angular.z = self.latest_imu.angular_velocity.z
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
