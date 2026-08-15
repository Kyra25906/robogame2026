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

    def test_dispatch_marks_status_decoded_only_after_decoder_call(self):
        tree = self._robot_bridge_tree()
        dispatch = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_dispatch_frame"
        )

        calls = [
            node for node in ast.walk(dispatch)
            if isinstance(node, ast.Call)
        ]
        called_names = {
            node.func.id for node in calls if isinstance(node.func, ast.Name)
        }
        called_methods = {
            node.func.attr for node in calls if isinstance(node.func, ast.Attribute)
        }
        assigned_attributes = {
            node.attr for node in ast.walk(dispatch)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
        }

        self.assertIn("decode_status", called_names)
        self.assertIn("last_decoded_status_rx", assigned_attributes)
        self.assertIn("_on_status_boot_id", called_methods)

        handlers = [
            node for node in ast.walk(dispatch)
            if isinstance(node, ast.ExceptHandler)
        ]
        self.assertTrue(any(
            isinstance(handler.type, ast.Name)
            and handler.type.id == "ProtocolError"
            for handler in handlers
        ))

    def test_dispatch_decodes_odom_before_refreshing_freshness(self):
        tree = self._robot_bridge_tree()
        dispatch = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_dispatch_frame"
        )
        called_names = {
            node.func.id for node in ast.walk(dispatch)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assigned_attributes = {
            node.attr for node in ast.walk(dispatch)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
        }
        self.assertIn("decode_odom", called_names)
        self.assertIn("_latest_odom", assigned_attributes)
        self.assertIn("last_decoded_odom_rx", assigned_attributes)

    def test_dispatch_decodes_imu_before_refreshing_freshness(self):
        tree = self._robot_bridge_tree()
        dispatch = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_dispatch_frame"
        )
        called_names = {
            node.func.id for node in ast.walk(dispatch)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assigned_attributes = {
            node.attr for node in ast.walk(dispatch)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
        }
        self.assertIn("decode_imu", called_names)
        self.assertIn("_latest_imu", assigned_attributes)
        self.assertIn("last_decoded_imu_rx", assigned_attributes)

    def test_tick_marks_missing_or_stale_real_imu_unavailable(self):
        tree = self._robot_bridge_tree()
        tick = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_tick"
        )
        covariance_assignments = [
            node for node in ast.walk(tick)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.UnaryOp)
            and isinstance(node.value.op, ast.USub)
            and isinstance(node.value.operand, ast.Constant)
            and node.value.operand.value == 1.0
        ]
        refs = {
            node.attr for node in ast.walk(tick)
            if isinstance(node, ast.Attribute)
        }
        self.assertIn("last_decoded_imu_rx", refs)
        self.assertIn("_latest_imu", refs)
        self.assertTrue(covariance_assignments)

    def test_real_status_defaults_to_calibrating_until_decoded(self):
        tree = self._robot_bridge_tree()
        tick = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_tick"
        )
        calibrating_assignments = [
            node for node in ast.walk(tick)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute) and target.attr == "calibrating"
                for target in node.targets
            )
        ]
        self.assertTrue(any(
            isinstance(node.value, ast.IfExp)
            and isinstance(node.value.orelse, ast.Constant)
            and node.value.orelse.value is True
            for node in calibrating_assignments
        ))

    def test_tick_uses_measured_velocity_for_real_odometry(self):
        tree = self._robot_bridge_tree()
        tick = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_tick"
        )
        measured_assignments = [
            node for node in ast.walk(tick)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "measured_velocity"
                    for target in node.targets)
        ]
        self.assertGreaterEqual(len(measured_assignments), 3)
        integrator_calls = [
            node for node in ast.walk(tick)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
        ]
        self.assertTrue(any(
            call.args and isinstance(call.args[0], ast.Name)
            and call.args[0].id == "measured_velocity"
            for call in integrator_calls
        ))

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

    def test_timeout_stop_gates_on_trust(self):
        tree = self._robot_bridge_tree()
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn("_send_zero_velocity", methods, "_send_zero_velocity must be defined")
        stop = methods["_send_zero_velocity"]
        refs = {node.attr for node in ast.walk(stop) if isinstance(node, ast.Attribute)}
        self.assertIn("_handshake_state", refs)
        self.assertIn("_communication_ok", refs)

        tick_refs = {
            node.func.attr
            for node in ast.walk(methods["_tick"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn(
            "_send_zero_velocity", tick_refs,
            "_tick must call _send_zero_velocity on timeout",
        )

    def test_heartbeat_is_periodic_and_requires_status_trust(self):
        tree = self._robot_bridge_tree()
        methods = {
            node.name: node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        heartbeat = methods["_send_heartbeat"]
        refs = {
            node.attr for node in ast.walk(heartbeat)
            if isinstance(node, ast.Attribute)
        }
        called_names = {
            node.func.id for node in ast.walk(heartbeat)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("_handshake_state", refs)
        self.assertIn("_last_heartbeat_tx", refs)
        self.assertIn("_communication_ok", refs)
        self.assertIn("encode_heartbeat", called_names)

        tick_calls = {
            node.func.attr for node in ast.walk(methods["_tick"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn("_send_heartbeat", tick_calls)

    def test_handshake_requires_decoded_ack_and_pending_sequence(self):
        tree = self._robot_bridge_tree()
        methods = {
            node.name: node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        accept = methods["_accept_frame_for_handshake"]
        called_names = {
            node.func.id for node in ast.walk(accept)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        refs = {
            node.attr for node in ast.walk(accept)
            if isinstance(node, ast.Attribute)
        }
        compared_constants = {
            node.value for node in ast.walk(accept)
            if isinstance(node, ast.Constant)
        }
        self.assertIn("decode_ack", called_names)
        self.assertIn("_handshake_pending_sequence", refs)
        self.assertIn(0, compared_constants)

    def test_periodic_status_cannot_complete_handshake(self):
        tree = self._robot_bridge_tree()
        accept = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_accept_frame_for_handshake"
        )
        refs = {
            node.attr for node in ast.walk(accept)
            if isinstance(node, ast.Attribute)
        }
        self.assertNotIn("last_decoded_status_rx", refs)
        self.assertNotIn("_latest_status", refs)

    def test_handshake_has_bounded_attempts_and_real_timeout(self):
        tree = self._robot_bridge_tree()
        run = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_run_handshake"
        )
        refs = {
            node.attr for node in ast.walk(run)
            if isinstance(node, ast.Attribute)
        }
        names = {
            node.id for node in ast.walk(run) if isinstance(node, ast.Name)
        }
        self.assertIn("_handshake_retries", refs)
        self.assertIn("_HANDSHAKE_MAX_RETRIES", names)
        self.assertIn("_HANDSHAKE_TIMEOUT_S", names)

    def test_real_stop_routes_through_tested_fail_safe_dispatcher(self):
        tree = self._robot_bridge_tree()
        mechanism = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_mechanism"
        )
        called_names = {
            node.func.id for node in ast.walk(mechanism)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assigned_attrs = {
            node.attr for node in ast.walk(mechanism)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
        }
        self.assertIn("dispatch_stop_frames", called_names)
        self.assertIn("velocity", assigned_attrs)
        self.assertIn("sequence", assigned_attrs)

    def test_tick_attempts_bounded_serial_reconnect(self):
        tree = self._robot_bridge_tree()
        methods = {
            node.name: node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        reconnect = methods["_maybe_reconnect_serial"]
        refs = {
            node.attr for node in ast.walk(reconnect)
            if isinstance(node, ast.Attribute)
        }
        names = {
            node.id for node in ast.walk(reconnect) if isinstance(node, ast.Name)
        }
        tick_calls = {
            node.func.attr for node in ast.walk(methods["_tick"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn("_last_reconnect_attempt", refs)
        self.assertIn("_SERIAL_RECONNECT_INTERVAL_S", names)
        self.assertIn("_maybe_reconnect_serial", tick_calls)

    def test_disconnect_reset_invalidates_commands_and_all_telemetry(self):
        tree = self._robot_bridge_tree()
        reset = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_reset_real_transport_state"
        )
        assigned_attrs = {
            node.attr for node in ast.walk(reset)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
        }
        for required in (
            "velocity",
            "last_frame_rx",
            "last_decoded_status_rx",
            "last_decoded_odom_rx",
            "last_decoded_imu_rx",
            "_latest_status",
            "_latest_odom",
            "_latest_imu",
            "_communication_ok",
            "_handshake_state",
        ):
            self.assertIn(required, assigned_attrs)


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
        from robogame_core.models import Velocity2D
        from robogame_core.serial_protocol import StreamDecoder

        bridge = self.RobotBridge.__new__(self.RobotBridge)
        bridge.serial = None
        bridge.mock_mode = False
        bridge.velocity = Velocity2D(1.0, -1.0, 0.5)
        bridge.decoder = StreamDecoder()
        bridge.last_frame_rx = 1.0
        bridge.last_decoded_status_rx = 1.0
        bridge.last_decoded_odom_rx = 1.0
        bridge.last_decoded_imu_rx = 1.0
        bridge._latest_status = object()
        bridge._latest_odom = object()
        bridge._latest_imu = object()
        bridge._communication_ok = True
        bridge._handshake_state = "READY"
        bridge._handshake_last_hello = 1.0
        bridge._handshake_retries = 2
        bridge._handshake_pending_sequence = 7
        bridge._last_heartbeat_tx = 1.0
        bridge._last_reconnect_attempt = 0.0
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

    def test_disconnect_invalidates_old_session_state(self):
        serial = _FakeSerial(write_error=OSError("device disconnected"))
        self.bridge.serial = serial

        self.assertFalse(self.bridge._safe_serial_write(b"x"))

        self.assertIsNone(self.bridge.serial)
        self.assertEqual(
            (self.bridge.velocity.vx, self.bridge.velocity.vy, self.bridge.velocity.wz),
            (0.0, 0.0, 0.0),
        )
        self.assertFalse(self.bridge._communication_ok)
        self.assertEqual(self.bridge._handshake_state, "HANDSHAKING")
        self.assertIsNone(self.bridge.last_decoded_status_rx)
        self.assertIsNone(self.bridge.last_decoded_odom_rx)
        self.assertIsNone(self.bridge.last_decoded_imu_rx)

    def test_reconnect_attempt_is_throttled(self):
        from unittest.mock import MagicMock

        self.bridge.serial = None
        self.bridge._last_reconnect_attempt = 10.0
        self.bridge._open_serial = MagicMock(return_value=False)

        self.bridge._maybe_reconnect_serial(10.5)
        self.bridge._open_serial.assert_not_called()
        self.bridge._maybe_reconnect_serial(11.0)
        self.bridge._open_serial.assert_called_once_with()

    def test_reconnect_is_not_attempted_while_serial_is_open(self):
        from unittest.mock import MagicMock

        self.bridge.serial = _FakeSerial()
        self.bridge._open_serial = MagicMock(return_value=True)
        self.bridge._maybe_reconnect_serial(100.0)
        self.bridge._open_serial.assert_not_called()


class RobotBridgeTimeoutStopTests(unittest.TestCase):
    """Command timeout must send an explicit zero-velocity frame to the MCU.

    The MCU keeps executing its last non-zero velocity until its own watchdog
    fires (~350ms+). When our command_timeout expires, _send_zero_velocity must
    close that gap — but only when the link is trusted (handshake READY +
    communication_ok), mirroring _on_cmd_vel's gating.
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
            from robot_bridge.node import RobotBridge, _HANDSHAKE_READY
        except ModuleNotFoundError as exc:
            cls._skip_reason = f"robot_bridge unavailable ({exc}); skipping"
            return
        cls.RobotBridge = RobotBridge
        cls._HANDSHAKE_READY = _HANDSHAKE_READY

    def setUp(self):
        if self._skip_reason:
            self.skipTest(self._skip_reason)
        from unittest.mock import MagicMock

        bridge = self.RobotBridge.__new__(self.RobotBridge)
        bridge.serial = None
        bridge.sequence = 0
        bridge.get_logger = MagicMock(return_value=MagicMock())
        self.bridge = bridge

    def test_sends_zero_velocity_when_link_trusted(self):
        serial = _FakeSerial()
        self.bridge.serial = serial
        self.bridge._handshake_state = self._HANDSHAKE_READY
        self.bridge._communication_ok = True
        self.bridge._send_zero_velocity()
        self.assertEqual(len(serial.writes), 1)
        self.assertEqual(self.bridge.sequence, 1)

    def test_blocks_zero_velocity_when_communication_untrusted(self):
        serial = _FakeSerial()
        self.bridge.serial = serial
        self.bridge._handshake_state = self._HANDSHAKE_READY
        self.bridge._communication_ok = False
        self.bridge._send_zero_velocity()
        self.assertEqual(len(serial.writes), 0)
        self.assertEqual(self.bridge.sequence, 0)

    def test_blocks_zero_velocity_when_no_serial(self):
        self.bridge.serial = None
        self.bridge._handshake_state = self._HANDSHAKE_READY
        self.bridge._communication_ok = True
        self.bridge._send_zero_velocity()
        self.assertEqual(self.bridge.sequence, 0)

    def test_status_timeout_sends_stop_and_invalidates_old_session(self):
        from robogame_core.models import Velocity2D
        from robogame_core.serial_protocol import (
            MSG_TYPE_EMERGENCY_STOP,
            MSG_TYPE_VELOCITY,
            StreamDecoder,
        )

        serial = _FakeSerial()
        self.bridge.serial = serial
        self.bridge.mock_mode = False
        self.bridge.velocity = Velocity2D(0.6, 0.0, 0.2)
        self.bridge.decoder = StreamDecoder()
        self.bridge.last_frame_rx = 1.0
        self.bridge.last_decoded_status_rx = 1.0
        self.bridge.last_decoded_odom_rx = 1.0
        self.bridge.last_decoded_imu_rx = 1.0
        self.bridge._latest_status = object()
        self.bridge._latest_odom = object()
        self.bridge._latest_imu = object()
        self.bridge._communication_ok = False
        self.bridge._handshake_state = self._HANDSHAKE_READY
        self.bridge._handshake_last_hello = 1.0
        self.bridge._handshake_retries = 1
        self.bridge._handshake_pending_sequence = None
        self.bridge._last_heartbeat_tx = 1.0

        self.bridge._enter_status_timeout_failsafe()

        frames = StreamDecoder().feed(b"".join(serial.writes))
        self.assertEqual(
            [frame.message_type for frame in frames],
            [MSG_TYPE_VELOCITY, MSG_TYPE_EMERGENCY_STOP],
        )
        self.assertEqual(
            (self.bridge.velocity.vx, self.bridge.velocity.vy, self.bridge.velocity.wz),
            (0.0, 0.0, 0.0),
        )
        self.assertEqual(self.bridge._handshake_state, "HANDSHAKING")
        self.assertFalse(self.bridge._communication_ok)

    def test_heartbeat_stops_when_status_is_untrusted(self):
        serial = _FakeSerial()
        self.bridge.serial = serial
        self.bridge.mock_mode = False
        self.bridge._handshake_state = self._HANDSHAKE_READY
        self.bridge._communication_ok = False
        self.bridge._last_heartbeat_tx = 0.0

        self.bridge._send_heartbeat(10.0)

        self.assertEqual(serial.writes, [])

    def test_shutdown_sends_stop_frames_before_closing_serial(self):
        from robogame_core.models import Velocity2D
        from robogame_core.serial_protocol import (
            MSG_TYPE_EMERGENCY_STOP,
            MSG_TYPE_VELOCITY,
            StreamDecoder,
        )

        events = []

        class OrderedSerial(_FakeSerial):
            def write(self, data):
                events.append(("write", data))
                return super().write(data)

            def close(self):
                events.append(("close", None))
                super().close()

        serial = OrderedSerial()
        self.bridge.serial = serial
        self.bridge.mock_mode = False
        self.bridge.velocity = Velocity2D(0.4, 0.0, -0.2)

        self.bridge.shutdown_transport()

        frames = StreamDecoder().feed(
            b"".join(data for kind, data in events if kind == "write")
        )
        self.assertEqual(
            [frame.message_type for frame in frames],
            [MSG_TYPE_VELOCITY, MSG_TYPE_EMERGENCY_STOP],
        )
        self.assertEqual([kind for kind, _data in events], ["write", "write", "close"])
        self.assertTrue(serial.closed)
        self.assertIsNone(self.bridge.serial)
        self.assertEqual(
            (self.bridge.velocity.vx, self.bridge.velocity.vy, self.bridge.velocity.wz),
            (0.0, 0.0, 0.0),
        )


if __name__ == "__main__":
    unittest.main()
