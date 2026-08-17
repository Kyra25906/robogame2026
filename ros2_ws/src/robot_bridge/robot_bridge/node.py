from __future__ import annotations

import math
import threading
import time

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from robogame_core.hardware_readiness import (
    receive_timestamp_is_fresh,
    robot_status_communication_ok,
)
from robogame_core.models import Pose2D, Velocity2D
from robogame_core.mcu_time import validate_mcu_tick
from robogame_core.manipulator import MechanismOperation as WorkflowOperation
from robogame_core.mechanism_transport import (
    MechanismCommandTracker,
    PollAction,
)
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
    MSG_TYPE_MECHANISM_COMMAND,
    MSG_TYPE_MECHANISM_STATUS,
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
    MechanismCommand,
    MechanismOperation as ProtocolOperation,
    decode_ack,
    decode_imu,
    decode_mechanism_status,
    decode_odom,
    decode_status,
    encode_frame,
    encode_heartbeat,
    encode_hello,
    encode_mechanism_command,
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
        self.declare_parameter("mechanism_ack_timeout_s", 0.1)
        self.declare_parameter("mechanism_status_timeout_s", 0.3)
        self.declare_parameter("mechanism_max_attempts", 3)
        self.declare_parameter("odom_pose_xy_variance", 0.25)
        self.declare_parameter("odom_pose_yaw_variance", 0.1219)
        self.declare_parameter("odom_twist_linear_variance", 0.04)
        self.declare_parameter("odom_twist_yaw_variance", 0.09)
        self.declare_parameter("imu_yaw_rate_variance", 0.09)
        self.declare_parameter("unavailable_variance", 1_000_000.0)
        self.declare_parameter("max_mcu_sample_gap_ms", 250)
        self.declare_parameter("mock_start_after_s", 2.0)
        self.declare_parameter("mock_grab_success", True)
        self.declare_parameter("mock_release_success", True)
        self.declare_parameter("mock_lift_success", True)
        self.mock_mode = bool(self.get_parameter("mock_mode").value)
        self.command_timeout = float(self.get_parameter("command_timeout_s").value)
        self.mechanism_ack_timeout_s = float(
            self.get_parameter("mechanism_ack_timeout_s").value
        )
        self.mechanism_status_timeout_s = float(
            self.get_parameter("mechanism_status_timeout_s").value
        )
        self.mechanism_max_attempts = int(
            self.get_parameter("mechanism_max_attempts").value
        )
        if (
            not math.isfinite(self.mechanism_ack_timeout_s)
            or self.mechanism_ack_timeout_s <= 0.0
            or not math.isfinite(self.mechanism_status_timeout_s)
            or self.mechanism_status_timeout_s <= 0.0
        ):
            raise ValueError("mechanism transport timeouts must be positive and finite")
        if self.mechanism_max_attempts <= 0:
            raise ValueError("mechanism_max_attempts must be positive")
        covariance_names = (
            "odom_pose_xy_variance",
            "odom_pose_yaw_variance",
            "odom_twist_linear_variance",
            "odom_twist_yaw_variance",
            "imu_yaw_rate_variance",
            "unavailable_variance",
        )
        self.covariance = {
            name: float(self.get_parameter(name).value) for name in covariance_names
        }
        if any(not math.isfinite(value) or value <= 0.0 for value in self.covariance.values()):
            raise ValueError("all covariance parameters must be positive and finite")
        self.max_mcu_sample_gap_ms = int(
            self.get_parameter("max_mcu_sample_gap_ms").value
        )
        if self.max_mcu_sample_gap_ms <= 0:
            raise ValueError("max_mcu_sample_gap_ms must be positive")
        self.status_pub = self.create_publisher(RobotStatus, "/robot/status", 10)
        self.odom_pub = self.create_publisher(Odometry, "/wheel_odom", 20)
        self.imu_pub = self.create_publisher(Imu, "/imu/data", 20)
        self.create_subscription(Twist, "/cmd_vel", self._on_cmd_vel, 20)
        self._mechanism_callback_group = ReentrantCallbackGroup()
        self.create_service(
            ExecuteMechanism, "/gripper/grab", self._mechanism,
            callback_group=self._mechanism_callback_group,
        )
        self.create_service(
            ExecuteMechanism, "/gripper/release", self._mechanism,
            callback_group=self._mechanism_callback_group,
        )
        self.create_service(
            ExecuteMechanism, "/chassis/stop", self._mechanism,
            callback_group=self._mechanism_callback_group,
        )
        self.create_service(
            ExecuteMechanism, "/chassis/retreat", self._mechanism,
            callback_group=self._mechanism_callback_group,
        )
        self.create_service(
            ExecuteMechanism, "/mechanism/home", self._mechanism,
            callback_group=self._mechanism_callback_group,
        )
        self.create_service(
            ExecuteMechanism, "/mechanism/stop", self._mechanism_stop,
            callback_group=self._mechanism_callback_group,
        )
        self.create_service(
            SetLiftHeight, "/lift/set_height", self._lift,
            callback_group=self._mechanism_callback_group,
        )
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
        self._last_mcu_tick_ms = {"odom": None, "imu": None}
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
        self._serial_write_lock = threading.Lock()
        self._mechanism_condition = threading.Condition(threading.RLock())
        self._mechanism_tracker: MechanismCommandTracker | None = None
        self._next_mechanism_command_id = 1
        if not self.mock_mode:
            self._open_serial()
        self.create_timer(0.02, self._tick)

    def _reset_real_transport_state(self) -> None:
        """Invalidate old session state without restoring any old command."""
        self._cancel_pending_mechanism(9003, "mechanism transport reset")
        self.velocity = Velocity2D(0.0, 0.0, 0.0)
        self.decoder = StreamDecoder()
        self.last_frame_rx = None
        self.last_decoded_status_rx = None
        self.last_decoded_odom_rx = None
        self.last_decoded_imu_rx = None
        self._latest_status = None
        self._latest_odom = None
        self._latest_imu = None
        self._last_mcu_tick_ms = {"odom": None, "imu": None}
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
        # Some transport unit tests deliberately construct RobotBridge with
        # ``__new__`` so they can exercise fail-safe writes without starting a
        # ROS node. Keep that supported while normal nodes create the lock in
        # ``__init__``.
        if not hasattr(self, "_serial_write_lock"):
            self._serial_write_lock = threading.Lock()
        try:
            with self._serial_write_lock:
                written = self.serial.write(frame)
            if written != len(frame):
                raise OSError(f"short serial write: {written}/{len(frame)} bytes")
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
        if not self._communication_ok:
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

    def _enter_status_timeout_failsafe(self) -> None:
        """Stop the old control session when decoded MCU status goes stale."""
        self.get_logger().error(
            "MCU STATUS timed out; sending STOP, stopping heartbeat, and "
            "requiring a fresh handshake"
        )
        self.velocity = Velocity2D(0.0, 0.0, 0.0)
        if self.serial is not None:
            result = dispatch_stop_frames(self._safe_serial_write, self.sequence)
            self.sequence = result.next_sequence
            if not result.success:
                self.get_logger().error(
                    "STATUS-timeout STOP incomplete; MCU watchdog must stop motion"
                )
        self._reset_real_transport_state()

    def shutdown_transport(self) -> None:
        """Actively stop the MCU before closing a real serial session."""
        self._cancel_pending_mechanism(9003, "robot bridge shutting down")
        self.velocity = Velocity2D(0.0, 0.0, 0.0)
        if not self.mock_mode and self.serial is not None:
            result = dispatch_stop_frames(self._safe_serial_write, self.sequence)
            self.sequence = result.next_sequence
            if not result.success:
                self.get_logger().error(
                    "shutdown STOP incomplete; MCU watchdog must stop motion"
                )
        if self.serial is not None:
            self.serial.close()
            self.serial = None

    def _cancel_pending_mechanism(self, error_code: int, detail: str) -> None:
        condition = getattr(self, "_mechanism_condition", None)
        if condition is None:
            return
        with condition:
            if self._mechanism_tracker is not None:
                self._mechanism_tracker.cancel(
                    error_code, detail, time.monotonic()
                )
                condition.notify_all()

    def _send_pending_mechanism(self, now: float) -> bool:
        with self._mechanism_condition:
            tracker = self._mechanism_tracker
            if tracker is None or tracker.done:
                return False
            sequence = self.sequence
            frame = encode_frame(
                MSG_TYPE_MECHANISM_COMMAND,
                sequence,
                encode_mechanism_command(tracker.command),
            )
            if not self._safe_serial_write(frame):
                tracker.cancel(9003, "mechanism serial write failed", now)
                self._mechanism_condition.notify_all()
                return False
            self.sequence = (self.sequence + 1) & 0xFFFF
            tracker.mark_sent(sequence, now)
            return True

    def _update_mechanism_transport(self, now: float) -> None:
        with self._mechanism_condition:
            tracker = self._mechanism_tracker
            if tracker is None or tracker.done:
                return
            action = tracker.poll(now)
            if tracker.done:
                self._mechanism_condition.notify_all()
                return
        if action is PollAction.RETRY:
            self._send_pending_mechanism(now)

    def _real_mechanism_ready(self) -> tuple[bool, int, str]:
        if self.serial is None:
            return False, 9003, "STM32 serial is unavailable"
        if self._handshake_state != _HANDSHAKE_READY or not self._communication_ok:
            return False, 9003, "STM32 session is not ready"
        status = self._latest_status
        if status is None:
            return False, 9003, "decoded STM32 STATUS is unavailable"
        if status.flags & STATUS_EMERGENCY_STOP:
            return False, 9001, "software emergency stop is active"
        if status.flags & STATUS_MECHANISM_FAULT:
            return False, 9006, "STM32 reports a mechanism fault"
        if not status.flags & STATUS_PHYSICAL_START:
            return False, 5, "physical start is not authorized"
        return True, 0, ""

    def _execute_real_mechanism(
        self,
        operation: ProtocolOperation,
        *,
        parameter: int,
        timeout_s: float,
    ):
        started = time.monotonic()
        ready, error_code, detail = self._real_mechanism_ready()
        if not ready:
            return False, error_code, 0.0, detail
        if not math.isfinite(timeout_s) or timeout_s <= 0.0:
            return False, 4, 0.0, "timeout_s must be positive and finite"

        timeout_ms = min(int(round(timeout_s * 1000.0)), 0xFFFFFFFF)
        with self._mechanism_condition:
            if (
                self._mechanism_tracker is not None
                and not self._mechanism_tracker.done
            ):
                return False, 6, 0.0, "another mechanism command is active"
            command_id = self._next_mechanism_command_id
            self._next_mechanism_command_id = (command_id + 1) & 0xFFFF
            tracker = MechanismCommandTracker(
                MechanismCommand(
                    command_id,
                    operation,
                    parameter,
                    timeout_ms,
                ),
                started_s=started,
                timeout_s=timeout_s + 0.5,
                ack_timeout_s=self.mechanism_ack_timeout_s,
                status_timeout_s=self.mechanism_status_timeout_s,
                max_attempts=self.mechanism_max_attempts,
            )
            self._mechanism_tracker = tracker

        if not self._send_pending_mechanism(started):
            result = tracker.result
            return False, result.error_code, result.duration_s, result.detail

        with self._mechanism_condition:
            while not tracker.done:
                remaining = tracker.deadline_s - time.monotonic()
                if remaining <= 0.0:
                    tracker.poll(time.monotonic())
                    break
                self._mechanism_condition.wait(timeout=min(remaining, 0.1))
            result = tracker.result
        return result.success, result.error_code, result.duration_s, result.detail

    def _mechanism(self, request, response):
        started = time.monotonic()
        command = request.command.upper()
        if command == "STOP" and not self.mock_mode:
            self._cancel_pending_mechanism(
                9001, "global chassis STOP interrupted mechanism command"
            )
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
            operations = {
                "GRAB": ProtocolOperation.GRAB,
                "RELEASE": ProtocolOperation.RELEASE,
                "HOME": ProtocolOperation.HOME,
            }
            operation = operations.get(command)
            if operation is None:
                response.success = False
                response.error_code = 9
                response.duration_s = float(time.monotonic() - started)
                response.detail = f"unsupported real mechanism command {command}"
                return response
            (
                response.success,
                response.error_code,
                response.duration_s,
                response.detail,
            ) = self._execute_real_mechanism(
                operation,
                parameter=0,
                timeout_s=float(request.timeout_s),
            )
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
        operation = WorkflowOperation(command)
        configured_success = bool(self.get_parameter(
            "mock_grab_success" if operation is WorkflowOperation.GRAB
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

    def _mechanism_stop(self, request, response):
        started = time.monotonic()
        if self.mock_mode:
            response.success = True
            response.error_code = 0
            response.duration_s = float(time.monotonic() - started)
            response.detail = "mock mechanism STOP completed"
            return response
        self._cancel_pending_mechanism(8, "mechanism STOP cancelled active command")
        (
            response.success,
            response.error_code,
            response.duration_s,
            response.detail,
        ) = self._execute_real_mechanism(
            ProtocolOperation.STOP,
            parameter=0,
            timeout_s=float(request.timeout_s),
        )
        return response

    def _lift(self, request, response):
        started = time.monotonic()
        if not self.mock_mode:
            height_m = float(request.height_m)
            if not math.isfinite(height_m) or height_m < 0.0:
                response.success = False
                response.error_code = 4
                response.duration_s = float(time.monotonic() - started)
                response.detail = "height_m must be finite and non-negative"
                return response
            (
                response.success,
                response.error_code,
                response.duration_s,
                response.detail,
            ) = self._execute_real_mechanism(
                ProtocolOperation.LIFT_ABS,
                parameter=int(round(height_m * 1000.0)),
                timeout_s=float(request.timeout_s),
            )
            return response
        result = execute_mock_mechanism(
            self.mock_mechanism_state,
            WorkflowOperation.LIFT,
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
        self._cancel_pending_mechanism(9004, "STM32 restarted during mechanism command")
        self.get_logger().warn(
            f"MCU reset detected: boot_id {self._last_boot_id} -> {boot_id}. "
            "Resetting odometry and re-handshaking."
        )
        self._last_boot_id = boot_id
        self.integrator = OdometryIntegrator(Pose2D(0.0, 0.0, 0.0))
        self.velocity = Velocity2D(0.0, 0.0, 0.0)
        self._latest_odom = None
        self.last_decoded_odom_rx = None
        self._latest_imu = None
        self.last_decoded_imu_rx = None
        self._last_mcu_tick_ms = {"odom": None, "imu": None}
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
        if msg_type == MSG_TYPE_ACK:
            try:
                acknowledged, result, _version = decode_ack(frame.payload)
            except ProtocolError as exc:
                self.get_logger().warn(f"invalid ACK payload rejected: {exc}")
                return
            with self._mechanism_condition:
                tracker = self._mechanism_tracker
                if tracker is not None and tracker.handle_ack(
                    acknowledged, result, now
                ):
                    self._mechanism_condition.notify_all()
        elif msg_type == MSG_TYPE_MECHANISM_STATUS:
            try:
                mechanism_status = decode_mechanism_status(frame.payload)
            except ProtocolError as exc:
                self.get_logger().warn(
                    f"invalid MECHANISM_STATUS payload rejected: {exc}"
                )
                return
            with self._mechanism_condition:
                tracker = self._mechanism_tracker
                if tracker is not None and tracker.handle_status(
                    mechanism_status, now
                ):
                    self._mechanism_condition.notify_all()
        elif msg_type == MSG_TYPE_STATUS:
            try:
                status = decode_status(frame.payload)
            except ProtocolError as exc:
                self.get_logger().warn(f"invalid STATUS payload rejected: {exc}")
                return
            self._latest_status = status
            self.last_decoded_status_rx = now
            self._on_status_boot_id(status.boot_id)
            if status.flags & STATUS_EMERGENCY_STOP:
                self._cancel_pending_mechanism(
                    9001, "STM32 emergency stop interrupted mechanism command"
                )
            elif status.flags & STATUS_MECHANISM_FAULT:
                self._cancel_pending_mechanism(
                    9006, "STM32 mechanism fault interrupted command"
                )
        elif msg_type == MSG_TYPE_ODOM:
            try:
                odom = decode_odom(frame.payload)
            except ProtocolError as exc:
                self.get_logger().warn(f"invalid ODOM payload rejected: {exc}")
                return
            if not self._accept_mcu_tick("odom", odom.mcu_tick_ms):
                return
            self._latest_odom = odom
            self.last_decoded_odom_rx = now
        elif msg_type == MSG_TYPE_IMU:
            try:
                imu = decode_imu(frame.payload)
            except ProtocolError as exc:
                self.get_logger().warn(f"invalid IMU payload rejected: {exc}")
                return
            if not self._accept_mcu_tick("imu", imu.mcu_tick_ms):
                return
            self._latest_imu = imu
            self.last_decoded_imu_rx = now

    def _accept_mcu_tick(self, stream: str, current_tick_ms: int) -> bool:
        previous = self._last_mcu_tick_ms[stream]
        result = validate_mcu_tick(
            previous,
            current_tick_ms,
            max_gap_ms=self.max_mcu_sample_gap_ms,
        )
        if result.accepted or result.resync:
            self._last_mcu_tick_ms[stream] = current_tick_ms
        if not result.accepted:
            self.get_logger().warn(
                f"rejected {stream.upper()} mcu_tick_ms={current_tick_ms}: "
                f"{result.reason} delta_ms={result.delta_ms}"
            )
        return result.accepted

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - self.last_tick
        self.last_tick = now

        self._maybe_reconnect_serial(now)
        self._update_mechanism_transport(now)

        # ---------- handshake state machine ----------
        if self._handshake_state == _HANDSHAKE_HANDSHAKING:
            self._run_handshake(now)

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
        communication_was_ok = self._communication_ok
        self._communication_ok = communication_ok
        if not self.mock_mode and communication_was_ok and not communication_ok:
            self._enter_status_timeout_failsafe()
            communication_ok = False

        self._send_heartbeat(now)
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
                self._latest_odom.vx_mps,
                self._latest_odom.vy_mps,
                self._latest_odom.wz_radps,
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
        odom.pose.covariance[0] = self.covariance["odom_pose_xy_variance"]
        odom.pose.covariance[7] = self.covariance["odom_pose_xy_variance"]
        odom.pose.covariance[14] = self.covariance["unavailable_variance"]
        odom.pose.covariance[21] = self.covariance["unavailable_variance"]
        odom.pose.covariance[28] = self.covariance["unavailable_variance"]
        odom.pose.covariance[35] = self.covariance["odom_pose_yaw_variance"]
        odom.twist.twist.linear.x = measured_velocity.vx
        odom.twist.twist.linear.y = measured_velocity.vy
        odom.twist.twist.angular.z = measured_velocity.wz
        odom.twist.covariance[0] = self.covariance["odom_twist_linear_variance"]
        odom.twist.covariance[7] = self.covariance["odom_twist_linear_variance"]
        odom.twist.covariance[14] = self.covariance["unavailable_variance"]
        odom.twist.covariance[21] = self.covariance["unavailable_variance"]
        odom.twist.covariance[28] = self.covariance["unavailable_variance"]
        odom.twist.covariance[35] = self.covariance["odom_twist_yaw_variance"]
        if not self.mock_mode and not odom_fresh:
            odom.twist.covariance[0] = self.covariance["unavailable_variance"]
            odom.twist.covariance[7] = self.covariance["unavailable_variance"]
            odom.twist.covariance[35] = self.covariance["unavailable_variance"]
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
            imu.angular_velocity_covariance[0] = self.covariance["unavailable_variance"]
            imu.angular_velocity_covariance[4] = self.covariance["unavailable_variance"]
            imu.angular_velocity_covariance[8] = self.covariance["imu_yaw_rate_variance"]
        elif self._latest_imu is not None and imu_fresh and self._latest_imu.valid:
            imu.angular_velocity.z = self._latest_imu.gyro_z_radps
            imu.angular_velocity_covariance[0] = self.covariance["unavailable_variance"]
            imu.angular_velocity_covariance[4] = self.covariance["unavailable_variance"]
            imu.angular_velocity_covariance[8] = self.covariance["imu_yaw_rate_variance"]
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
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.shutdown_transport()
        executor.remove_node(node)
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
