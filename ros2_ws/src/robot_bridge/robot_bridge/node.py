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
    MSG_TYPE_HEARTBEAT,
    MSG_TYPE_HELLO,
    MSG_TYPE_IMU,
    MSG_TYPE_ODOM,
    MSG_TYPE_STATUS,
    ProtocolError,
    STATUS_EMERGENCY_STOP,
    STATUS_GRIPPER_CLOSED,
    STATUS_IMU_CALIBRATING,
    STATUS_IMU_VALID,
    STATUS_MECHANISM_FAULT,
    STATUS_PHYSICAL_START,
    STATUS_CUBE_PRESENT,
    StreamDecoder,
    decode_ack,
    decode_imu,
    decode_odom,
    decode_status,
    encode_frame,
    encode_heartbeat,
    encode_hello,
    encode_velocity,
)
from robogame_core.stop_transport import dispatch_stop_frames
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
_HEARTBEAT_INTERVAL_S = 0.05
_SERIAL_RECONNECT_INTERVAL_S = 1.0


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
        self.last_decoded_odom_rx: float | None = None
        self.last_decoded_imu_rx: float | None = None
        self._latest_status = None
        self._latest_odom = None
        self._latest_imu = None
        self.mock_mechanism_state = MockMechanismState()
        self._handshake_state = _HANDSHAKE_READY if self.mock_mode else _HANDSHAKE_HANDSHAKING
        self._handshake_last_hello = 0.0
        self._handshake_retries = 0
        self._handshake_pending_sequence: int | None = None
        self._last_heartbeat_tx = 0.0
        self._last_reconnect_attempt = self.started_at
        self._communication_ok = self.mock_mode
        self._cmd_vel_block_warned = False
        self._last_boot_id: int | None = None
        self._frame_types_seen: set[int] = set()
        if not self.mock_mode:
            self._open_serial()
        self.create_timer(0.02, self._tick)

    def _reset_real_transport_state(self) -> None:
        """Invalidate old session state without restoring any old command."""
        self.velocity = Velocity2D(0.0, 0.0, 0.0)
        self.decoder = StreamDecoder()
        self.last_frame_rx = None
        self.last_decoded_status_rx = None
        self.last_decoded_odom_rx = None
        self.last_decoded_imu_rx = None
        self._latest_status = None
        self._latest_odom = None
        self._latest_imu = None
        self._communication_ok = False
        self._handshake_state = _HANDSHAKE_HANDSHAKING
        self._handshake_last_hello = 0.0
        self._handshake_retries = 0
        self._handshake_pending_sequence = None
        self._last_heartbeat_tx = 0.0

    def _open_serial(self) -> bool:
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
            return False
        self._reset_real_transport_state()
        self.get_logger().info(f"opened MCU serial port {port} at {baud}")
        return True

    def _maybe_reconnect_serial(self, now: float) -> None:
        if self.mock_mode or self.serial is not None:
            return
        if now - self._last_reconnect_attempt < _SERIAL_RECONNECT_INTERVAL_S:
            return
        self._last_reconnect_attempt = now
        if self._open_serial():
            self.get_logger().info("MCU serial reconnected; starting a new handshake")

    def _close_serial_after_error(self, operation: str, exc: Exception) -> None:
        self.get_logger().error(
            f"MCU serial {operation} failed: {exc}; closing port (fail-safe)"
        )
        try:
            self.serial.close()
        except Exception:
            pass
        self.serial = None
        self._reset_real_transport_state()
        self._last_reconnect_attempt = time.monotonic()

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

    def _send_zero_velocity(self) -> None:
        """Explicitly zero the MCU velocity on command timeout.

        The MCU keeps executing its last non-zero velocity until its own
        watchdog fires; sending zero here closes that gap as soon as our
        command_timeout expires. Mirrors _on_cmd_vel's trust gating.
        """
        if self._handshake_state != _HANDSHAKE_READY:
            return
        if not self._communication_ok:
            return
        if self.serial is None:
            return
        if self._safe_serial_write(
            encode_frame(0x01, self.sequence, encode_velocity(0.0, 0.0, 0.0))
        ):
            self.sequence = (self.sequence + 1) & 0xFFFF

    def _send_heartbeat(self, now: float) -> None:
        """Keep the MCU watchdog alive without authorizing any motion."""
        if self.mock_mode or self.serial is None:
            return
        if self._handshake_state != _HANDSHAKE_READY:
            return
        if now - self._last_heartbeat_tx < _HEARTBEAT_INTERVAL_S:
            return
        host_tick_ms = int(now * 1000.0) & 0xFFFFFFFF
        frame = encode_frame(
            MSG_TYPE_HEARTBEAT,
            self.sequence,
            encode_heartbeat(host_tick_ms),
        )
        if self._safe_serial_write(frame):
            self.sequence = (self.sequence + 1) & 0xFFFF
            self._last_heartbeat_tx = now

    def _mechanism(self, request, response):
        started = time.monotonic()
        command = request.command.upper()
        if command == "STOP" and not self.mock_mode:
            self.velocity = Velocity2D(0.0, 0.0, 0.0)
            result = dispatch_stop_frames(self._safe_serial_write, self.sequence)
            self.sequence = result.next_sequence
            response.success = result.success
            response.error_code = 0 if result.success else 3001
            response.duration_s = float(time.monotonic() - started)
            response.detail = (
                "real STOP sent: zero velocity + emergency stop"
                if result.success
                else "real STOP incomplete: "
                f"zero_velocity_sent={result.zero_velocity_sent} "
                f"emergency_stop_sent={result.emergency_stop_sent}"
            )
            return response
        if not self.mock_mode:
            response.success = False
            response.error_code = 2001
            response.duration_s = float(time.monotonic() - started)
            response.detail = "real mechanism payload is disabled until the MCU contract is signed"
            return response
        allowed = {"GRAB", "RELEASE", "STOP", "RETREAT"}
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
        if self.serial is None:
            return
        elapsed = now - self._handshake_last_hello
        if self._handshake_retries < _HANDSHAKE_MAX_RETRIES:
            if self._handshake_retries == 0 or elapsed >= _HANDSHAKE_HELLO_INTERVAL_S:
                hello_sequence = self.sequence
                frame = encode_frame(
                    MSG_TYPE_HELLO, hello_sequence, encode_hello()
                )
                if self._safe_serial_write(frame):
                    self._handshake_pending_sequence = hello_sequence
                    self.sequence = (self.sequence + 1) & 0xFFFF
                    self._handshake_retries += 1
                    self._handshake_last_hello = now
            return

        if elapsed >= _HANDSHAKE_TIMEOUT_S:
            self.get_logger().error(
                f"handshake failed after {self._handshake_retries} attempts"
            )
            self._handshake_state = _HANDSHAKE_UNAVAILABLE

    def _accept_frame_for_handshake(self, frame) -> None:
        """Check whether an incoming frame completes the handshake.

        Only a valid ACK for the most recently transmitted HELLO may move the
        bridge to READY. Periodic STATUS telemetry does not prove that the MCU
        accepted this control session.
        """
        if self._handshake_state != _HANDSHAKE_HANDSHAKING:
            return
        accepted = False
        if frame.message_type == MSG_TYPE_ACK:
            try:
                acknowledged, result, _version = decode_ack(frame.payload)
            except ProtocolError as exc:
                self.get_logger().warn(f"invalid handshake ACK rejected: {exc}")
                return
            accepted = (
                self._handshake_pending_sequence is not None
                and acknowledged == self._handshake_pending_sequence
                and result == 0
            )
        if not accepted:
            return
        self.get_logger().info(
            f"handshake complete (validated type=0x{frame.message_type:02X})"
        )
        self._handshake_state = _HANDSHAKE_READY
        self._handshake_pending_sequence = None

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
            self._handshake_pending_sequence = None

    # -- frame dispatch ----------------------------------------------------
    # ODOM, IMU and STATUS are decoded fail-closed: malformed payloads never
    # refresh trusted-state freshness.

    def _dispatch_frame(self, frame, now: float) -> None:
        msg_type = frame.message_type
        if msg_type not in self._frame_types_seen:
            self._frame_types_seen.add(msg_type)
            self.get_logger().info(
                f"first frame received: type=0x{msg_type:02X}  "
                f"seq={frame.sequence}  len={len(frame.payload)}"
            )
        if msg_type == MSG_TYPE_STATUS:
            try:
                status = decode_status(frame.payload)
            except ProtocolError as exc:
                self.get_logger().warn(f"invalid STATUS payload rejected: {exc}")
                return
            self._latest_status = status
            self.last_decoded_status_rx = now
            self._on_status_boot_id(status.boot_id)
        elif msg_type == MSG_TYPE_ODOM:
            try:
                odom = decode_odom(frame.payload)
            except ProtocolError as exc:
                self.get_logger().warn(f"invalid ODOM payload rejected: {exc}")
                return
            self._latest_odom = odom
            self.last_decoded_odom_rx = now
        elif msg_type == MSG_TYPE_IMU:
            try:
                imu = decode_imu(frame.payload)
            except ProtocolError as exc:
                self.get_logger().warn(f"invalid IMU payload rejected: {exc}")
                return
            self._latest_imu = imu
            self.last_decoded_imu_rx = now

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - self.last_tick
        self.last_tick = now

        self._maybe_reconnect_serial(now)

        # ---------- handshake state machine ----------
        if self._handshake_state == _HANDSHAKE_HANDSHAKING:
            self._run_handshake(now)

        self._send_heartbeat(now)

        data = self._safe_serial_read()
        if data:
            for _frame in self.decoder.feed(data):
                self._dispatch_frame(_frame, now)
                self._accept_frame_for_handshake(_frame)
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
            if self.velocity.vx or self.velocity.vy or self.velocity.wz:
                self._send_zero_velocity()
            self.velocity = Velocity2D(0.0, 0.0, 0.0)
        odom_fresh = receive_timestamp_is_fresh(
            last_received_s=self.last_decoded_odom_rx,
            now_s=now,
            timeout_s=0.30,
        )
        if self.mock_mode:
            measured_velocity = self.velocity
        elif self._latest_odom is not None and odom_fresh:
            measured_velocity = Velocity2D(
                self._latest_odom.vx,
                self._latest_odom.vy,
                self._latest_odom.wz,
            )
        else:
            measured_velocity = Velocity2D(0.0, 0.0, 0.0)
        pose = self.integrator.update(measured_velocity, dt, measured_velocity.wz)
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
        odom.twist.twist.linear.x = measured_velocity.vx
        odom.twist.twist.linear.y = measured_velocity.vy
        odom.twist.twist.angular.z = measured_velocity.wz
        self.odom_pub.publish(odom)

        imu = Imu()
        imu.header.stamp = stamp
        imu.header.frame_id = "imu_link"
        imu_fresh = receive_timestamp_is_fresh(
            last_received_s=self.last_decoded_imu_rx,
            now_s=now,
            timeout_s=0.30,
        )
        if self.mock_mode:
            imu.angular_velocity.z = self.velocity.wz
        elif self._latest_imu is not None and imu_fresh and self._latest_imu.valid:
            imu.angular_velocity.z = self._latest_imu.gyro_z
        else:
            # sensor_msgs/Imu convention: covariance[0] = -1 means this
            # measurement is unavailable and consumers must not use it.
            imu.angular_velocity.z = 0.0
            imu.angular_velocity_covariance[0] = -1.0
        self.imu_pub.publish(imu)

        status = RobotStatus()
        status.stamp = stamp
        status.communication_ok = communication_ok
        real_status = self._latest_status if not self.mock_mode else None
        flags = real_status.flags if real_status is not None else 0
        status.emergency_stop = bool(flags & STATUS_EMERGENCY_STOP)
        status.physical_start = (
            bool(flags & STATUS_PHYSICAL_START)
            if real_status is not None
            else self.mock_mode and (
                now - self.started_at >= float(self.get_parameter("mock_start_after_s").value)
            )
        )
        status.gripper_closed = (
            bool(flags & STATUS_GRIPPER_CLOSED)
            if real_status is not None
            else self.mock_mechanism_state.gripper_closed
        )
        status.cube_present = (
            bool(flags & STATUS_CUBE_PRESENT)
            if real_status is not None
            else self.mock_mechanism_state.cube_present
        )
        status.retreat_complete = self.mock_mechanism_state.retreat_complete
        status.mechanism_fault = bool(flags & STATUS_MECHANISM_FAULT)
        status.battery_voltage = (
            real_status.battery_mv / 1000.0 if real_status is not None else 24.0
        )
        status.error_code = real_status.error_code if real_status is not None else 0
        # IMU calibration state (mock: 2s calibration; real: fail-safe until
        # 0x12 STATUS decoder provides actual values).
        mock_start_after = float(self.get_parameter("mock_start_after_s").value)
        if self.mock_mode:
            status.calibrating = (now - self.started_at) < mock_start_after
            status.imu_valid = not status.calibrating
            status.boot_id = 0
        else:
            status.calibrating = (
                bool(flags & STATUS_IMU_CALIBRATING)
                if real_status is not None
                else True
            )
            status.imu_valid = (
                bool(flags & STATUS_IMU_VALID)
                if real_status is not None
                else False
            )
            status.boot_id = real_status.boot_id if real_status is not None else 0
        if self.mock_mode:
            status.detail = "mock hardware"
        elif real_status is not None:
            status.detail = "decoded MCU V1 STATUS"
        elif transport_fresh:
            status.detail = "MCU transport active; decoded RobotStatus unavailable"
        else:
            status.detail = "MCU transport inactive; decoded RobotStatus unavailable"
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
