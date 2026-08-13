import ast
import unittest
from pathlib import Path

from robogame_core.hardware_readiness import (
    format_mechanism_status_summary,
    receive_timestamp_is_fresh,
    robot_status_communication_ok,
    validate_runtime_evidence_policy,
)


class MechanismStatusSummaryTests(unittest.TestCase):
    def test_healthy_status_is_compact_and_unambiguous(self):
        self.assertEqual(
            format_mechanism_status_summary({
                "communication_ok": True,
                "emergency_stop": False,
                "calibrating": False,
                "imu_valid": True,
                "mechanism_fault": False,
            }),
            "comm=OK estop=OFF calibrating=NO imu_valid=YES mechanism_fault=NO",
        )

    def test_missing_status_is_never_displayed_as_healthy(self):
        self.assertEqual(
            format_mechanism_status_summary({}),
            "comm=UNKNOWN estop=UNKNOWN calibrating=UNKNOWN "
            "imu_valid=UNKNOWN mechanism_fault=UNKNOWN",
        )

    def test_fault_flags_are_visible(self):
        summary = format_mechanism_status_summary({
            "communication_ok": False,
            "emergency_stop": True,
            "calibrating": True,
            "imu_valid": False,
            "mechanism_fault": True,
        })
        self.assertIn("comm=BAD", summary)
        self.assertIn("estop=ON", summary)
        self.assertIn("mechanism_fault=YES", summary)


class ReceiveFreshnessTests(unittest.TestCase):
    def test_missing_message_is_not_fresh(self):
        self.assertFalse(receive_timestamp_is_fresh(
            last_received_s=None, now_s=10.0, timeout_s=0.3
        ))

    def test_boundary_is_fresh_but_older_message_is_stale(self):
        self.assertTrue(receive_timestamp_is_fresh(
            last_received_s=9.7, now_s=10.0, timeout_s=0.3
        ))
        self.assertFalse(receive_timestamp_is_fresh(
            last_received_s=9.699, now_s=10.0, timeout_s=0.3
        ))

    def test_future_timestamp_is_rejected_as_not_fresh(self):
        self.assertFalse(receive_timestamp_is_fresh(
            last_received_s=10.1, now_s=10.0, timeout_s=0.3
        ))

    def test_invalid_time_configuration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "now_s"):
            receive_timestamp_is_fresh(
                last_received_s=1.0, now_s=float("nan"), timeout_s=0.3
            )
        with self.assertRaisesRegex(ValueError, "timeout_s"):
            receive_timestamp_is_fresh(
                last_received_s=1.0, now_s=2.0, timeout_s=0.0
            )


class RobotStatusCommunicationTests(unittest.TestCase):
    def test_mock_mode_is_healthy_without_serial_status(self):
        self.assertTrue(robot_status_communication_ok(
            mock_mode=True, serial_open=False, last_decoded_status_s=None,
            now_s=10.0, timeout_s=0.3,
        ))

    def test_real_mode_requires_an_open_serial_port(self):
        self.assertFalse(robot_status_communication_ok(
            mock_mode=False, serial_open=False, last_decoded_status_s=9.9,
            now_s=10.0, timeout_s=0.3,
        ))

    def test_generic_transport_activity_cannot_replace_decoded_status(self):
        self.assertFalse(robot_status_communication_ok(
            mock_mode=False, serial_open=True, last_decoded_status_s=None,
            now_s=10.0, timeout_s=0.3,
        ))

    def test_real_mode_accepts_only_a_fresh_decoded_status(self):
        self.assertTrue(robot_status_communication_ok(
            mock_mode=False, serial_open=True, last_decoded_status_s=9.8,
            now_s=10.0, timeout_s=0.3,
        ))
        self.assertFalse(robot_status_communication_ok(
            mock_mode=False, serial_open=True, last_decoded_status_s=9.0,
            now_s=10.0, timeout_s=0.3,
        ))


