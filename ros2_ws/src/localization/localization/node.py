from __future__ import annotations

import copy

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import Imu
from tf2_ros import TransformBroadcaster


class LocalizationNode(Node):
    """Initial sprint localization: wheel pose with IMU yaw-rate observability.

    Replace this node with robot_localization after the single-cube gate passes.
    AprilTag corrections intentionally remain a future configurable input.
    """

    def __init__(self) -> None:
        super().__init__("localization")
        self.declare_parameter("imu_stale_s", 0.2)
        self.publisher = self.create_publisher(Odometry, "/pose", 20)
        self.tf_broadcaster = TransformBroadcaster(self)
        self.create_subscription(Odometry, "/wheel_odom", self._on_odom, 20)
        self.create_subscription(Imu, "/imu/data", self._on_imu, 20)
        self.latest_imu: Imu | None = None

    def _on_imu(self, msg: Imu) -> None:
        self.latest_imu = msg

    def _on_odom(self, msg: Odometry) -> None:
        fused = copy.deepcopy(msg)
        fused.header.frame_id = "map"
        if self.latest_imu is not None:
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
