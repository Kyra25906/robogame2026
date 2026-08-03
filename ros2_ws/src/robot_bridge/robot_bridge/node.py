from __future__ import annotations

import math
import time

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from robogame_core.models import Pose2D, Velocity2D
from robogame_core.navigation import OdometryIntegrator
from robogame_core.serial_protocol import StreamDecoder, encode_frame, encode_velocity
from robogame_interfaces.msg import RobotStatus
from robogame_interfaces.srv import ExecuteMechanism, SetLiftHeight
from sensor_msgs.msg import Imu


def yaw_to_quaternion(yaw: float) -> tuple[float, float, float, float]:
    return 0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0)


class RobotBridge(Node):
    """Safe bridge. Mock mode is complete; real status decoding is an explicit integration point."""

    def __init__(self) -> None:
        super().__init__("robot_bridge")
        self.declare_parameter("mock_mode", True)
        self.declare_parameter("serial_port", "/dev/ttyACM0")
        self.declare_parameter("baud_rate", 115200)
        self.declare_parameter("command_timeout_s", 0.15)
        self.declare_parameter("mock_start_after_s", 2.0)
        self.mock_mode = bool(self.get_parameter("mock_mode").value)
        self.command_timeout = float(self.get_parameter("command_timeout_s").value)
        self.status_pub = self.create_publisher(RobotStatus, "/robot/status", 10)
        self.odom_pub = self.create_publisher(Odometry, "/wheel_odom", 20)
        self.imu_pub = self.create_publisher(Imu, "/imu/data", 20)
        self.create_subscription(Twist, "/cmd_vel", self._on_cmd_vel, 20)
        self.create_service(ExecuteMechanism, "/gripper/grab", self._mechanism)
        self.create_service(ExecuteMechanism, "/gripper/release", self._mechanism)
        self.create_service(ExecuteMechanism, "/chassis/stop", self._mechanism)
        self.create_service(SetLiftHeight, "/lift/set_height", self._lift)
        self.velocity = Velocity2D(0.0, 0.0, 0.0)
        self.integrator = OdometryIntegrator(Pose2D(0.0, 0.0, 0.0))
        self.last_command = time.monotonic()
        self.started_at = self.last_command
        self.last_tick = self.last_command
        self.sequence = 0
        self.serial = None
        self.decoder = StreamDecoder()
        self.last_rx = 0.0
        if not self.mock_mode:
            self._open_serial()
        self.create_timer(0.02, self._tick)

    def _open_serial(self) -> None:
        import serial

        port = str(self.get_parameter("serial_port").value)
        baud = int(self.get_parameter("baud_rate").value)
        self.serial = serial.Serial(port, baud, timeout=0.0)
        self.get_logger().info(f"opened MCU serial port {port} at {baud}")

    def _on_cmd_vel(self, msg: Twist) -> None:
        self.velocity = Velocity2D(msg.linear.x, msg.linear.y, msg.angular.z)
        self.last_command = time.monotonic()
        if self.serial is not None:
            payload = encode_velocity(self.velocity.vx, self.velocity.vy, self.velocity.wz)
            self.serial.write(encode_frame(0x01, self.sequence, payload))
            self.sequence = (self.sequence + 1) & 0xFFFF

    def _mechanism(self, request, response):
        started = time.monotonic()
        if not self.mock_mode:
            response.success = False
            response.error_code = 2001
            response.duration_s = float(time.monotonic() - started)
            response.detail = "real mechanism payload is disabled until the MCU contract is signed"
            return response
        allowed = {"GRAB", "RELEASE", "STOP"}
        command = request.command.upper()
        response.success = command in allowed
        response.error_code = 0 if response.success else 1001
        response.duration_s = float(time.monotonic() - started)
        response.detail = "mock command completed" if response.success else f"unsupported command {command}"
        if command == "STOP":
            self.velocity = Velocity2D(0.0, 0.0, 0.0)
        return response

    def _lift(self, request, response):
        started = time.monotonic()
        if not self.mock_mode:
            response.success = False
            response.error_code = 2001
            response.duration_s = float(time.monotonic() - started)
            response.detail = "real lift payload is disabled until the MCU contract is signed"
            return response
        response.success = 0.0 <= request.height_m <= 0.8
        response.error_code = 0 if response.success else 1002
        response.duration_s = float(time.monotonic() - started)
        response.detail = "mock lift completed" if response.success else "height outside [0, 0.8] m"
        return response

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - self.last_tick
        self.last_tick = now
        if self.serial is not None and self.serial.in_waiting:
            for _frame in self.decoder.feed(self.serial.read(self.serial.in_waiting)):
                self.last_rx = now
        communication_ok = self.mock_mode or (self.serial is not None and now - self.last_rx < 0.30)
        if now - self.last_command > self.command_timeout:
            self.velocity = Velocity2D(0.0, 0.0, 0.0)
        pose = self.integrator.update(self.velocity, dt, self.velocity.wz)
        stamp = self.get_clock().now().to_msg()

        odom = Odometry()
        odom.header.stamp = stamp
        odom.header.frame_id = "odom"
        odom.child_frame_id = "base_link"
        odom.pose.pose.position.x = pose.x
        odom.pose.pose.position.y = pose.y
        qx, qy, qz, qw = yaw_to_quaternion(pose.yaw)
        odom.pose.pose.orientation.x, odom.pose.pose.orientation.y = qx, qy
        odom.pose.pose.orientation.z, odom.pose.pose.orientation.w = qz, qw
        odom.twist.twist.linear.x = self.velocity.vx
        odom.twist.twist.linear.y = self.velocity.vy
        odom.twist.twist.angular.z = self.velocity.wz
        self.odom_pub.publish(odom)

        imu = Imu()
        imu.header.stamp = stamp
        imu.header.frame_id = "imu_link"
        imu.angular_velocity.z = self.velocity.wz
        self.imu_pub.publish(imu)

        status = RobotStatus()
        status.stamp = stamp
        status.communication_ok = communication_ok
        status.emergency_stop = False
        status.physical_start = self.mock_mode and (
            now - self.started_at >= float(self.get_parameter("mock_start_after_s").value)
        )
        status.battery_voltage = 24.0
        status.detail = "mock hardware" if self.mock_mode else (
            "MCU frame received; status payload adapter pending" if communication_ok else "MCU heartbeat missing"
        )
        self.status_pub.publish(status)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = RobotBridge()
    try:
        rclpy.spin(node)
    finally:
        if node.serial is not None:
            node.serial.close()
        node.destroy_node()
        rclpy.shutdown()
