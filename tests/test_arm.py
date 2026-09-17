"""机械臂关节模型与 mock ARM_SET 的纯逻辑测试（不接触 ROS / 硬件）。"""
import pathlib
import re
import unittest

from robogame_core.arm import (
    ARM_ERROR_GRIPPER_POLICY,
    ARM_ERROR_NOT_FROZEN,
    ARM_ERROR_TARGET_REJECTED,
    ARM_JOINT_ANGLE_RANGE_DEG,
    ARM_JOINT_PULSE_LIMITS_US,
    ARM_SAFE_REFERENCE_PULSE_US,
    ARM_SET_JOINTS,
    ARM_SET_PARAMETER_SCALE,
    ArmGripperPolicyRejected,
    ArmJoint,
    ArmJointNotFrozen,
    ArmJointSpec,
    ArmJointTable,
    ArmJointTarget,
    ArmPose,
    ArmTargetRejected,
    UNFROZEN_ARM_JOINT_TABLE,
    arm_angle_for_pulse_us,
    arm_joint_table_with_frozen_ranges,
    arm_pulse_us_for_angle,
    arm_rejection_error_code,
    arm_zero_displacement_targets,
    decode_arm_set_parameter,
    default_arm_joint_table,
    encode_arm_pose_parameters,
    encode_arm_set_parameter,
    format_arm_pose,
    iter_arm_joint_targets,
    parse_arm_joint_ranges,
    validate_arm_pose,
    validate_arm_target,
)
from robogame_core.mock_arm import (
    MOCK_ARM_SET_FAILED,
    MockArmState,
    execute_mock_arm_set,
)


def frozen_table(**ranges):
    """0=云盘, 1=肩, 2=肘, 3=腕 —— 测试用的“已冻结”表（按关节名传参）。"""
    return arm_joint_table_with_frozen_ranges(
        {ArmJoint[name]: value for name, value in ranges.items()},
        evidence="unit test freeze",
    )


ALL_FROZEN = frozen_table(
    BASE=(0.0, 90.0),
    SHOULDER=(0.0, 270.0),
    ELBOW=(0.0, 270.0),
    WRIST=(0.0, 270.0),
)


class ArmJointIdentityTests(unittest.TestCase):
    def test_joint_ids_reuse_the_firmware_arm_h_enum(self):
        # 编号必须逐项等于 arm.h::Arm_Joint，两侧才能零映射。
        self.assertEqual(
            {item.name: int(item) for item in ArmJoint},
            {
                "BASE": 0,
                "SHOULDER": 1,
                "ELBOW": 2,
                "WRIST": 3,
                "GRIPPER": 4,
            },
        )

    def test_gripper_is_not_an_arm_set_joint(self):
        # C-2 / C-9：爪子走 GRAB / RELEASE。
        self.assertNotIn(ArmJoint.GRIPPER, ARM_SET_JOINTS)
        self.assertEqual(len(ARM_SET_JOINTS), 4)

    def test_default_table_freezes_nothing(self):
        table = default_arm_joint_table()
        self.assertEqual(table.unfrozen_joints(), tuple(ArmJoint))
        self.assertEqual(table.frozen_joints(), ())
        self.assertFalse(table.fully_frozen)

    def test_default_table_records_design_ranges_from_firmware(self):
        table = default_arm_joint_table()
        for joint in (ArmJoint.BASE, ArmJoint.SHOULDER, ArmJoint.ELBOW, ArmJoint.WRIST):
            self.assertEqual(
                table.spec(joint).design_range_deg,
                (0.0, ARM_JOINT_ANGLE_RANGE_DEG[joint]),
            )
        # 爪不是角度关节，不给"总行程"以免暗示可以按角度下发。
        self.assertIsNone(table.spec(ArmJoint.GRIPPER).design_range_deg)

    def test_default_evidence_names_a_source_for_every_joint(self):
        table = default_arm_joint_table()
        for joint in ArmJoint:
            evidence = table.spec(joint).evidence
            self.assertTrue(evidence.strip(), joint.name)
            self.assertTrue(
                ("C-" in evidence) or ("arm.c" in evidence) or ("arm.h" in evidence),
                f"{joint.name} evidence has no traceable source: {evidence}",
            )

    def test_describe_reports_unfrozen_state(self):
        described = UNFROZEN_ARM_JOINT_TABLE.describe()
        for joint in ArmJoint:
            self.assertIn(f"{joint.name}=UNFROZEN", described)


