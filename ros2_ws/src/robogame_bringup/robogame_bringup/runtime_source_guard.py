from __future__ import annotations

import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from robogame_core.runtime_source import (
    CRITICAL_TOPICS,
    PublisherIdentity,
    validate_runtime_sources,
)
from robogame_interfaces.msg import RobotStatus


class RuntimeSourceGuard(Node):
    def __init__(self) -> None:
        super().__init__("runtime_source_guard")
        self.declare_parameter("expected_mode", "field")
        self.declare_parameter("sample_duration_s", 1.0)
        self.declare_parameter("stay_alive_on_pass", False)
        self.expected_mode = str(self.get_parameter("expected_mode").value)
        self.sample_duration_s = float(self.get_parameter("sample_duration_s").value)
        self.stay_alive_on_pass = bool(
            self.get_parameter("stay_alive_on_pass").value
        )
        if self.expected_mode not in {"mock", "field"}:
            raise ValueError("expected_mode must be mock or field")
        if self.sample_duration_s <= 0.0:
            raise ValueError("sample_duration_s must be positive")
        self.started_at = time.monotonic()
        self.status_details: list[str] = []
        self.exit_code: int | None = None
        self.create_subscription(RobotStatus, "/robot/status", self._on_status, 20)
        self.create_timer(0.05, self._tick)

    def _on_status(self, msg: RobotStatus) -> None:
        self.status_details.append(msg.detail)

    def _tick(self) -> None:
        if self.exit_code is not None:
            return
        if time.monotonic() - self.started_at < self.sample_duration_s:
            return
        publishers = {
            topic: [
                PublisherIdentity(info.node_name, info.node_namespace)
                for info in self.get_publishers_info_by_topic(topic)
            ]
            for topic in CRITICAL_TOPICS
        }
        result = validate_runtime_sources(
            self.expected_mode, publishers, self.status_details
        )
        if result.passed:
            self.get_logger().info(
                f"PASS: runtime sources are exclusively {self.expected_mode}; "
                "hardware health must be checked separately"
            )
            self.exit_code = 0
            if not self.stay_alive_on_pass:
                rclpy.shutdown()
        else:
            for issue in result.issues:
                self.get_logger().error(f"SOURCE_GUARD_FAIL: {issue}")
            self.exit_code = 1
            rclpy.shutdown()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = RuntimeSourceGuard()
    try:
        rclpy.spin(node)
    except (ExternalShutdownException, KeyboardInterrupt):
        pass
    finally:
        exit_code = 1 if node.exit_code is None else node.exit_code
        try:
            node.destroy_node()
        except KeyboardInterrupt:
            pass
        if rclpy.ok():
            rclpy.shutdown()
    raise SystemExit(exit_code)