class RobotBridgeDispatchSafetyTests(unittest.TestCase):
    @staticmethod
    def _robot_bridge_tree():
        node_path = (
            Path(__file__).resolve().parents[1]
            / "ros2_ws" / "src" / "robot_bridge" / "robot_bridge" / "node.py"
        )
        return ast.parse(node_path.read_text(encoding="utf-8"))

    def test_placeholder_dispatch_cannot_mark_status_as_decoded(self):
        tree = self._robot_bridge_tree()
        dispatch = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_dispatch_frame"
        )

        assigned_attributes = {
            node.attr
            for node in ast.walk(dispatch)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
        }
        called_methods = {
            node.func.attr
            for node in ast.walk(dispatch)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

        self.assertNotIn("last_decoded_status_rx", assigned_attributes)
        self.assertNotIn("_on_status_boot_id", called_methods)

    def test_external_shutdown_is_a_clean_exit_path(self):
        tree = self._robot_bridge_tree()
        main = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        handled_names = set()
        for handler in (
            node for node in ast.walk(main) if isinstance(node, ast.ExceptHandler)
        ):
            if isinstance(handler.type, ast.Name):
                handled_names.add(handler.type.id)
            elif isinstance(handler.type, ast.Tuple):
                handled_names.update(
                    item.id for item in handler.type.elts if isinstance(item, ast.Name)
                )

        self.assertIn("KeyboardInterrupt", handled_names)
        self.assertIn("ExternalShutdownException", handled_names)

    def test_cmd_vel_gating_checks_communication_ok(self):
        """Undecoded STATUS must not unlock non-zero cmd_vel.

        _on_cmd_vel must guard on _communication_ok so that a handshake
        completed by an undecoded 0x12 envelope cannot forward non-zero
        velocity to the serial port.
        """
        tree = self._robot_bridge_tree()
        on_cmd_vel = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_on_cmd_vel"
        )
        referenced_attrs = {
            node.attr
            for node in ast.walk(on_cmd_vel)
            if isinstance(node, ast.Attribute)
        }
        self.assertIn(
            "_communication_ok", referenced_attrs,
            "_on_cmd_vel must gate on _communication_ok before forwarding velocity",
        )

    def test_tick_stores_communication_ok_state(self):
        """_tick must update _communication_ok so _on_cmd_vel can read it."""
        tree = self._robot_bridge_tree()
        tick = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_tick"
        )
        assigned_attrs = {
            node.attr
            for node in ast.walk(tick)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
        }
        self.assertIn(
            "_communication_ok", assigned_attrs,
            "_tick must store _communication_ok for _on_cmd_vel gating",
        )

    def test_missing_serial_device_is_contained_by_bridge(self):
        tree = self._robot_bridge_tree()
        open_serial = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_open_serial"
        )
        handled_names = set()
        assigns_serial_none = False
        for node in ast.walk(open_serial):
            if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Tuple):
                handled_names.update(
                    item.id for item in node.type.elts if isinstance(item, ast.Name)
                )
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and node.value.value is None
            ):
                assigns_serial_none |= any(
                    isinstance(target, ast.Attribute)
                    and target.attr == "serial"
                    for target in node.targets
                )

        self.assertIn("OSError", handled_names)
        self.assertTrue(assigns_serial_none)

    def test_serial_io_routes_through_safe_helpers(self):
        tree = self._robot_bridge_tree()
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in ("_safe_serial_write", "_safe_serial_read"):
            self.assertIn(name, methods, f"{name} must be defined")
            handled = {
                handler.type.id
                for handler in ast.walk(methods[name])
                if isinstance(handler, ast.ExceptHandler)
                and isinstance(handler.type, ast.Name)
            }
            self.assertIn("OSError", handled, f"{name} must catch OSError")

        for name in ("_on_cmd_vel", "_run_handshake", "_tick"):
            direct_io = {
                (call.func.value.attr, call.func.attr)
                for call in ast.walk(methods[name])
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Attribute)
                and call.func.value.attr == "serial"
                and call.func.attr in ("write", "read")
            }
            self.assertFalse(
                direct_io, f"{name} must not call self.serial.write/read directly"
            )


class RuntimeEvidencePolicyTests(unittest.TestCase):
    def test_mock_runtime_accepts_mock_evidence(self):
        validate_runtime_evidence_policy(
            runtime_mode="mock", placement_evidence_policy="mock_qualified"
        )

    def test_field_runtime_accepts_unavailable_evidence(self):
        validate_runtime_evidence_policy(
            runtime_mode="field", placement_evidence_policy="unavailable"
        )

    def test_field_runtime_rejects_every_mock_evidence_policy(self):
        for policy in ("mock_qualified", "mock_failed"):
            with self.subTest(policy=policy), self.assertRaisesRegex(
                ValueError, "field runtime cannot use simulated"
            ):
                validate_runtime_evidence_policy(
                    runtime_mode="field", placement_evidence_policy=policy
                )

    def test_unknown_runtime_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "runtime_mode"):
            validate_runtime_evidence_policy(
                runtime_mode="production", placement_evidence_policy="unavailable"
            )


