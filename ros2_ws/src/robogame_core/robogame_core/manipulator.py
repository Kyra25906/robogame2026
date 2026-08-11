from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class AlignmentCommand:
    linear_x: float
    linear_y: float
    ready_to_grab: bool


class ServiceWaitDecision(str, Enum):
    WAIT = "WAIT"
    READY = "READY"
    TIMEOUT = "TIMEOUT"


class ManipulatorState(str, Enum):
    IDLE = "IDLE"
    WAITING_TARGET = "WAITING_TARGET"
    ALIGNING = "ALIGNING"
    WAITING_SERVICE = "WAITING_SERVICE"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RETREATING = "RETREATING"
    WAITING_RETREAT_EVIDENCE = "WAITING_RETREAT_EVIDENCE"
    OBSERVING_STABILITY = "OBSERVING_STABILITY"


class MechanismOperation(str, Enum):
    NONE = "NONE"
    GRAB = "GRAB"
    LIFT = "LIFT"
    RELEASE = "RELEASE"


class GrabVerificationPolicy(str, Enum):
    SERVICE_ONLY = "service_only"
    CUBE_PRESENT = "cube_present"
    GRIPPER_AND_CUBE = "gripper_and_cube"


class PlaceVerificationPolicy(str, Enum):
    SERVICE_ONLY = "service_only"
    CUBE_ABSENT = "cube_absent"
    GRIPPER_OPEN_AND_CUBE_ABSENT = "gripper_open_and_cube_absent"


class VerificationDecision(str, Enum):
    PASS = "PASS"
    WAIT = "WAIT"
    TIMEOUT = "TIMEOUT"


class PlacementEvidence(str, Enum):
    """Meaning of one post-retreat visual observation."""

    QUALIFIED = "QUALIFIED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class StabilityDecision(str, Enum):
    """Progress or terminal result of post-placement observation."""

    OBSERVING = "OBSERVING"
    STABLE = "STABLE"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class PlacementStabilityObserver:
    """Require qualified evidence after retreat, tolerating a configured gap.

    Create the observer only after the mechanism/chassis has reported that
    retreat is complete. Timestamps must use one monotonic time source.
    """

    observation_started_s: float
    stable_duration_s: float = 3.0
    observation_timeout_s: float = 6.0
    max_unavailable_gap_s: float = 0.0
    qualified_since_s: float | None = None
    unavailable_since_s: float | None = None
    last_timestamp_s: float | None = None
    decision: StabilityDecision = StabilityDecision.OBSERVING

    def __post_init__(self) -> None:
        if self.observation_started_s < 0.0:
            raise ValueError("observation_started_s cannot be negative")
        if self.stable_duration_s <= 0.0:
            raise ValueError("stable_duration_s must be positive")
        if self.observation_timeout_s < self.stable_duration_s:
            raise ValueError(
                "observation_timeout_s cannot be shorter than stable_duration_s"
            )
        if self.max_unavailable_gap_s < 0.0:
            raise ValueError("max_unavailable_gap_s cannot be negative")

    def update(
        self, *, timestamp_s: float, evidence: PlacementEvidence
    ) -> StabilityDecision:
        """Consume one observation; terminal decisions remain unchanged."""
        if timestamp_s < self.observation_started_s:
            raise ValueError("timestamp_s cannot precede observation start")
        if self.last_timestamp_s is not None and timestamp_s < self.last_timestamp_s:
            raise ValueError("timestamp_s must be monotonic")
        if self.decision is not StabilityDecision.OBSERVING:
            return self.decision

        self.last_timestamp_s = timestamp_s
        if evidence is PlacementEvidence.FAILED:
            self.decision = StabilityDecision.FAILED
            return self.decision

        if evidence is PlacementEvidence.QUALIFIED:
            if self.qualified_since_s is None:
                self.qualified_since_s = timestamp_s
            elif self.unavailable_since_s is not None:
                unavailable_gap_s = timestamp_s - self.unavailable_since_s
                if unavailable_gap_s > self.max_unavailable_gap_s:
                    self.qualified_since_s = timestamp_s
                else:
                    # Preserve progress, but do not count unseen time as stable.
                    self.qualified_since_s += unavailable_gap_s
            self.unavailable_since_s = None
            if timestamp_s - self.qualified_since_s >= self.stable_duration_s:
                self.decision = StabilityDecision.STABLE
                return self.decision
        else:
            if self.qualified_since_s is not None:
                if self.max_unavailable_gap_s == 0.0:
                    self.qualified_since_s = None
                    self.unavailable_since_s = None
                elif self.unavailable_since_s is None:
                    self.unavailable_since_s = timestamp_s
                elif (
                    timestamp_s - self.unavailable_since_s
                    > self.max_unavailable_gap_s
                ):
                    self.qualified_since_s = None
                    self.unavailable_since_s = None

        if timestamp_s - self.observation_started_s >= self.observation_timeout_s:
            self.decision = StabilityDecision.INCONCLUSIVE
        return self.decision


class CancellationDecision(str, Enum):
    NO_ACTIVE_ACTION = "NO_ACTIVE_ACTION"
    STOP_WORKFLOW = "STOP_WORKFLOW"
    STOP_WORKFLOW_ACTIVE_OPERATION = "STOP_WORKFLOW_ACTIVE_OPERATION"


def cancellation_decision(
    state: ManipulatorState, operation: MechanismOperation
) -> CancellationDecision:
    """Describe what a local cancellation can guarantee at the current stage."""
    if state is ManipulatorState.IDLE:
        return CancellationDecision.NO_ACTIVE_ACTION
    if state is ManipulatorState.EXECUTING and operation is not MechanismOperation.NONE:
        return CancellationDecision.STOP_WORKFLOW_ACTIVE_OPERATION
    return CancellationDecision.STOP_WORKFLOW


