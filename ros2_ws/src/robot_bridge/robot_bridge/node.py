from __future__ import annotations

import math
import time

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from robogame_core.hardware_readiness import (
    receive_timestamp_is_fresh,
    robot_status_communication_ok,
)
from robogame_core.models import Pose2D, Velocity2D
from robogame_core.manipulator import MechanismOperation
from robogame_core.mock_mechanism import (
    MockMechanismState,
    complete_mock_retreat,
    execute_mock_mechanism,
)
from robogame_core.navigation import OdometryIntegrator
from robogame_core.serial_protocol import (
    MSG_TYPE_ACK,
    MSG_TYPE_IMU,
    MSG_TYPE_ODOM,
    MSG_TYPE_STATUS,
    StreamDecoder,
    encode_frame,
    encode_hello,
    encode_velocity,
)
from robogame_interfaces.msg import RobotStatus
from robogame_interfaces.srv import ExecuteMechanism, SetLiftHeight
from sensor_msgs.msg import Imu


def yaw_to_quaternion(yaw: float) -> tuple[float, float, float, float]:
    return 0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0)


_HANDSHAKE_HELLO_INTERVAL_S = 0.5
_HANDSHAKE_TIMEOUT_S = 3.0
_HANDSHAKE_MAX_RETRIES = 3
_HANDSHAKE_UNAVAILABLE = "UNAVAILABLE"
_HANDSHAKE_HANDSHAKING = "HANDSHAKING"
_HANDSHAKE_READY = "READY"


