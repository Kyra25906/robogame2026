from __future__ import annotations

import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from robogame_core.models import control_safety_result
from robogame_interfaces.msg import CubeDetection, CubeDetectionArray, RobotStatus
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
            "status_stale_s": 0.30,
            "place_heights_m": [0.10, 0.20, 0.30],
        }.items():
            self.declare_parameter(name, default)
        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 20)
        self.result_pub = self.create_publisher(String, "/manipulator/result", 10)
        self.create_subscription(CubeDetectionArray, "/cubes", self._on_cubes, 10)
        self.create_subscription(String, "/manipulator/command", self._on_command, 10)
        self.create_subscription(RobotStatus, "/robot/status", self._on_robot_status, 10)
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
        self.robot_status: RobotStatus | None = None
        self.robot_status_time = 0.0
        self.status_stale = float(self.get_parameter("status_stale_s").value)
        if self.status_stale <= 0.0:
            raise ValueError("status_stale_s must be positive")
        self.create_timer(0.05, self._tick)

    def _status_failure(self):
        status = self.robot_status
        status_fresh = status is not None and (
            time.monotonic() - self.robot_status_time <= self.status_stale
        )
        return control_safety_result(
            status_received=status_fresh,
            communication_ok=bool(status and status.communication_ok),
            emergency_stop=bool(status and status.emergency_stop),
            mechanism_fault=bool(status and status.mechanism_fault),
        )

    def _on_robot_status(self, msg: RobotStatus) -> None:
        self.robot_status = msg
        self.robot_status_time = time.monotonic()
        failure = self._status_failure()
        if self.command is not None and failure is not None:
            details = {
                "SAFETY_STOP": "emergency stop",
                "COMMUNICATION_ERROR": "robot communication unavailable",
                "MECHANISM_ERROR": "robot mechanism fault",
            }
            self._finish(f"{failure.value}: {details[failure.value]}")

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
        failure = self._status_failure()
        if failure is not None:
            details = {
                "SAFETY_STOP": "emergency stop",
                "COMMUNICATION_ERROR": "robot status missing or communication unavailable",
                "MECHANISM_ERROR": "robot mechanism fault",
            }
            self._publish_stop()
            self.result_pub.publish(String(data=f"{failure.value}: {details[failure.value]}"))
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
        status_failure = self._status_failure()
        if status_failure is not None:
            details = {
                "SAFETY_STOP": "emergency stop",
                "COMMUNICATION_ERROR": "robot status stale or communication unavailable",
                "MECHANISM_ERROR": "robot mechanism fault",
            }
            self._finish(f"{status_failure.value}: {details[status_failure.value]}")
            return
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
