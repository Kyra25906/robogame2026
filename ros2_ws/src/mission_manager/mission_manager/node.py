from __future__ import annotations

import time

import rclpy
from geometry_msgs.msg import Pose2D, Twist
from rclpy.node import Node
from robogame_core.mission import MissionConfig, MissionMachine, MissionState
from robogame_core.models import MissionResult
from robogame_interfaces.msg import CargoState, MissionState as MissionStateMsg, RobotStatus
from std_msgs.msg import String


class MissionManagerNode(Node):
    def __init__(self) -> None:
        super().__init__("mission_manager")
        defaults = {
            "orange_target": 1, "purple_target": 0, "max_retries": 2,
            "state_timeout_s": 20.0, "build_stability_s": 3.0,
            "orange_waypoint": [1.0, 0.5, 0.0], "purple_waypoint": [1.5, 1.0, 0.0],
            "build_waypoint": [0.5, 1.5, 1.57], "retreat_waypoint": [0.5, 1.2, 1.57],
        }
        for name, value in defaults.items():
            self.declare_parameter(name, value)
        self.machine = MissionMachine(MissionConfig(
            orange_target=int(self.get_parameter("orange_target").value),
            purple_target=int(self.get_parameter("purple_target").value),
            max_retries=int(self.get_parameter("max_retries").value),
            state_timeout_s=float(self.get_parameter("state_timeout_s").value),
            build_stability_s=float(self.get_parameter("build_stability_s").value),
        ))
        self.status: RobotStatus | None = None
        self.last_dispatched: MissionState | None = None
        self.state_pub = self.create_publisher(MissionStateMsg, "/mission/state", 10)
        self.cargo_pub = self.create_publisher(CargoState, "/mission/cargo", 10)
        self.goal_pub = self.create_publisher(Pose2D, "/motion/goal", 10)
        self.manip_pub = self.create_publisher(String, "/manipulator/command", 10)
        self.stop_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.create_subscription(RobotStatus, "/robot/status", self._on_status, 10)
        self.create_subscription(String, "/motion/result", self._on_action_result, 10)
        self.create_subscription(String, "/manipulator/result", self._on_action_result, 10)
        self.create_timer(0.05, self._tick)

    def _on_status(self, msg: RobotStatus) -> None:
        self.status = msg

    def _on_action_result(self, msg: String) -> None:
        if msg.data == "SUCCESS":
            self.machine.tick(action_succeeded=True)
        else:
            prefix = msg.data.split(":", 1)[0]
            result = MissionResult.__members__.get(prefix, MissionResult.MECHANISM_ERROR)
            self.machine.tick(action_failed=True, failure_result=result, failure_detail=msg.data)
        self.last_dispatched = None

    def _waypoint(self, name: str) -> Pose2D:
        values = list(self.get_parameter(name).value)
        return Pose2D(x=values[0], y=values[1], theta=values[2])

    def _dispatch(self) -> None:
        state = self.machine.state
        if state == self.last_dispatched:
            return
        if state is MissionState.GO_TO_ORANGE:
            self.goal_pub.publish(self._waypoint("orange_waypoint"))
        elif state is MissionState.GO_TO_PURPLE:
            self.goal_pub.publish(self._waypoint("purple_waypoint"))
        elif state is MissionState.GO_TO_BUILD:
            self.goal_pub.publish(self._waypoint("build_waypoint"))
        elif state is MissionState.RETREAT:
            self.goal_pub.publish(self._waypoint("retreat_waypoint"))
        elif state in {MissionState.PICK_ORANGE, MissionState.PICK_PURPLE,
                       MissionState.PLACE_ORANGE, MissionState.PLACE_PURPLE}:
            self.manip_pub.publish(String(data=state.value))
        elif state in {MissionState.SAFE_STOP, MissionState.FAILED, MissionState.COMPLETE}:
            self.stop_pub.publish(Twist())
        self.last_dispatched = state

    def _tick(self) -> None:
        now = time.monotonic()
        if self.status is None:
            return
        if self.machine.state is MissionState.SELF_CHECK:
            self.machine.tick(now=now, communication_ok=self.status.communication_ok,
                              emergency_stop=self.status.emergency_stop,
                              action_succeeded=not self.status.mechanism_fault)
        elif self.machine.state is MissionState.WAIT_FOR_PHYSICAL_START:
            self.machine.tick(now=now, communication_ok=self.status.communication_ok,
                              emergency_stop=self.status.emergency_stop,
                              physical_start=self.status.physical_start)
        elif self.machine.state is MissionState.VERIFY_BUILD:
            stable_for = now - self.machine.entered_at
            self.machine.tick(now=now, communication_ok=self.status.communication_ok,
                              emergency_stop=self.status.emergency_stop,
                              action_succeeded=stable_for >= self.machine.config.build_stability_s)
        else:
            self.machine.tick(now=now, communication_ok=self.status.communication_ok,
                              emergency_stop=self.status.emergency_stop)
        self._dispatch()
        stamp = self.get_clock().now().to_msg()
        state = MissionStateMsg(stamp=stamp, state=self.machine.state.value,
                                result=self.machine.result.value, retry_count=self.machine.retries,
                                detail=self.machine.detail)
        self.state_pub.publish(state)
        cargo = CargoState(stamp=stamp, orange_onboard=self.machine.cargo.orange,
                           purple_onboard=self.machine.cargo.purple,
                           total_onboard=self.machine.cargo.total, valid=self.machine.cargo.valid)
        self.cargo_pub.publish(cargo)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MissionManagerNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
