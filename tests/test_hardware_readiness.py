import ast
import unittest
from pathlib import Path

from robogame_core.hardware_readiness import (
    receive_timestamp_is_fresh,
    robot_status_communication_ok,
    validate_runtime_evidence_policy,
)


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
    def test_placeholder_dispatch_cannot_mark_status_as_decoded(self):
        node_path = (
            Path(__file__).resolve().parents[1]
            / "ros2_ws" / "src" / "robot_bridge" / "robot_bridge" / "node.py"
        )
        tree = ast.parse(node_path.read_text(encoding="utf-8"))
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


if __name__ == "__main__":
    unittest.main()
