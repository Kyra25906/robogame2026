from __future__ import annotations

import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from robogame_interfaces.msg import CubeDetection, CubeDetectionArray
from robogame_interfaces.srv import ExecuteMechanism, SetLiftHeight
from std_msgs.msg import String


class ManipulatorClientNode(Node):
    """Coordinates visual approach with low-level mechanism services."""

    def __init__(self) -> None:
        super().__init__("manipulator_client")
        for name, default in {
            "target_distance_m": 0.24, "distance_tolerance_m": 0.025,
            "lateral_tolerance_m": 0.018, "kp_distance": 0.8, "kp_lateral": 1.2,
            "max_speed": 0.18, "target_stale_s": 0.5, "action_timeout_s": 12.0,
            "place_heights_m": [0.10, 0.20, 0.30],
        }.items():
            self.declare_parameter(name, default)
        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 20)
        self.result_pub = self.create_publisher(String, "/manipulator/result", 10)
        self.create_subscription(CubeDetectionArray, "/cubes", self._on_cubes, 10)
        self.create_subscription(String, "/manipulator/command", self._on_command, 10)
        self.grab = self.create_client(ExecuteMechanism, "/gripper/grab")
        self.release = self.create_client(ExecuteMechanism, "/gripper/release")
        self.lift = self.create_client(SetLiftHeight, "/lift/set_height")
        self.command: str | None = None
        self.target: CubeDetection | None = None
        self.target_time = 0.0
        self.started_at = 0.0
        self.service_future = None
        self.service_phase: str | None = None
        self.placed_layers = 0
        self.create_timer(0.05, self._tick)

    def _on_cubes(self, msg: CubeDetectionArray) -> None:
        if not self.command or not self.command.startswith("PICK_"):
            return
        desired = CubeDetection.ORANGE if self.command == "PICK_ORANGE" else CubeDetection.PURPLE
        matches = [d for d in msg.detections if d.color == desired]
        if matches:
            self.target = max(matches, key=lambda d: (d.confidence, -d.distance_m))
            self.target_time = time.monotonic()

    def _on_command(self, msg: String) -> None:
        allowed = {"PICK_ORANGE", "PICK_PURPLE", "PLACE_ORANGE", "PLACE_PURPLE"}
        if msg.data not in allowed:
            self.result_pub.publish(String(data=f"MECHANISM_ERROR: unsupported {msg.data}"))
            return
        if self.command is not None:
            self.result_pub.publish(String(data="MECHANISM_ERROR: manipulator busy"))
            return
        self.command = msg.data
        self.started_at = time.monotonic()
        self.target = None
        self.service_future = None
        self.service_phase = None

    def _publish_stop(self) -> None:
        self.cmd_pub.publish(Twist())

    def _finish(self, result: str) -> None:
        self._publish_stop()
        self.result_pub.publish(String(data=result))
        self.command = None
        self.target = None
        self.service_future = None
        self.service_phase = None

    def _start_release(self) -> None:
        request = ExecuteMechanism.Request()
        request.command, request.timeout_s = "RELEASE", 3.0
        if self.release.service_is_ready():
            self.service_phase = "RELEASING"
            self.service_future = self.release.call_async(request)

    def _tick(self) -> None:
        if self.command is None:
            return
        now = time.monotonic()
        if now - self.started_at > float(self.get_parameter("action_timeout_s").value):
            self._finish("TIMEOUT: manipulator action")
            return
        if self.service_future is not None:
            if self.service_future.done():
                response = self.service_future.result()
                if not response or not response.success:
                    self._finish("MECHANISM_ERROR: service failed")
                elif self.service_phase == "LIFTING":
                    self.service_future = None
                    self._start_release()
                elif self.service_phase == "RELEASING":
                    self.placed_layers += 1
                    self._finish("SUCCESS")
                else:
                    self._finish("SUCCESS")
            return
        if self.command.startswith("PLACE_"):
            heights = list(self.get_parameter("place_heights_m").value)
            height = heights[min(self.placed_layers, len(heights) - 1)]
            request = SetLiftHeight.Request()
            request.height_m, request.timeout_s = float(height), 3.0
            if self.lift.service_is_ready():
                self.service_phase = "LIFTING"
                self.service_future = self.lift.call_async(request)
            return
        if self.target is None or now - self.target_time > float(self.get_parameter("target_stale_s").value):
            self._publish_stop()
            return
        distance_error = self.target.distance_m - float(self.get_parameter("target_distance_m").value)
        lateral_error = self.target.lateral_m
        if (abs(distance_error) <= float(self.get_parameter("distance_tolerance_m").value)
                and abs(lateral_error) <= float(self.get_parameter("lateral_tolerance_m").value)):
            self._publish_stop()
            request = ExecuteMechanism.Request()
            request.command, request.timeout_s = "GRAB", 3.0
            if self.grab.service_is_ready():
                self.service_phase = "GRABBING"
                self.service_future = self.grab.call_async(request)
            return
        limit = float(self.get_parameter("max_speed").value)
        msg = Twist()
        msg.linear.x = max(-limit, min(limit, float(self.get_parameter("kp_distance").value) * distance_error))
        msg.linear.y = max(-limit, min(limit, -float(self.get_parameter("kp_lateral").value) * lateral_error))
        self.cmd_pub.publish(msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ManipulatorClientNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
