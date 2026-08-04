from __future__ import annotations

import time

import rclpy
from rclpy.node import Node
from robogame_interfaces.msg import RobotStatus
from std_msgs.msg import String


class ManipulatorMockSmoke(Node):
    """Drive and verify one strict mock pick/place cycle."""

    def __init__(self) -> None:
        super().__init__("manipulator_mock_smoke")
        self.declare_parameter("expected_place_result", "STABLE")
        self.declare_parameter("cancel_during_stability", False)
        self.declare_parameter("cancel_delay_s", 0.15)
        self.expected_place_result = str(
            self.get_parameter("expected_place_result").value
        )
        if self.expected_place_result not in {
            "STABLE", "FAILED", "INCONCLUSIVE", "CANCELLED"
        }:
            raise ValueError(
                "expected_place_result must be STABLE, FAILED, INCONCLUSIVE, "
                "or CANCELLED"
            )
        self.cancel_during_stability = bool(
            self.get_parameter("cancel_during_stability").value
        )
        self.cancel_delay_s = float(self.get_parameter("cancel_delay_s").value)
        if self.cancel_delay_s < 0.0:
            raise ValueError("cancel_delay_s cannot be negative")
        if self.cancel_during_stability and self.expected_place_result != "CANCELLED":
            raise ValueError(
                "cancel_during_stability requires expected_place_result=CANCELLED"
            )
        self.command_pub = self.create_publisher(String, "/manipulator/command", 10)
        self.create_subscription(RobotStatus, "/robot/status", self._on_status, 10)
        self.create_subscription(String, "/manipulator/result", self._on_result, 10)
        self.started_at = time.monotonic()
        self.phase = "WAIT_STATUS"
        self.command_ready_at = 0.0
        self.cancel_ready_at = 0.0
        self.pick_evidence_seen = False
        self.place_evidence_seen = False
        self.exit_code: int | None = None
        self.create_timer(0.05, self._tick)

    def _publish_command(self, command: str) -> None:
        self.get_logger().info(f"publishing {command}")
        self.command_pub.publish(String(data=command))

    def _on_status(self, msg: RobotStatus) -> None:
        if self.phase == "WAIT_STATUS" and msg.communication_ok:
            # Give every RobotStatus subscriber time to receive a fresh sample.
            # Publishing from the first status callback creates a startup race:
            # this smoke node may receive it before manipulator_client does.
            self.phase = "WAIT_COMMAND_READY"
            self.command_ready_at = time.monotonic() + 0.10
        elif self.phase == "WAIT_PICK_RESULT":
            self.pick_evidence_seen |= msg.cube_present and msg.gripper_closed
        elif self.phase == "WAIT_PICK_EVIDENCE":
            if msg.cube_present and msg.gripper_closed:
                self._start_place()
        elif self.phase == "WAIT_PLACE_RESULT":
            self.place_evidence_seen |= not msg.cube_present and not msg.gripper_closed
            if (
                self.cancel_during_stability
                and msg.retreat_complete
                and self.cancel_ready_at == 0.0
            ):
                # The client starts observation from this fresh retreat evidence.
                # Delay slightly so CANCEL is exercised during observation itself.
                self.cancel_ready_at = time.monotonic() + self.cancel_delay_s
        elif self.phase == "WAIT_PLACE_EVIDENCE":
            if not msg.cube_present and not msg.gripper_closed:
                self._pass()

    def _on_result(self, msg: String) -> None:
        if self.phase == "WAIT_PICK_RESULT":
            if msg.data != "SUCCESS":
                self._fail(f"pick failed: {msg.data}")
                return
            if self.pick_evidence_seen:
                self._start_place()
            else:
                self.phase = "WAIT_PICK_EVIDENCE"
            return
        if self.phase in {"WAIT_PLACE_RESULT", "WAIT_CANCEL_RESULT"}:
            terminal = msg.data.split(":", 1)[0]
            if terminal != self.expected_place_result:
                self._fail(f"place failed: {msg.data}")
                return
            if terminal != "STABLE" or self.place_evidence_seen:
                self._pass()
            else:
                self.phase = "WAIT_PLACE_EVIDENCE"

    def _start_place(self) -> None:
        self.phase = "WAIT_PLACE_RESULT"
        self.place_evidence_seen = False
        self._publish_command("PLACE_ORANGE")

    def _pass(self) -> None:
        self.get_logger().info(
            "PASS: strict mock pick/place/retreat/stability cycle completed "
            f"with expected result {self.expected_place_result}"
        )
        self.exit_code = 0
        rclpy.shutdown()

    def _fail(self, reason: str) -> None:
        self.get_logger().error(f"FAIL: {reason}")
        self.exit_code = 1
        rclpy.shutdown()

    def _tick(self) -> None:
        if (
            self.phase == "WAIT_COMMAND_READY"
            and time.monotonic() >= self.command_ready_at
        ):
            self.phase = "WAIT_PICK_RESULT"
            self._publish_command("PICK_ORANGE")
        if (
            self.phase == "WAIT_PLACE_RESULT"
            and self.cancel_ready_at > 0.0
            and time.monotonic() >= self.cancel_ready_at
        ):
            self.phase = "WAIT_CANCEL_RESULT"
            self._publish_command("CANCEL")
        if time.monotonic() - self.started_at > 15.0:
            self._fail(f"smoke test timed out in phase {self.phase}")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ManipulatorMockSmoke()
    try:
        rclpy.spin(node)
    finally:
        exit_code = 1 if node.exit_code is None else node.exit_code
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    raise SystemExit(exit_code)