class ArmTargetPolicyTests(unittest.TestCase):
    def test_unfrozen_joint_is_refused_with_a_reason(self):
        target = ArmJointTarget(ArmJoint.BASE, 45.0)
        with self.assertRaises(ArmJointNotFrozen) as caught:
            validate_arm_target(target, table=UNFROZEN_ARM_JOINT_TABLE)
        self.assertIn("C-10", str(caught.exception))
        with self.assertRaises(ArmJointNotFrozen):
            encode_arm_set_parameter(target, table=UNFROZEN_ARM_JOINT_TABLE)

    def test_partial_freeze_leaves_the_others_refusing(self):
        table = frozen_table(BASE=(0.0, 90.0))
        validate_arm_target(ArmJointTarget(ArmJoint.BASE, 30.0), table=table)
        with self.assertRaises(ArmJointNotFrozen):
            validate_arm_target(ArmJointTarget(ArmJoint.SHOULDER, 30.0), table=table)

    def test_gripper_policy_beats_even_a_frozen_range(self):
        # 即使有人把爪子也冻结了，策略仍然拒绝：爪子不该走 ARM_SET。
        table = frozen_table(GRIPPER=(0.0, 270.0))
        target = ArmJointTarget(ArmJoint.GRIPPER, 90.0)
        with self.assertRaises(ArmGripperPolicyRejected) as caught:
            validate_arm_target(target, table=table)
        self.assertIn("GRAB", str(caught.exception))
        with self.assertRaises(ArmGripperPolicyRejected):
            encode_arm_set_parameter(target, table=table)

    def test_out_of_range_angle_is_rejected_at_both_ends(self):
        for angle in (-1.0, 90.1, 270.0):
            with self.subTest(angle=angle), self.assertRaises(ArmTargetRejected):
                validate_arm_target(
                    ArmJointTarget(ArmJoint.BASE, angle), table=ALL_FROZEN
                )

    def test_range_endpoints_are_accepted(self):
        for angle in (0.0, 90.0):
            validate_arm_target(
                ArmJointTarget(ArmJoint.BASE, angle), table=ALL_FROZEN
            )

    def test_rejection_reason_maps_to_a_stable_error_code(self):
        table = ALL_FROZEN
        cases = (
            (ArmJoint.GRIPPER, 10.0, ARM_ERROR_GRIPPER_POLICY),
            (ArmJoint.BASE, 200.0, ARM_ERROR_TARGET_REJECTED),
        )
        for joint, angle, expected in cases:
            with self.subTest(joint=joint.name):
                with self.assertRaises((ArmTargetRejected, ArmJointNotFrozen)) as caught:
                    validate_arm_target(ArmJointTarget(joint, angle), table=table)
                self.assertEqual(arm_rejection_error_code(caught.exception), expected)
        with self.assertRaises(ArmJointNotFrozen) as caught:
            validate_arm_target(
                ArmJointTarget(ArmJoint.BASE, 10.0), table=UNFROZEN_ARM_JOINT_TABLE
            )
        self.assertEqual(arm_rejection_error_code(caught.exception), ARM_ERROR_NOT_FROZEN)


