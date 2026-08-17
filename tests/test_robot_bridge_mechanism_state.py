import ast
from pathlib import Path
import unittest


NODE_PATH = (
    Path(__file__).parents[1]
    / "ros2_ws"
    / "src"
    / "robot_bridge"
    / "robot_bridge"
    / "node.py"
)


class RobotBridgeMechanismStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = NODE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_real_services_use_v1_tracker_instead_of_placeholder_failure(self):
        self.assertIn("MechanismCommandTracker(", self.source)
        self.assertIn("MSG_TYPE_MECHANISM_COMMAND", self.source)
        self.assertNotIn(
            "real mechanism payload is disabled until the MCU contract is signed",
            self.source,
        )

    def test_incoming_ack_and_mechanism_status_reach_tracker(self):
        self.assertIn("tracker.handle_ack(", self.source)
        self.assertIn("decode_mechanism_status(frame.payload)", self.source)
        self.assertIn("tracker.handle_status(", self.source)

    def test_real_motion_is_guarded_by_trusted_status_and_local_authorization(self):
        ready_method = self._method_source("_real_mechanism_ready")
        for required in (
            "_HANDSHAKE_READY",
            "_communication_ok",
            "STATUS_EMERGENCY_STOP",
            "STATUS_MECHANISM_FAULT",
            "STATUS_PHYSICAL_START",
        ):
            self.assertIn(required, ready_method)

    def test_executor_can_process_serial_while_service_waits(self):
        self.assertIn("MultiThreadedExecutor(num_threads=2)", self.source)
        self.assertIn("ReentrantCallbackGroup()", self.source)

    def test_disconnect_restart_and_global_stop_cancel_pending_command(self):
        self.assertGreaterEqual(
            self.source.count("_cancel_pending_mechanism("),
            6,
        )
        self.assertIn("STM32 restarted during mechanism command", self.source)
        self.assertIn("global chassis STOP interrupted mechanism command", self.source)

    def test_transport_timing_and_retry_policy_are_ros_parameters(self):
        for parameter in (
            "mechanism_ack_timeout_s",
            "mechanism_status_timeout_s",
            "mechanism_max_attempts",
        ):
            self.assertIn(f'self.declare_parameter("{parameter}"', self.source)
            self.assertIn(f"self.{parameter}", self.source)
        execute_method = self._method_source("_execute_real_mechanism")
        self.assertIn("ack_timeout_s=self.mechanism_ack_timeout_s", execute_method)
        self.assertIn("status_timeout_s=self.mechanism_status_timeout_s", execute_method)
        self.assertIn("max_attempts=self.mechanism_max_attempts", execute_method)

    def _method_source(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return ast.get_source_segment(self.source, node)
        self.fail(f"method {name} not found")


if __name__ == "__main__":
    unittest.main()
