from __future__ import annotations

from dataclasses import dataclass

from .manipulator import MechanismOperation


@dataclass(frozen=True)
class MockMechanismState:
    cube_present: bool = False
    gripper_closed: bool = False
    lift_height_m: float = 0.0


@dataclass(frozen=True)
class MockMechanismResult:
    state: MockMechanismState
    success: bool
    error_code: int
    detail: str


def execute_mock_mechanism(
    state: MockMechanismState,
    operation: MechanismOperation,
    *,
    configured_success: bool = True,
    height_m: float | None = None,
) -> MockMechanismResult:
    """Apply a deterministic mock mechanism operation for offline tests."""
    if operation is MechanismOperation.GRAB:
        if not configured_success:
            return MockMechanismResult(state, False, 1101, "mock grab failed")
        updated = MockMechanismState(True, True, state.lift_height_m)
        return MockMechanismResult(updated, True, 0, "mock grab completed")

    if operation is MechanismOperation.RELEASE:
        if not configured_success:
            return MockMechanismResult(state, False, 1102, "mock release failed")
        updated = MockMechanismState(False, False, state.lift_height_m)
        return MockMechanismResult(updated, True, 0, "mock release completed")

    if operation is MechanismOperation.LIFT:
        if height_m is None:
            raise ValueError("height_m is required for a mock lift")
        if not 0.0 <= height_m <= 0.8:
            return MockMechanismResult(state, False, 1002, "height outside [0, 0.8] m")
        if not configured_success:
            return MockMechanismResult(state, False, 1103, "mock lift failed")
        updated = MockMechanismState(
            state.cube_present, state.gripper_closed, float(height_m)
        )
        return MockMechanismResult(updated, True, 0, "mock lift completed")

    raise ValueError(f"unsupported mock mechanism operation: {operation.value}")