class RobotBridgeCmdVelBehavioralTests(unittest.TestCase):
    """Behavioral: exercise _on_cmd_vel gating with real state combinations.

    AST tests only verify that _communication_ok is *mentioned* in the code.
    These tests verify that the gating *behaves* correctly at runtime —
    wrong polarity (``if self._communication_ok`` instead of
    ``if not self._communication_ok``) would still pass AST checks but
    would be caught here.

    These tests require rclpy (ROS 2). On platforms without rclpy they
    are skipped — the AST-level tests in RobotBridgeDispatchSafetyTests
    still protect the structural contract.
    """

    @classmethod
    def setUpClass(cls):
        cls._skip_reason = None
        try:
            import rclpy  # noqa: F401
        except ModuleNotFoundError as exc:
            cls._skip_reason = f"rclpy unavailable ({exc}); skipping behavioral tests"
            return
        try:
            from robot_bridge.node import (
                RobotBridge,
                _HANDSHAKE_HANDSHAKING,
                _HANDSHAKE_READY,
            )
        except ModuleNotFoundError as exc:
            cls._skip_reason = f"robot_bridge unavailable ({exc}); skipping behavioral tests"
            return

        cls.RobotBridge = RobotBridge
        cls._HANDSHAKE_READY = _HANDSHAKE_READY
        cls._HANDSHAKE_HANDSHAKING = _HANDSHAKE_HANDSHAKING

    def setUp(self):
        if self._skip_reason:
            self.skipTest(self._skip_reason)

        from unittest.mock import MagicMock
        from robogame_core.models import Velocity2D

        bridge = self.RobotBridge.__new__(self.RobotBridge)
        bridge._handshake_state = self._HANDSHAKE_READY
        bridge._communication_ok = True
        bridge.velocity = Velocity2D(0.0, 0.0, 0.0)
        bridge.serial = None
        bridge.sequence = 0
        bridge.last_command = 0.0
        bridge._cmd_vel_block_warned = False
        bridge._logger = MagicMock()
        self.bridge = bridge
        self.Velocity2D = Velocity2D

    def _cmd_vel_msg(self, vx=0.5, vy=0.0, wz=0.0):
        from unittest.mock import MagicMock

        msg = MagicMock()
        msg.linear.x = vx
        msg.linear.y = vy
        msg.angular.z = wz
        return msg

    # -- normal path -------------------------------------------------------

    def test_ready_and_communication_ok_passes_velocity(self):
        self.bridge._handshake_state = self._HANDSHAKE_READY
        self.bridge._communication_ok = True
        self.bridge._on_cmd_vel(self._cmd_vel_msg(0.8, 0.0, 0.0))
        self.assertAlmostEqual(self.bridge.velocity.vx, 0.8)

    def test_ready_and_communication_ok_passes_angular(self):
        self.bridge._handshake_state = self._HANDSHAKE_READY
        self.bridge._communication_ok = True
        self.bridge._on_cmd_vel(self._cmd_vel_msg(0.0, 0.0, 1.2))
        self.assertAlmostEqual(self.bridge.velocity.wz, 1.2)

    # -- undecoded STATUS: the exact gap from the handoff document ---------

    def test_undecoded_status_blocks_velocity_even_when_handshake_ready(self):
        """Section 6.1: 0x12 envelope completed handshake but payload undecoded."""
        self.bridge._handshake_state = self._HANDSHAKE_READY
        self.bridge._communication_ok = False
        self.bridge._on_cmd_vel(self._cmd_vel_msg(0.5, -0.2, 0.5))
        self.assertAlmostEqual(self.bridge.velocity.vx, 0.0)
        self.assertAlmostEqual(self.bridge.velocity.vy, 0.0)
        self.assertAlmostEqual(self.bridge.velocity.wz, 0.0)

    # -- handshake not ready -----------------------------------------------

    def test_handshaking_blocks_velocity(self):
        self.bridge._handshake_state = self._HANDSHAKE_HANDSHAKING
        self.bridge._communication_ok = True
        self.bridge._on_cmd_vel(self._cmd_vel_msg(0.5, 0.0, 0.0))
        self.assertAlmostEqual(self.bridge.velocity.vx, 0.0)

    def test_both_conditions_fail_blocks_velocity(self):
        self.bridge._handshake_state = self._HANDSHAKE_HANDSHAKING
        self.bridge._communication_ok = False
        self.bridge._on_cmd_vel(self._cmd_vel_msg(0.3, 0.1, -0.2))
        self.assertAlmostEqual(self.bridge.velocity.vx, 0.0)

    # -- zero velocity still passes when both gates are satisfied ----------

    def test_zero_velocity_passes_when_ready_and_ok(self):
        self.bridge._handshake_state = self._HANDSHAKE_READY
        self.bridge._communication_ok = True
        self.bridge._on_cmd_vel(self._cmd_vel_msg(0.0, 0.0, 0.0))
        self.assertAlmostEqual(self.bridge.velocity.vx, 0.0)
        self.assertAlmostEqual(self.bridge.velocity.wz, 0.0)


