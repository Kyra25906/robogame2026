from __future__ import annotations

import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from robogame_core.manipulator import (
    CancellationDecision,
    GrabVerificationPolicy,
    ManipulatorState,
    MechanismOperation,
    PlacementEvidence,
    PlacementStabilityObserver,
    PlaceVerificationPolicy,
    ServiceWaitDecision,
    StabilityDecision,
    VerificationDecision,
    cancellation_decision,
    calculate_alignment_command,
    format_mechanism_failure,
    format_service_exception,
    select_place_height,
    grab_verification_decision,
    place_verification_decision,
    service_wait_decision,
)
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
            "status_stale_s": 0.30, "service_wait_timeout_s": 1.0,
            "grab_verification_policy": "service_only",
            "grab_verification_timeout_s": 1.0,
            "place_verification_policy": "service_only",
            "place_verification_timeout_s": 1.0,
            "placement_evidence_policy": "unavailable",
            "placement_stable_duration_s": 3.0,
            "placement_observation_timeout_s": 6.0,
            "placement_max_unavailable_gap_s": 0.0,
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
        self.retreat = self.create_client(ExecuteMechanism, "/chassis/retreat")
        self.lift = self.create_client(SetLiftHeight, "/lift/set_height")
        self.command: str | None = None
        self.target: CubeDetection | None = None
        self.target_time = 0.0
        self.started_at = 0.0
        self.service_future = None
        self.retreat_future = None
        self.stability_observer: PlacementStabilityObserver | None = None
        self.workflow_state = ManipulatorState.IDLE
        self.operation = MechanismOperation.NONE
        self.service_wait_started_at = 0.0
        self.verification_started_at = 0.0
        self.retreat_started_at = 0.0
        try:
            self.grab_verification_policy = GrabVerificationPolicy(
                str(self.get_parameter("grab_verification_policy").value)
            )
        except ValueError as exc:
            allowed = ", ".join(item.value for item in GrabVerificationPolicy)
            raise ValueError(
                f"grab_verification_policy must be one of: {allowed}"
            ) from exc
        try:
            self.place_verification_policy = PlaceVerificationPolicy(
                str(self.get_parameter("place_verification_policy").value)
            )
        except ValueError as exc:
            allowed = ", ".join(item.value for item in PlaceVerificationPolicy)
            raise ValueError(
                f"place_verification_policy must be one of: {allowed}"
            ) from exc
        self.placed_layers = 0
        self.placement_evidence_policy = str(
            self.get_parameter("placement_evidence_policy").value
        )
        if self.placement_evidence_policy not in {
            "mock_qualified", "mock_failed", "unavailable"
        }:
            raise ValueError(
                "placement_evidence_policy must be mock_qualified, "
                "mock_failed, or unavailable"
            )
        # Construct once to validate the two configurable durations at startup.
        PlacementStabilityObserver(
            observation_started_s=0.0,
            stable_duration_s=float(
                self.get_parameter("placement_stable_duration_s").value
            ),
            observation_timeout_s=float(
                self.get_parameter("placement_observation_timeout_s").value
            ),
            max_unavailable_gap_s=float(
                self.get_parameter("placement_max_unavailable_gap_s").value
            ),
        )
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
        if msg.data == "CANCEL":
            self._cancel_current_action()
            return
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
        self.retreat_future = None
        self.stability_observer = None
        self.workflow_state = (
            ManipulatorState.WAITING_TARGET
            if self.command.startswith("PICK_")
            else ManipulatorState.WAITING_SERVICE
        )
        self.operation = (
            MechanismOperation.NONE
            if self.command.startswith("PICK_")
            else MechanismOperation.LIFT
        )
        self.service_wait_started_at = self.started_at
        self.verification_started_at = 0.0
        self.retreat_started_at = 0.0

    def _cancel_current_action(self) -> None:
        if self.workflow_state is ManipulatorState.RETREATING:
            self._finish(
                "CANCELLED: software workflow stopped; "
                "active retreat service may still complete"
            )
            return
        decision = cancellation_decision(self.workflow_state, self.operation)
        if decision is CancellationDecision.NO_ACTIVE_ACTION:
            self._publish_stop()
            self.result_pub.publish(String(data="CANCELLED: no active manipulator action"))
            return
        operation = self.operation.value.lower()
        if decision is CancellationDecision.STOP_WORKFLOW_ACTIVE_OPERATION:
            result = (
                "CANCELLED: software workflow stopped; "
                f"active {operation} service may still complete"
            )
        else:
            result = "CANCELLED: software workflow stopped"
        self._finish(result)

    def _publish_stop(self) -> None:
        self.cmd_pub.publish(Twist())

    def _finish(self, result: str) -> None:
        self._publish_stop()
        self.result_pub.publish(String(data=result))
        self.command = None
        self.target = None
        self.service_future = None
        self.retreat_future = None
        self.stability_observer = None
        self.workflow_state = ManipulatorState.IDLE
        self.operation = MechanismOperation.NONE
        self.service_wait_started_at = 0.0
        self.verification_started_at = 0.0
        self.retreat_started_at = 0.0

    def _verify_grab(self, now: float) -> None:
        status = self.robot_status
        decision = grab_verification_decision(
            policy=self.grab_verification_policy,
            cube_present=bool(status and status.cube_present),
            gripper_closed=bool(status and status.gripper_closed),
            evidence_updated_after_action=(
                self.robot_status_time >= self.verification_started_at
            ),
            elapsed_s=now - self.verification_started_at,
            timeout_s=float(self.get_parameter("grab_verification_timeout_s").value),
        )
        if decision is VerificationDecision.PASS:
            self._finish("SUCCESS")
        elif decision is VerificationDecision.TIMEOUT:
            self._finish(
                "MECHANISM_ERROR: grab verification timed out "
                f"(policy={self.grab_verification_policy.value})"
            )

    def _verify_place(self, now: float) -> None:
        status = self.robot_status
        decision = place_verification_decision(
            policy=self.place_verification_policy,
            cube_present=bool(status and status.cube_present),
            gripper_closed=bool(status and status.gripper_closed),
            evidence_updated_after_action=(
                self.robot_status_time >= self.verification_started_at
            ),
            elapsed_s=now - self.verification_started_at,
            timeout_s=float(self.get_parameter("place_verification_timeout_s").value),
        )
        if decision is VerificationDecision.PASS:
            self.get_logger().info(
                "place verification passed; requesting clearance retreat"
            )
            self.workflow_state = ManipulatorState.WAITING_SERVICE
            self.operation = MechanismOperation.NONE
            self.service_wait_started_at = now
            self._start_retreat(now)
        elif decision is VerificationDecision.TIMEOUT:
            self._finish(
                "MECHANISM_ERROR: place verification timed out "
                f"(policy={self.place_verification_policy.value})"
            )

    def _wait_for_service(self, client, name: str, now: float) -> bool:
        decision = service_wait_decision(
            ready=client.service_is_ready(),
            elapsed_s=now - self.service_wait_started_at,
            timeout_s=float(self.get_parameter("service_wait_timeout_s").value),
        )
        if decision is ServiceWaitDecision.TIMEOUT:
            self._finish(f"MECHANISM_ERROR: {name} service unavailable")
            return False
        return decision is ServiceWaitDecision.READY

    def _start_release(self, now: float) -> None:
        request = ExecuteMechanism.Request()
        request.command, request.timeout_s = "RELEASE", 3.0
        if self._wait_for_service(self.release, "release", now):
            self.workflow_state = ManipulatorState.EXECUTING
            self.operation = MechanismOperation.RELEASE
            self.service_future = self.release.call_async(request)

    def _start_retreat(self, now: float) -> None:
        request = ExecuteMechanism.Request()
        request.command, request.timeout_s = "RETREAT", 3.0
        if self._wait_for_service(self.retreat, "retreat", now):
            self.workflow_state = ManipulatorState.RETREATING
            self.operation = MechanismOperation.NONE
            self.retreat_started_at = now
            self.retreat_future = self.retreat.call_async(request)

    def _start_stability_observation(self, now: float) -> None:
        self.stability_observer = PlacementStabilityObserver(
            observation_started_s=now,
            stable_duration_s=float(
                self.get_parameter("placement_stable_duration_s").value
            ),
            observation_timeout_s=float(
                self.get_parameter("placement_observation_timeout_s").value
            ),
            max_unavailable_gap_s=float(
                self.get_parameter("placement_max_unavailable_gap_s").value
            ),
        )
        self.workflow_state = ManipulatorState.OBSERVING_STABILITY
        self.get_logger().info(
            "retreat evidence confirmed; starting placement stability observation"
        )

    def _observe_stability(self, now: float) -> None:
        if self.stability_observer is None:
            self._finish("INCONCLUSIVE: stability observer was not initialized")
            return
        evidence = {
            "mock_qualified": PlacementEvidence.QUALIFIED,
            "mock_failed": PlacementEvidence.FAILED,
            "unavailable": PlacementEvidence.UNAVAILABLE,
        }[self.placement_evidence_policy]
        decision = self.stability_observer.update(
            timestamp_s=now, evidence=evidence
        )
        if decision is StabilityDecision.STABLE:
            self.get_logger().info("placement remained stable for the required window")
            self.placed_layers += 1
            self._finish("STABLE")
        elif decision is StabilityDecision.FAILED:
            self._finish("FAILED: placement became unstable")
        elif decision is StabilityDecision.INCONCLUSIVE:
            self._finish("INCONCLUSIVE: stable placement could not be confirmed")

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
        if self.retreat_future is not None:
            if self.retreat_future.done():
                try:
                    response = self.retreat_future.result()
                except Exception as exc:
                    self._finish(
                        f"MECHANISM_ERROR: retreat service exception: {exc}"
                    )
                    return
                if response is None or not response.success:
                    detail = "no response" if response is None else response.detail
                    self._finish(f"MECHANISM_ERROR: retreat failed: {detail}")
                    return
                self.retreat_future = None
                self.workflow_state = ManipulatorState.WAITING_RETREAT_EVIDENCE
                self.get_logger().info(
                    "retreat service completed; waiting for fresh retreat status"
                )
            return
        if self.service_future is not None:
            if self.service_future.done():
                try:
                    response = self.service_future.result()
                except Exception as exc:  # ROS futures surface transport/server failures here.
                    self._finish(format_service_exception(self.operation, exc))
                    return
                failure = (
                    "MECHANISM_ERROR: service returned no response"
                    if response is None
                    else format_mechanism_failure(
                        operation=self.operation,
                        success=response.success,
                        error_code=response.error_code,
                        detail=response.detail,
                    )
                )
                if failure is not None:
                    self._finish(failure)
                elif self.operation is MechanismOperation.GRAB:
                    self.service_future = None
                    self.workflow_state = ManipulatorState.VERIFYING
                    self.verification_started_at = now
                    self._verify_grab(now)
                elif self.operation is MechanismOperation.LIFT:
                    self.service_future = None
                    self.workflow_state = ManipulatorState.WAITING_SERVICE
                    self.operation = MechanismOperation.RELEASE
                    self.service_wait_started_at = now
                    self._start_release(now)
                elif self.operation is MechanismOperation.RELEASE:
                    self.service_future = None
                    self.workflow_state = ManipulatorState.VERIFYING
                    self.verification_started_at = now
                    self._verify_place(now)
                else:
                    self._finish("SUCCESS")
            return
        if (self.workflow_state is ManipulatorState.VERIFYING
                and self.operation is MechanismOperation.GRAB):
            self._verify_grab(now)
            return
        if (self.workflow_state is ManipulatorState.VERIFYING
                and self.operation is MechanismOperation.RELEASE):
            self._verify_place(now)
            return
        if self.workflow_state is ManipulatorState.WAITING_RETREAT_EVIDENCE:
            if (
                self.robot_status is not None
                and self.robot_status.retreat_complete
                and self.robot_status_time >= self.retreat_started_at
            ):
                self._start_stability_observation(now)
            return
        if self.workflow_state is ManipulatorState.OBSERVING_STABILITY:
            self._observe_stability(now)
            return
        if self.command.startswith("PLACE_"):
            if (self.workflow_state is ManipulatorState.WAITING_SERVICE
                    and self.operation is MechanismOperation.NONE):
                self._start_retreat(now)
                return
            if (self.workflow_state is ManipulatorState.WAITING_SERVICE
                    and self.operation is MechanismOperation.RELEASE):
                self._start_release(now)
                return
            heights = list(self.get_parameter("place_heights_m").value)
            try:
                height = select_place_height(self.placed_layers, heights)
            except ValueError as exc:
                self._finish(f"MECHANISM_ERROR: {exc}")
                return
            request = SetLiftHeight.Request()
            request.height_m, request.timeout_s = height, 3.0
            if self.operation is not MechanismOperation.LIFT:
                self.operation = MechanismOperation.LIFT
                self.workflow_state = ManipulatorState.WAITING_SERVICE
                self.service_wait_started_at = now
            if self._wait_for_service(self.lift, "lift", now):
                self.workflow_state = ManipulatorState.EXECUTING
                self.operation = MechanismOperation.LIFT
                self.service_future = self.lift.call_async(request)
            return
        if self.target is None or now - self.target_time > float(self.get_parameter("target_stale_s").value):
            self.workflow_state = ManipulatorState.WAITING_TARGET
            self.operation = MechanismOperation.NONE
            self._publish_stop()
            return
        self.workflow_state = ManipulatorState.ALIGNING
        alignment = calculate_alignment_command(
            distance_m=self.target.distance_m,
            lateral_m=self.target.lateral_m,
            target_distance_m=float(self.get_parameter("target_distance_m").value),
            distance_tolerance_m=float(self.get_parameter("distance_tolerance_m").value),
            lateral_tolerance_m=float(self.get_parameter("lateral_tolerance_m").value),
            kp_distance=float(self.get_parameter("kp_distance").value),
            kp_lateral=float(self.get_parameter("kp_lateral").value),
            max_speed=float(self.get_parameter("max_speed").value),
        )
        if alignment.ready_to_grab:
            self._publish_stop()
            request = ExecuteMechanism.Request()
            request.command, request.timeout_s = "GRAB", 3.0
            if self.operation is not MechanismOperation.GRAB:
                self.workflow_state = ManipulatorState.WAITING_SERVICE
                self.operation = MechanismOperation.GRAB
                self.service_wait_started_at = now
            if self._wait_for_service(self.grab, "grab", now):
                self.workflow_state = ManipulatorState.EXECUTING
                self.operation = MechanismOperation.GRAB
                self.service_future = self.grab.call_async(request)
            return
        if (self.workflow_state is ManipulatorState.WAITING_SERVICE
                and self.operation is MechanismOperation.GRAB):
            self.workflow_state = ManipulatorState.ALIGNING
            self.operation = MechanismOperation.NONE
            self.service_wait_started_at = 0.0
        msg = Twist()
        msg.linear.x = alignment.linear_x
        msg.linear.y = alignment.linear_y
        self.cmd_pub.publish(msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ManipulatorClientNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
