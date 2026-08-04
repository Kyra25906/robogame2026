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
        self.command_pub = self.create_publisher(String, "/manipulator/command", 10)
        self.create_subscription(RobotStatus, "/robot/status", self._on_status, 10)
        self.create_subscription(String, "/manipulator/result", self._on_result, 10)
        self.started_at = time.monotonic()
        self.phase = "WAIT_STATUS"
        self.pick_evidence_seen = False
        self.place_evidence_seen = False
        self.exit_code: int | None = None
        self.create_timer(0.05, self._tick)

    def _publish_command(self, command: str) -> None:
        self.get_logger().info(f"publishing {command}")
        self.command_pub.publish(String(data=command))

    def _on_status(self, msg: RobotStatus) -> None:
        if self.phase == "WAIT_STATUS" and msg.communication_ok:
            self.phase = "WAIT_PICK_RESULT"
            self._publish_command("PICK_ORANGE")
        elif self.phase == "WAIT_PICK_RESULT":
            self.pick_evidence_seen |= msg.cube_present and msg.gripper_closed
        elif self.phase == "WAIT_PICK_EVIDENCE":
            if msg.cube_present and msg.gripper_closed:
                self._start_place()
        elif self.phase == "WAIT_PLACE_RESULT":
            self.place_evidence_seen |= not msg.cube_present and not msg.gripper_closed
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
        if self.phase == "WAIT_PLACE_RESULT":
            if msg.data != "SUCCESS":
                self._fail(f"place failed: {msg.data}")
                return
            if self.place_evidence_seen:
                self._pass()
            else:
                self.phase = "WAIT_PLACE_EVIDENCE"

    def _start_place(self) -> None:
        self.phase = "WAIT_PLACE_RESULT"
        self.place_evidence_seen = False
        self._publish_command("PLACE_ORANGE")

    def _pass(self) -> None:
        self.get_logger().info("PASS: strict mock pick/place cycle completed")
        self.exit_code = 0
        rclpy.shutdown()

    def _fail(self, reason: str) -> None:
        self.get_logger().error(f"FAIL: {reason}")
        self.exit_code = 1
        rclpy.shutdown()

    def _tick(self) -> None:
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