class _FakeSerial:
    def __init__(self, read_data=b"", write_error=None, read_error=None):
        self._read_data = read_data
        self._write_error = write_error
        self._read_error = read_error
        self.writes = []
        self.closed = False
        self.in_waiting = len(read_data)

    def write(self, data):
        if self._write_error is not None:
            raise self._write_error
        self.writes.append(data)
        return len(data)

    def read(self, n):
        if self._read_error is not None:
            raise self._read_error
        return self._read_data[:n]

    def close(self):
        self.closed = True


class RobotBridgeSerialSafetyTests(unittest.TestCase):
    """Serial read/write failures must be contained, not crash the bridge.

    USB unplug raises serial.SerialException (an OSError subclass) on the next
    read/write. The safe helpers must catch it, close the port, and return a
    benign value so the timer callback keeps running and cmd_vel is gated off.
    """

    @classmethod
    def setUpClass(cls):
        cls._skip_reason = None
        try:
            import rclpy  # noqa: F401
        except ModuleNotFoundError as exc:
            cls._skip_reason = f"rclpy unavailable ({exc}); skipping"
            return
        try:
            from robot_bridge.node import RobotBridge
        except ModuleNotFoundError as exc:
            cls._skip_reason = f"robot_bridge unavailable ({exc}); skipping"
            return
        cls.RobotBridge = RobotBridge

    def setUp(self):
        if self._skip_reason:
            self.skipTest(self._skip_reason)
        from unittest.mock import MagicMock

        bridge = self.RobotBridge.__new__(self.RobotBridge)
        bridge.serial = None
        bridge.get_logger = MagicMock(return_value=MagicMock())
        self.bridge = bridge

    def test_write_success_returns_true(self):
        serial = _FakeSerial()
        self.bridge.serial = serial
        self.assertTrue(self.bridge._safe_serial_write(b"\x01\x02"))
        self.assertEqual(serial.writes, [b"\x01\x02"])
        self.assertIsNotNone(self.bridge.serial)

    def test_write_failure_closes_serial_and_returns_false(self):
        serial = _FakeSerial(write_error=OSError("device disconnected"))
        self.bridge.serial = serial
        self.assertFalse(self.bridge._safe_serial_write(b"\x01"))
        self.assertIsNone(self.bridge.serial)
        self.assertTrue(serial.closed)

    def test_write_when_serial_none_returns_false(self):
        self.assertFalse(self.bridge._safe_serial_write(b"\x01"))

    def test_read_success_returns_data(self):
        serial = _FakeSerial(read_data=b"\xaa\xbb")
        self.bridge.serial = serial
        self.assertEqual(self.bridge._safe_serial_read(), b"\xaa\xbb")

    def test_read_failure_closes_serial_and_returns_empty(self):
        serial = _FakeSerial(read_data=b"\x00\x00", read_error=OSError("device disconnected"))
        self.bridge.serial = serial
        self.assertEqual(self.bridge._safe_serial_read(), b"")
        self.assertIsNone(self.bridge.serial)
        self.assertTrue(serial.closed)

    def test_read_when_serial_none_returns_empty(self):
        self.assertEqual(self.bridge._safe_serial_read(), b"")


if __name__ == "__main__":
    unittest.main()