def grab_verification_decision(
    *,
    policy: GrabVerificationPolicy,
    cube_present: bool,
    gripper_closed: bool,
    evidence_updated_after_action: bool,
    elapsed_s: float,
    timeout_s: float,
) -> VerificationDecision:
    """Evaluate configurable evidence after the grab service succeeds."""
    if elapsed_s < 0.0:
        raise ValueError("elapsed_s cannot be negative")
    if timeout_s <= 0.0:
        raise ValueError("grab verification timeout must be positive")
    signal_satisfied = {
        GrabVerificationPolicy.SERVICE_ONLY: True,
        GrabVerificationPolicy.CUBE_PRESENT: cube_present,
        GrabVerificationPolicy.GRIPPER_AND_CUBE: cube_present and gripper_closed,
    }[policy]
    evidence_satisfied = (
        signal_satisfied
        and (
            policy is GrabVerificationPolicy.SERVICE_ONLY
            or evidence_updated_after_action
        )
    )
    if evidence_satisfied:
        return VerificationDecision.PASS
    if elapsed_s >= timeout_s:
        return VerificationDecision.TIMEOUT
    return VerificationDecision.WAIT


def place_verification_decision(
    *,
    policy: PlaceVerificationPolicy,
    cube_present: bool,
    gripper_closed: bool,
    evidence_updated_after_action: bool,
    elapsed_s: float,
    timeout_s: float,
) -> VerificationDecision:
    """Evaluate configurable evidence after the release service succeeds."""
    if elapsed_s < 0.0:
        raise ValueError("elapsed_s cannot be negative")
    if timeout_s <= 0.0:
        raise ValueError("place verification timeout must be positive")
    signal_satisfied = {
        PlaceVerificationPolicy.SERVICE_ONLY: True,
        PlaceVerificationPolicy.CUBE_ABSENT: not cube_present,
        PlaceVerificationPolicy.GRIPPER_OPEN_AND_CUBE_ABSENT: (
            not gripper_closed and not cube_present
        ),
    }[policy]
    evidence_satisfied = (
        signal_satisfied
        and (
            policy is PlaceVerificationPolicy.SERVICE_ONLY
            or evidence_updated_after_action
        )
    )
    if evidence_satisfied:
        return VerificationDecision.PASS
    if elapsed_s >= timeout_s:
        return VerificationDecision.TIMEOUT
    return VerificationDecision.WAIT


def service_wait_decision(
    *, ready: bool, elapsed_s: float, timeout_s: float
) -> ServiceWaitDecision:
    """Decide whether a mechanism service can be called or has timed out."""
    if elapsed_s < 0.0:
        raise ValueError("elapsed_s cannot be negative")
    if timeout_s <= 0.0:
        raise ValueError("service wait timeout must be positive")
    if ready:
        return ServiceWaitDecision.READY
    if elapsed_s >= timeout_s:
        return ServiceWaitDecision.TIMEOUT
    return ServiceWaitDecision.WAIT


def format_mechanism_failure(
    *, operation: MechanismOperation, success: bool, error_code: int, detail: str
) -> str | None:
    """Preserve a mechanism service's failure evidence for logs and callers."""
    if success:
        return None
    operation_name = (
        operation.value.lower()
        if operation is not MechanismOperation.NONE
        else "mechanism"
    )
    message = f"MECHANISM_ERROR: {operation_name} failed (code={int(error_code)})"
    cleaned_detail = detail.strip()
    if cleaned_detail:
        message += f": {cleaned_detail}"
    return message


def format_service_exception(
    operation: MechanismOperation, error: BaseException
) -> str:
    """Turn an asynchronous service exception into a stable action result."""
    operation_name = (
        operation.value.lower()
        if operation is not MechanismOperation.NONE
        else "mechanism"
    )
    detail = str(error).strip() or error.__class__.__name__
    return f"MECHANISM_ERROR: {operation_name} service exception: {detail}"


def select_place_height(placed_layers: int, heights_m: list[float]) -> float:
    """Select the next lift height, retaining the highest configured layer."""
    if placed_layers < 0:
        raise ValueError("placed_layers cannot be negative")
    if not heights_m:
        raise ValueError("at least one place height is required")
    if any(height <= 0.0 for height in heights_m):
        raise ValueError("place heights must be positive")
    return float(heights_m[min(placed_layers, len(heights_m) - 1)])


def calculate_alignment_command(
    *,
    distance_m: float,
    lateral_m: float,
    target_distance_m: float,
    distance_tolerance_m: float,
    lateral_tolerance_m: float,
    kp_distance: float,
    kp_lateral: float,
    max_speed: float,
) -> AlignmentCommand:
    """Calculate the low-speed chassis command used before a grab."""
    if distance_tolerance_m < 0.0 or lateral_tolerance_m < 0.0:
        raise ValueError("alignment tolerances cannot be negative")
    if max_speed <= 0.0:
        raise ValueError("max_speed must be positive")

    distance_error = distance_m - target_distance_m
    ready = (
        abs(distance_error) <= distance_tolerance_m
        and abs(lateral_m) <= lateral_tolerance_m
    )
    if ready:
        return AlignmentCommand(0.0, 0.0, True)

    linear_x = max(-max_speed, min(max_speed, kp_distance * distance_error))
    linear_y = max(-max_speed, min(max_speed, -kp_lateral * lateral_m))
    return AlignmentCommand(linear_x, linear_y, False)