class ArmParameterCodingTests(unittest.TestCase):
    def test_parameter_is_joint_id_times_1000_plus_angle(self):
        cases = {
            (ArmJoint.BASE, 90.0): 90,
            (ArmJoint.BASE, 0.0): 0,
            (ArmJoint.SHOULDER, 45.0): 1045,
            (ArmJoint.ELBOW, 7.0): 2007,
            (ArmJoint.WRIST, 270.0): 3270,
        }
        for (joint, angle), expected in cases.items():
            with self.subTest(joint=joint.name, angle=angle):
                self.assertEqual(
                    encode_arm_set_parameter(
                        ArmJointTarget(joint, angle), table=ALL_FROZEN
                    ),
                    expected,
                )
                self.assertEqual(expected // ARM_SET_PARAMETER_SCALE, int(joint))

    def test_round_trip_through_the_wire_parameter(self):
        for joint in ARM_SET_JOINTS:
            for angle in (0.0, 1.0, 45.0, 90.0):
                with self.subTest(joint=joint.name, angle=angle):
                    target = ArmJointTarget(joint, angle)
                    parameter = encode_arm_set_parameter(target, table=ALL_FROZEN)
                    self.assertEqual(
                        decode_arm_set_parameter(parameter, table=ALL_FROZEN), target
                    )

    def test_non_integral_angle_is_rejected_instead_of_silently_rounded(self):
        # 进位编码没有小数位：45.5 只能被写成 45 或 46，两者都是错的姿态。
        with self.assertRaisesRegex(ArmTargetRejected, "whole degree"):
            encode_arm_set_parameter(
                ArmJointTarget(ArmJoint.BASE, 45.5), table=ALL_FROZEN
            )

    def test_non_finite_and_non_numeric_angles_are_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf"), "90", None, True):
            with self.subTest(angle=bad), self.assertRaises(ArmTargetRejected):
                ArmJointTarget(ArmJoint.BASE, bad)

    def test_unknown_joint_ids_are_rejected(self):
        for bad in (5, -1, 9, "0", None, True):
            with self.subTest(joint=bad), self.assertRaises(ArmTargetRejected):
                ArmJointTarget(bad, 10.0)

    def test_decode_is_structural_by_default_and_policy_with_a_table(self):
        # 结构可读但策略上不可用：肩 900° 结构合法，值域外。
        structural = decode_arm_set_parameter(1900)
        self.assertEqual(structural, ArmJointTarget(ArmJoint.SHOULDER, 900.0))
        with self.assertRaises(ArmTargetRejected):
            decode_arm_set_parameter(1900, table=ALL_FROZEN)

    def test_decode_rejects_unknown_joint_and_negative_parameter(self):
        with self.assertRaisesRegex(ArmTargetRejected, "unknown ARM_SET joint"):
            decode_arm_set_parameter(5000)
        with self.assertRaisesRegex(ArmTargetRejected, "negative|为负"):
            decode_arm_set_parameter(-1)
        for bad in ("90", 90.0, None, True):
            with self.subTest(parameter=bad), self.assertRaises(ArmTargetRejected):
                decode_arm_set_parameter(bad)


class ArmTableConstructionTests(unittest.TestCase):
    def test_table_must_define_every_joint(self):
        specs = dict(default_arm_joint_table().specs)
        del specs[ArmJoint.GRIPPER]
        with self.assertRaisesRegex(ArmTargetRejected, "missing"):
            ArmJointTable(specs)

    def test_table_key_must_match_the_spec_joint(self):
        specs = dict(default_arm_joint_table().specs)
        specs[ArmJoint.BASE] = specs[ArmJoint.SHOULDER]
        with self.assertRaisesRegex(ArmTargetRejected, "does not match"):
            ArmJointTable(specs)

    def test_frozen_range_above_the_encoder_limit_is_rejected(self):
        with self.assertRaisesRegex(ArmTargetRejected, "999"):
            frozen_table(BASE=(0.0, ARM_SET_PARAMETER_SCALE))

    def test_reversed_or_degenerate_ranges_are_rejected(self):
        for bad in ((90.0, 0.0), (5.0, 5.0)):
            with self.subTest(range=bad), self.assertRaises(ArmTargetRejected):
                frozen_table(BASE=bad)

    def test_freezing_requires_evidence(self):
        with self.assertRaisesRegex(ArmTargetRejected, "evidence"):
            arm_joint_table_with_frozen_ranges({ArmJoint.BASE: (0.0, 90.0)}, evidence="  ")

    def test_spec_requires_evidence(self):
        with self.assertRaisesRegex(ArmTargetRejected, "evidence"):
            ArmJointSpec(ArmJoint.BASE, None, "")

    def test_design_range_does_not_override_the_encoder_limit(self):
        # design_range 只是说明用的：超 999 允许，但它不放行任何下发。
        spec = ArmJointSpec(
            ArmJoint.BASE, None, "unit test", design_range_deg=(0.0, 360.0)
        )
        self.assertIsNone(spec.software_range_deg)
        self.assertFalse(spec.frozen)


class ArmRangeConfigParsingTests(unittest.TestCase):
    def test_parses_joint_range_pairs(self):
        self.assertEqual(
            parse_arm_joint_ranges("0:0:90;3:0:180"),
            {ArmJoint.BASE: (0.0, 90.0), ArmJoint.WRIST: (0.0, 180.0)},
        )

    def test_empty_text_freezes_nothing(self):
        self.assertEqual(parse_arm_joint_ranges(""), {})
        self.assertEqual(parse_arm_joint_ranges("   "), {})

    def test_malformed_text_is_rejected(self):
        for text in ("0:0", "zero:0:90", "0:0:90:1", "0:90:0", "0:0:1000"):
            with self.subTest(text=text), self.assertRaises(ArmTargetRejected):
                parse_arm_joint_ranges(text)

    def test_duplicate_joint_is_rejected(self):
        with self.assertRaisesRegex(ArmTargetRejected, "duplicate"):
            parse_arm_joint_ranges("0:0:90;0:0:45")

    def test_non_string_input_is_rejected(self):
        with self.assertRaises(ArmTargetRejected):
            parse_arm_joint_ranges(None)


class ArmPulseForecastTests(unittest.TestCase):
    """角度→脉宽的离线核算；与 arm.c 同一线性映射。"""

    def test_endpoints_match_the_firmware_pulse_ranges(self):
        for joint in ArmJoint:
            low, high = ARM_JOINT_PULSE_LIMITS_US[joint]
            travel = ARM_JOINT_ANGLE_RANGE_DEG[joint]
            with self.subTest(joint=joint.name):
                self.assertAlmostEqual(arm_pulse_us_for_angle(joint, 0.0), low)
                self.assertAlmostEqual(arm_pulse_us_for_angle(joint, travel), high)
                self.assertAlmostEqual(
                    arm_pulse_us_for_angle(joint, travel / 2.0),
                    (low + high) / 2.0,
                )

    def test_shoulder_is_physically_limited_to_1500us(self):
        # 80KG 肩按电机参数文件限制 500~1500µs，不是 2500µs。
        self.assertEqual(ARM_JOINT_PULSE_LIMITS_US[ArmJoint.SHOULDER], (500, 1500))

    def test_out_of_travel_angle_is_rejected_not_clamped(self):
        with self.assertRaisesRegex(ArmTargetRejected, "总行程"):
            arm_pulse_us_for_angle(ArmJoint.BASE, 271.0)
        with self.assertRaises(ArmTargetRejected):
            arm_pulse_us_for_angle(ArmJoint.BASE, -0.1)

    def test_unknown_joint_and_bad_angle_are_rejected(self):
        with self.assertRaises(ArmTargetRejected):
            arm_pulse_us_for_angle(7, 10.0)
        with self.assertRaises(ArmTargetRejected):
            arm_pulse_us_for_angle(ArmJoint.BASE, float("nan"))


class ArmPoseTests(unittest.TestCase):
    def test_pose_encodes_in_caller_order(self):
        pose = ArmPose(iter_arm_joint_targets([(ArmJoint.SHOULDER, 45), (ArmJoint.BASE, 90)]))
        self.assertEqual(pose.joints(), (ArmJoint.SHOULDER, ArmJoint.BASE))
        self.assertEqual(
            encode_arm_pose_parameters(pose, table=ALL_FROZEN), (1045, 90)
        )

    def test_pose_rejects_repeated_joint(self):
        with self.assertRaisesRegex(ArmTargetRejected, "repeats"):
            ArmPose(
                iter_arm_joint_targets([(ArmJoint.BASE, 10), (ArmJoint.BASE, 20)])
            )

    def test_pose_must_not_be_empty(self):
        with self.assertRaisesRegex(ArmTargetRejected, "at least one"):
            ArmPose(())

    def test_pose_rejects_non_target_entries(self):
        with self.assertRaises(ArmTargetRejected):
            ArmPose((ArmJoint.BASE,))

    def test_pose_validation_reports_the_offending_joint(self):
        pose = ArmPose(iter_arm_joint_targets([(ArmJoint.BASE, 10), (ArmJoint.SHOULDER, 10)]))
        with self.assertRaises(ArmJointNotFrozen) as caught:
            validate_arm_pose(pose, table=UNFROZEN_ARM_JOINT_TABLE)
        self.assertIn("BASE", str(caught.exception))

    def test_format_arm_pose_is_readable(self):
        pose = ArmPose(iter_arm_joint_targets([(ArmJoint.SHOULDER, 45)]))
        self.assertEqual(format_arm_pose(pose), "SHOULDER=45°")


class ArmZeroDisplacementReferenceTests(unittest.TestCase):
    """联调"零位移自检"用的参考角度：它必须真的对应固件的安全姿态脉宽。"""

    ARM_C = (
        pathlib.Path(__file__).resolve().parents[1]
        / "Four_Motor_PID_Test_1" / "Four_Motor_PID_Test" / "Core" / "Src" / "arm.c"
    )
    FIRMWARE_ORDER = ("BASE", "SHOULDER", "ELBOW", "WRIST", "GRIPPER")

    def test_reference_pulses_equal_the_firmware_safe_pose_when_present(self):
        if not self.ARM_C.exists():
            self.skipTest("STM32 firmware sources are not present in this checkout")
        block = re.search(
            r"ARM_SAFE_PULSE_US\s*\[[^\]]*\]\s*=\s*\{(.*?)\}",
            self.ARM_C.read_text(encoding="utf-8"),
            re.S,
        )
        self.assertIsNotNone(block, "ARM_SAFE_PULSE_US not found in arm.c")
        body = re.sub(r"/\*.*?\*/", "", block.group(1), flags=re.S)
        values = [
            int(item.strip().rstrip("Uu")) for item in body.split(",") if item.strip()
        ]
        self.assertEqual(
            {name: value for name, value in zip(self.FIRMWARE_ORDER, values)},
            {item.name: value for item, value in ARM_SAFE_REFERENCE_PULSE_US.items()},
        )

    def test_zero_displacement_targets_reproduce_the_safe_pose(self):
        # 允许 ≤5µs 的取整误差：远小于肉眼可见的位移，所以这些指令可以安全下发。
        for target in arm_zero_displacement_targets():
            with self.subTest(joint=target.joint.name):
                predicted = arm_pulse_us_for_angle(target.joint, target.angle_deg)
                self.assertLessEqual(
                    abs(predicted - ARM_SAFE_REFERENCE_PULSE_US[target.joint]), 5.0
                )

    def test_zero_displacement_targets_cover_exactly_the_arm_set_joints(self):
        joints = [target.joint for target in arm_zero_displacement_targets()]
        self.assertEqual(joints, list(ARM_SET_JOINTS))
        self.assertNotIn(ArmJoint.GRIPPER, joints)
        # 整度，否则进位编码会直接拒绝。
        self.assertTrue(all(target.angle_deg.is_integer() for target in arm_zero_displacement_targets()))

    def test_angle_for_pulse_is_the_inverse_of_pulse_for_angle(self):
        for joint in ArmJoint:
            low, high = ARM_JOINT_PULSE_LIMITS_US[joint]
            for pulse in (float(low), (low + high) / 2.0, float(high)):
                with self.subTest(joint=joint.name, pulse=pulse):
                    angle = arm_angle_for_pulse_us(joint, pulse)
                    self.assertAlmostEqual(
                        arm_pulse_us_for_angle(joint, angle), pulse, places=6
                    )

    def test_angle_for_pulse_rejects_bad_input(self):
        low, high = ARM_JOINT_PULSE_LIMITS_US[ArmJoint.SHOULDER]
        for bad in (low - 1, high + 1, float("nan"), "1000", True):
            with self.subTest(pulse=bad), self.assertRaises(ArmTargetRejected):
                arm_angle_for_pulse_us(ArmJoint.SHOULDER, bad)


class MockArmSetTests(unittest.TestCase):
    def test_success_records_the_new_angle(self):
        result = execute_mock_arm_set(
            MockArmState(), ArmJointTarget(ArmJoint.BASE, 45.0), table=ALL_FROZEN
        )
        self.assertTrue(result.success)
        self.assertEqual(result.error_code, 0)
        self.assertEqual(result.state.angle(ArmJoint.BASE), 45.0)
        self.assertIn("BASE=45", result.detail)

    def test_angle_lookup_returns_none_for_a_joint_never_commanded(self):
        self.assertIsNone(MockArmState().angle(ArmJoint.WRIST))

    def test_refusals_match_the_bridge_error_codes_and_keep_state(self):
        cases = (
            (ArmJoint.BASE, 45.0, UNFROZEN_ARM_JOINT_TABLE, ARM_ERROR_NOT_FROZEN),
            (ArmJoint.BASE, 200.0, ALL_FROZEN, ARM_ERROR_TARGET_REJECTED),
            (ArmJoint.GRIPPER, 10.0, ALL_FROZEN, ARM_ERROR_GRIPPER_POLICY),
        )
        for joint, angle, table, expected in cases:
            with self.subTest(joint=joint.name, angle=angle):
                before = MockArmState()
                result = execute_mock_arm_set(
                    before, ArmJointTarget(joint, angle), table=table
                )
                self.assertFalse(result.success)
                self.assertEqual(result.error_code, expected)
                self.assertEqual(result.state, before)

    def test_mock_rejects_the_same_targets_as_the_real_path(self):
        """mock 与真机必须对同一个请求给出同一结论。

        2026-08-19 的缺陷：整度校验只写在 `encode_arm_set_parameter` 里，而 mock
        不编码（只做策略校验），于是 joint=0, angle=45.5 在 mock 返回成功/0、
        在真机返回 9011，并且 mock 把 45.5° 记进了状态——一个进位编码根本装不下
        的角度。本测试把“两侧结论一致”钉成断言，而不只是文档承诺。
        """
        cases = (
            (ArmJoint.BASE, 45.5, ALL_FROZEN),
            (ArmJoint.BASE, 0.5, ALL_FROZEN),
            (ArmJoint.BASE, 45.0, ALL_FROZEN),
            (ArmJoint.BASE, 200.0, ALL_FROZEN),
            (ArmJoint.GRIPPER, 10.0, ALL_FROZEN),
            (ArmJoint.SHOULDER, 30.0, UNFROZEN_ARM_JOINT_TABLE),
        )
        for joint, angle, table in cases:
            with self.subTest(joint=joint.name, angle=angle):
                target = ArmJointTarget(joint, angle)
                try:
                    encode_arm_set_parameter(target, table=table)
                except (ArmTargetRejected, ArmJointNotFrozen) as exc:
                    real_code = arm_rejection_error_code(exc)
                else:
                    real_code = 0
                mock = execute_mock_arm_set(
                    MockArmState(), target, table=table
                )
                self.assertEqual(
                    mock.error_code,
                    real_code,
                    f"{joint.name}={angle} 两侧结论不一致："
                    f"mock={mock.error_code} / real={real_code}",
                )

    def test_fractional_angle_keeps_mock_state_unchanged(self):
        before = MockArmState(((ArmJoint.BASE, 10.0),))
        result = execute_mock_arm_set(
            before, ArmJointTarget(ArmJoint.BASE, 45.5), table=ALL_FROZEN
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ARM_ERROR_TARGET_REJECTED)
        self.assertEqual(result.state, before)

    def test_configured_failure_keeps_the_previous_state(self):
        state = MockArmState(((ArmJoint.BASE, 10.0),))
        result = execute_mock_arm_set(
            state,
            ArmJointTarget(ArmJoint.BASE, 80.0),
            table=ALL_FROZEN,
            configured_success=False,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, MOCK_ARM_SET_FAILED)
        self.assertEqual(result.state, state)

    def test_successive_targets_accumulate_per_joint(self):
        state = MockArmState()
        for joint, angle in ((ArmJoint.BASE, 10.0), (ArmJoint.SHOULDER, 20.0)):
            state = execute_mock_arm_set(
                state, ArmJointTarget(joint, angle), table=ALL_FROZEN
            ).state
        self.assertEqual(state.angle(ArmJoint.BASE), 10.0)
        self.assertEqual(state.angle(ArmJoint.SHOULDER), 20.0)
        self.assertEqual(len(state.angles_deg), 2)


if __name__ == "__main__":
    unittest.main()
