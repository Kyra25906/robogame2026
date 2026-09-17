"""mock 机械臂关节状态：离线验证 ARM_SET 链路，不接触任何硬件。

与 `mock_mechanism.py` 同风格：状态是不可变 dataclass，每次操作返回新状态，
失败时**原样返回旧状态**（MANIPULATOR_ACTION_EXTENSION.md §9：模拟失败不得
修改机构状态）。

两条路径的拒绝理由必须一致：真实路径由 `robot_bridge` 用同一张
`ArmJointTable` 做同样的校验，因此 mock 和 real 在“未冻结 / 越界 / 爪子 /
非整度”这几种情况下会返回**相同的错误码**，只是不需要串口。

“一致”不能靠两边各写一遍检查来维持：本模块只调 `validate_arm_target`，
不复制任何判据，所以判据只在 `arm.py` 一处定义。2026-08-19 修正的一处分叉
（mock 曾接受 45.5°，而真机路径一律 9011）就是复制判据的直接后果。
"""

from __future__ import annotations

from dataclasses import dataclass

from .arm import (
    ArmJoint,
    ArmJointNotFrozen,
    ArmJointTable,
    ArmJointTarget,
    ArmTargetRejected,
    arm_rejection_error_code,
    validate_arm_target,
)


MOCK_ARM_SET_FAILED = 1104
"""mock 专用：注入的 ARM_SET 失败（与 mock_mechanism 的 1101-1103 同段）。"""


@dataclass(frozen=True)
class MockArmState:
    """各关节当前角度（度）。未出现过的关节视为“从未收到目标”。"""

    angles_deg: tuple[tuple[ArmJoint, float], ...] = ()

    def angle(self, joint: ArmJoint | int) -> float | None:
        for item, value in self.angles_deg:
            if int(item) == int(joint):
                return value
        return None

    def with_target(self, target: ArmJointTarget) -> MockArmState:
        updated = {item: value for item, value in self.angles_deg}
        updated[target.joint] = target.angle_deg
        return MockArmState(
            tuple(sorted(updated.items(), key=lambda pair: int(pair[0])))
        )


@dataclass(frozen=True)
class MockArmResult:
    state: MockArmState
    success: bool
    error_code: int
    detail: str


def execute_mock_arm_set(
    state: MockArmState,
    target: ArmJointTarget,
    *,
    table: ArmJointTable,
    configured_success: bool = True,
) -> MockArmResult:
    """Apply one ARM_SET in mock mode and report which checks rejected it.

    判据全部来自 `validate_arm_target`（策略 + 值域 + 整度），故意不在这里
    重复实现：只有这样 mock 才会和真机路径对同一个目标给出同一结论。
    """
    try:
        validate_arm_target(target, table=table)
    except (ArmTargetRejected, ArmJointNotFrozen) as exc:
        return MockArmResult(state, False, arm_rejection_error_code(exc), str(exc))

    if not configured_success:
        return MockArmResult(
            state,
            False,
            MOCK_ARM_SET_FAILED,
            f"mock arm set failed ({target.joint.name})",
        )
    return MockArmResult(
        state.with_target(target),
        True,
        0,
        f"mock arm set completed ({target.joint.name}={target.angle_deg:g}°)",
    )