class RobotBridge(Node):
    """Safe bridge. Mock mode is complete; real status decoding is an explicit integration point."""

    def __init__(self) -> None:
        super().__init__("robot_bridge")
        self.declare_parameter("mock_mode", True)
        self.declare_parameter("serial_port", "/dev/ttyACM0")
        self.declare_parameter("baud_rate", 115200)
        self.declare_parameter("command_timeout_s", 0.15)
        self.declare_parameter("mock_start_after_s", 2.0)
        self.declare_parameter("mock_grab_success", True)
        self.declare_parameter("mock_release_success", True)
        self.declare_parameter("mock_lift_success", True)
        self.mock_mode = bool(self.get_parameter("mock_mode").value)
        self.command_timeout = float(self.get_parameter("command_timeout_s").value)
        self.status_pub = self.create_publisher(RobotStatus, "/robot/status", 10)
        self.odom_pub = self.create_publisher(Odometry, "/wheel_odom", 20)
        self.imu_pub = self.create_publisher(Imu, "/imu/data", 20)
        self.create_subscription(Twist, "/cmd_vel", self._on_cmd_vel, 20)
        self.create_service(ExecuteMechanism, "/gripper/grab", self._mechanism)
        self.create_service(ExecuteMechanism, "/gripper/release", self._mechanism)
        self.create_service(ExecuteMechanism, "/chassis/stop", self._mechanism)
        self.create_service(ExecuteMechanism, "/chassis/retreat", self._mechanism)
        self.create_service(SetLiftHeight, "/lift/set_height", self._lift)
        self.velocity = Velocity2D(0.0, 0.0, 0.0)
        self.integrator = OdometryIntegrator(Pose2D(0.0, 0.0, 0.0))
        self.last_command = time.monotonic()
        self.started_at = self.last_command
        self.last_tick = self.last_command
        self.sequence = 0
        self.serial = None
        self.decoder = StreamDecoder()
        self.last_frame_rx: float | None = None
        self.last_decoded_status_rx: float | None = None
        self.mock_mechanism_state = MockMechanismState()
        self._handshake_state = _HANDSHAKE_READY if self.mock_mode else _HANDSHAKE_HANDSHAKING
        self._handshake_last_hello = 0.0
        self._handshake_retries = 0
        self._communication_ok = self.mock_mode
        self._cmd_vel_block_warned = False
        self._last_boot_id: int | None = None
        self._frame_types_seen: set[int] = set()
        if not self.mock_mode:
            self._open_serial()
        self.create_timer(0.02, self._tick)

    def _open_serial(self) -> None:
        import serial

        port = str(self.get_parameter("serial_port").value)
        baud = int(self.get_parameter("baud_rate").value)
        try:
            self.serial = serial.Serial(port, baud, timeout=0.0)
        except (serial.SerialException, OSError) as exc:
            self.serial = None
            self.get_logger().error(
                f"MCU serial unavailable at {port}: {exc}; staying fail-safe"
            )
            return
        self.get_logger().info(f"opened MCU serial port {port} at {baud}")

    def _close_serial_after_error(self, operation: str, exc: Exception) -> None:
        self.get_logger().error(
            f"MCU serial {operation} failed: {exc}; closing port (fail-safe)"
        )
        try:
            self.serial.close()
        except Exception:
            pass
        self.serial = None

    def _safe_serial_write(self, frame: bytes) -> bool:
        if self.serial is None:
            return False
        try:
            self.serial.write(frame)
            return True
        except OSError as exc:
            # serial.SerialException is an OSError subclass, so this covers USB unplug.
            self._close_serial_after_error("write", exc)
            return False

    def _safe_serial_read(self) -> bytes:
        if self.serial is None:
            return b""
        try:
            if not self.serial.in_waiting:
                return b""
            return self.serial.read(self.serial.in_waiting)
        except OSError as exc:
            self._close_serial_after_error("read", exc)
            return b""

    def _on_cmd_vel(self, msg: Twist) -> None:
        if self._handshake_state != _HANDSHAKE_READY:
            if not self._cmd_vel_block_warned:
                self.get_logger().warn(
                    f"cmd_vel blocked: handshake_state={self._handshake_state}"
                )
                self._cmd_vel_block_warned = True
            return
        if not self._communication_ok:
            if not self._cmd_vel_block_warned:
                self.get_logger().warn(
                    "cmd_vel blocked: communication_ok=False "
                    "(STATUS payload undecoded or stale)"
                )
                self._cmd_vel_block_warned = True
            return
        self._cmd_vel_block_warned = False
        self.velocity = Velocity2D(msg.linear.x, msg.linear.y, msg.angular.z)
        self.last_command = time.monotonic()
        if self.serial is not None:
            payload = encode_velocity(self.velocity.vx, self.velocity.vy, self.velocity.wz)
            if self._safe_serial_write(encode_frame(0x01, self.sequence, payload)):
                self.sequence = (self.sequence + 1) & 0xFFFF

    def _mechanism(self, request, response):
        started = time.monotonic()
        if not self.mock_mode:
            response.success = False
            response.error_code = 2001
            response.duration_s = float(time.monotonic() - started)
            response.detail = "real mechanism payload is disabled until the MCU contract is signed"
            return response
        allowed = {"GRAB", "RELEASE", "STOP", "RETREAT"}
        command = request.command.upper()
        if command not in allowed:
            response.success = False
            response.error_code = 1001
            response.duration_s = float(time.monotonic() - started)
            response.detail = f"unsupported command {command}"
            return response
        if command == "STOP":
            self.velocity = Velocity2D(0.0, 0.0, 0.0)
            response.success = True
            response.error_code = 0
            response.duration_s = float(time.monotonic() - started)
            response.detail = "mock command completed"
            return response
        if command == "RETREAT":
            self.velocity = Velocity2D(0.0, 0.0, 0.0)
            self.mock_mechanism_state = complete_mock_retreat(
                self.mock_mechanism_state
            )
            response.success = True
            response.error_code = 0
            response.duration_s = float(time.monotonic() - started)
            response.detail = "mock retreat completed"
            return response
        operation = MechanismOperation(command)
        configured_success = bool(self.get_parameter(
            "mock_grab_success" if operation is MechanismOperation.GRAB
            else "mock_release_success"
        ).value)
        result = execute_mock_mechanism(
            self.mock_mechanism_state,
            operation,
            configured_success=configured_success,
        )
        self.mock_mechanism_state = result.state
        response.success = result.success
        response.error_code = result.error_code
        response.duration_s = float(time.monotonic() - started)
        response.detail = result.detail
        return response

    def _lift(self, request, response):
        started = time.monotonic()
        if not self.mock_mode:
            response.success = False
            response.error_code = 2001
            response.duration_s = float(time.monotonic() - started)
            response.detail = "real lift payload is disabled until the MCU contract is signed"
            return response
        result = execute_mock_mechanism(
            self.mock_mechanism_state,
            MechanismOperation.LIFT,
            configured_success=bool(self.get_parameter("mock_lift_success").value),
            height_m=float(request.height_m),
        )
        self.mock_mechanism_state = result.state
        response.success = result.success
        response.error_code = result.error_code
        response.duration_s = float(time.monotonic() - started)
        response.detail = result.detail
        return response

    def _run_handshake(self, now: float) -> None:
        """Send periodic HELLO frames and watch for an ACK or STATUS reply.

        After _HANDSHAKE_MAX_RETRIES failed attempts the state moves to
        UNAVAILABLE and the bridge stays in fail-safe mode.
        """
        elapsed = now - self._handshake_last_hello
        if elapsed >= _HANDSHAKE_HELLO_INTERVAL_S and self.serial is not None:
            if self._safe_serial_write(encode_frame(MSG_TYPE_ACK, self.sequence, encode_hello())):
                self.sequence = (self.sequence + 1) & 0xFFFF
            self._handshake_last_hello = now

        if now - self._handshake_last_hello > _HANDSHAKE_TIMEOUT_S:
            if self._handshake_retries >= _HANDSHAKE_MAX_RETRIES:
                self.get_logger().error(
                    f"handshake failed after {self._handshake_retries + 1} attempts"
                )
                self._handshake_state = _HANDSHAKE_UNAVAILABLE
                return
            self._handshake_retries += 1
            self._handshake_last_hello = now
            self.get_logger().warn(
                f"handshake timeout, retry {self._handshake_retries}/{_HANDSHAKE_MAX_RETRIES}"
            )

    def _accept_frame_for_handshake(self, frame) -> None:
        """Check whether an incoming frame completes the handshake.

        An ACK (0x13) or STATUS (0x12) frame is required before the bridge
        enters READY and allows velocity commands.
        """
        if self._handshake_state != _HANDSHAKE_HANDSHAKING:
            return
        if frame.message_type in (MSG_TYPE_ACK, 0x12):
            self.get_logger().info(
                f"handshake complete (received type=0x{frame.message_type:02X})"
            )
            self._handshake_state = _HANDSHAKE_READY

    def _on_status_boot_id(self, boot_id: int) -> None:
        """Detect MCU reset via boot_id change and reset dependent state.

        Called when a decoded STATUS frame (0x12) provides a new boot_id.
        On change: resets odometry integrator, forces re-handshake, and
        invalidates IMU state until the next calibration cycle completes.
        """
        if self._last_boot_id is None:
            self._last_boot_id = boot_id
            return
        if boot_id == self._last_boot_id:
            return
        self.get_logger().warn(
            f"MCU reset detected: boot_id {self._last_boot_id} -> {boot_id}. "
            "Resetting odometry and re-handshaking."
        )
        self._last_boot_id = boot_id
        self.integrator = OdometryIntegrator(Pose2D(0.0, 0.0, 0.0))
        if not self.mock_mode:
            self._handshake_state = _HANDSHAKE_HANDSHAKING
            self._handshake_last_hello = 0.0
            self._handshake_retries = 0

    # -- frame dispatch skeleton ------------------------------------------
    # Payload decoders for 0x10 / 0x11 / 0x12 are not yet implemented.
    # Routing may record diagnostics, but it must not refresh decoded-state
    # freshness or boot_id until the complete payload has passed validation.

    def _dispatch_frame(self, frame, now: float) -> None:
        msg_type = frame.message_type
        if msg_type not in self._frame_types_seen:
            self._frame_types_seen.add(msg_type)
            self.get_logger().info(
                f"first frame received: type=0x{msg_type:02X}  "
                f"seq={frame.sequence}  len={len(frame.payload)}"
            )
        if msg_type == MSG_TYPE_STATUS:
            # Fail closed: receiving a frame with the STATUS type is not the
            # same as successfully decoding a complete RobotStatus payload.
            # The future payload adapter owns last_decoded_status_rx and
            # _on_status_boot_id() after length, field, and range validation.
            pass
        elif msg_type == MSG_TYPE_ODOM:
            pass  # placeholder: decode encoder counts, vx/vy/wz, tick
        elif msg_type == MSG_TYPE_IMU:
            pass  # placeholder: decode angular velocity, accel, quaternion

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - self.last_tick
        self.last_tick = now

        # ---------- handshake state machine ----------
        if self._handshake_state == _HANDSHAKE_HANDSHAKING:
            self._run_handshake(now)

        data = self._safe_serial_read()
        if data:
            for _frame in self.decoder.feed(data):
                self._accept_frame_for_handshake(_frame)
                self._dispatch_frame(_frame, now)
                self.last_frame_rx = now
        communication_ok = robot_status_communication_ok(
            mock_mode=self.mock_mode,
            serial_open=self.serial is not None,
            last_decoded_status_s=self.last_decoded_status_rx,
            now_s=now,
            timeout_s=0.30,
        )
        self._communication_ok = communication_ok
        transport_fresh = self.mock_mode or receive_timestamp_is_fresh(
            last_received_s=self.last_frame_rx,
            now_s=now,
            timeout_s=0.30,
        )
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
        status.gripper_closed = self.mock_mechanism_state.gripper_closed
        status.cube_present = self.mock_mechanism_state.cube_present
        status.retreat_complete = self.mock_mechanism_state.retreat_complete
        status.battery_voltage = 24.0
        # IMU calibration state (mock: 2s calibration; real: fail-safe until
        # 0x12 STATUS decoder provides actual values).
        mock_start_after = float(self.get_parameter("mock_start_after_s").value)
        if self.mock_mode:
            status.calibrating = (now - self.started_at) < mock_start_after
            status.imu_valid = not status.calibrating
            status.boot_id = 0
        else:
            status.calibrating = True
            status.imu_valid = False
            status.boot_id = 0
        status.detail = "mock hardware" if self.mock_mode else (
            "MCU transport active; decoded RobotStatus unavailable"
            if transport_fresh
            else "MCU transport inactive; decoded RobotStatus unavailable"
        )
        self.status_pub.publish(status)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = RobotBridge()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node.serial is not None:
            node.serial.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
