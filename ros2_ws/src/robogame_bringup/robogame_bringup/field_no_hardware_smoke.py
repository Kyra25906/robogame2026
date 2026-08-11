from __future__ import annotations

import time

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from robogame_interfaces.msg import RobotStatus
from robogame_interfaces.srv import ExecuteMechanism


class FieldNoHardwareSmoke(Node):
    """Verify that field mode fails closed when no MCU is connected."""

    def __init__(self) -> None:
        super().__init__("field_no_hardware_smoke")
        self.started_at = time.monotonic()
        self.phase = "WAIT_STATUS"
        self.status_seen = False
        self.odom_after_command = 0
        self.command_started_at = 0.0
        self.future = None
        self.exit_code: int | None = None
        self.grab_client = self.create_client(ExecuteMechanism, "/gripper/grab")
        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.create_subscription(RobotStatus, "/robot/status", self._on_status, 10)
        self.create_subscription(Odometry, "/wheel_odom", self._on_odom, 10)
        self.create_timer(0.05, self._tick)

    def _on_status(self, msg: RobotStatus) -> None:
        self.status_seen = True
        if msg.communication_ok:
            self._fail("communication_ok became true without decoded MCU status")
        elif msg.physical_start:
            self._fail("physical_start became true without hardware")
        elif not msg.calibrating:
            self._fail("calibrating must remain true while real IMU status is unavailable")
        elif msg.imu_valid:
            self._fail("imu_valid became true without decoded MCU status")

    def _on_odom(self, msg: Odometry) -> None:
        if self.phase != "CHECK_COMMAND_REJECTION":
            return
        self.odom_after_command += 1
        twist = msg.twist.twist
        if any(abs(value) > 1e-9 for value in (
            twist.linear.x, twist.linear.y, twist.angular.z
        )):
            self._fail("non-zero cmd_vel was accepted before hardware handshake")

    def _tick(self) -> None:
        if self.exit_code is not None:
            return
        now = time.monotonic()
        if now - self.started_at > 8.0:
            self._fail(f"timed out in phase {self.phase}")
            return

        if self.phase == "WAIT_STATUS":
            if self.status_seen and self.grab_client.service_is_ready():
                request = ExecuteMechanism.Request(command="GRAB", timeout_s=1.0)
                self.future = self.grab_client.call_async(request)
                self.phase = "WAIT_REJECTION"
            return

        if self.phase == "WAIT_REJECTION":
            if self.future is None or not self.future.done():
                return
            response = self.future.result()
            if response is None:
                self._fail("GRAB service returned no response")
                return
            if response.success or response.error_code != 2001:
                self._fail(
                    "real GRAB was not rejected with error_code=2001: "
                    f"success={response.success} code={response.error_code}"
                )
                return
            self.phase = "CHECK_COMMAND_REJECTION"
            self.command_started_at = now
            self.get_logger().info(
                "real mechanism correctly rejected; checking cmd_vel gate"
            )

        if self.phase == "CHECK_COMMAND_REJECTION":
            command = Twist()
            command.linear.x = 0.4
            command.linear.y = -0.2
            command.angular.z = 0.5
            self.cmd_pub.publish(command)
            if now - self.command_started_at >= 0.4 and self.odom_after_command >= 2:
                self._pass()

    def _pass(self) -> None:
        self.get_logger().info(
            "PASS: field mode stayed fail-safe without serial hardware "
            "(status false, mechanism rejected, cmd_vel gated)"
        )
        self.exit_code = 0
        rclpy.shutdown()

    def _fail(self, reason: str) -> None:
        self.get_logger().error(f"FAIL: {reason}")
        self.exit_code = 1
        rclpy.shutdown()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = FieldNoHardwareSmoke()
    try:
        rclpy.spin(node)
    except ExternalShutdownException:
        pass
    finally:
        exit_code = 1 if node.exit_code is None else node.exit_code
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    raise SystemExit(exit_code)
