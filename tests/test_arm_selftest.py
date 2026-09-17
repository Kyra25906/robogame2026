"""机械臂联调自检的无 ROS 单测。

`tools/arm_selftest.py` 不 import ROS：服务调用、状态、时钟全部注入，
所以每一级验收的判定逻辑都能在这里跑，不必等硬件。
"""
import asyncio
import dataclasses
import unittest

from tools.arm_selftest import (
    NO_LIFT_ERROR_CODE,
    NOT_FROZEN_ERROR_CODE,
    STALE_ERROR_CODE,
    ArmSelftest,
    stage_names,
)


@dataclasses.dataclass
class FakeSafety:
    received_at: float | None = 100.0
    communication_ok: bool = True
    physical_start: bool = True
    emergency_stop: bool = False
    mechanism_fault: bool = False
    gripper_closed: bool = False


class FakeRos:
    """记录每一次服务调用，并按脚本返回结果。"""

    def __init__(self, *, arm_results=None, mechanism_results=None, services=None):
        self.arm_results = list(arm_results or [])
        self.mechanism_results = dict(mechanism_results or {})
        self.services = services if services is not None else {
            "grab": True, "release": True, "stop": True, "arm_set_joint": True,
        }
        self.arm_calls = []
        self.mechanism_calls = []

    def service_status(self):
        return dict(self.services)

    def arm_set_joint(self, joint, angle_deg, timeout_s):
        self.arm_calls.append((joint, angle_deg, timeout_s))
        if self.arm_results:
            return self.arm_results.pop(0)
        return {"success": True, "error_code": 0, "detail": "ok"}

    def mechanism(self, action, timeout_s=3.0, height_m=None):
        self.mechanism_calls.append((action, timeout_s, height_m))
        return self.mechanism_results.get(action, {"success": True, "error_code": 0, "detail": "ok"})


def build(ros, *, safety=None, mode="OBSERVE", flag_sequence=None, busy=False):
    """组装一个 ArmSelftest；flag_sequence 控制 gripper_closed 的等待结果。"""
    state = {"safety": safety or FakeSafety(), "busy": busy, "released": 0, "now": 100.0}
    flags = list(flag_sequence or [])

    async def wait_flag(attr, want, timeout_s):
        if flags:
            return flags.pop(0)
        return True, str(want)

    def claim_busy():
        if state["busy"]:
            return False
        state["busy"] = True
        return True

    def release_busy():
        state["released"] += 1
        state["busy"] = False

    runner = ArmSelftest(
        ros=ros,
        safety=lambda: state["safety"],
        mode=lambda: mode,
        wait_flag=wait_flag,
        claim_busy=claim_busy,
        release_busy=release_busy,
        now=lambda: state["now"],
    )
    return runner, state


def failed(steps):
    return [item["name"] for item in steps if not item["ok"]]


class StagePlanTests(unittest.TestCase):
    def test_stage_plan_has_the_five_documented_stages(self):
        plan = stage_names()
        self.assertEqual(
            [item["stage"] for item in plan],
            ["link", "arm_path", "gripper", "timeout", "boundaries"],
        )
        self.assertTrue(all(item["label"] for item in plan))


class PrecheckTests(unittest.IsolatedAsyncioTestCase):
    """前置条件不满足时，绝不能带着未知状态去动机械臂。"""

    async def test_missing_status_blocks_every_stage(self):
        ros = FakeRos()
        runner, _ = build(ros, safety=FakeSafety(received_at=None))
        for stage in ("arm_path", "gripper", "timeout", "boundaries"):
            with self.subTest(stage=stage):
                result = await runner.run(stage)
                self.assertFalse(result["passed"])
                self.assertIn("收到 /robot/status", failed(result["steps"]))
        self.assertEqual(ros.arm_calls, [])
        self.assertEqual(ros.mechanism_calls, [])

    async def test_stale_status_blocks(self):
        ros = FakeRos()
        runner, _ = build(ros, safety=FakeSafety(received_at=99.0))
        result = await runner.run("gripper")
        self.assertFalse(result["passed"])
        self.assertEqual(ros.mechanism_calls, [])

    async def test_not_authorized_blocks(self):
        ros = FakeRos()
        runner, _ = build(ros, safety=FakeSafety(physical_start=False))
        result = await runner.run("gripper")
        self.assertFalse(result["passed"])
        self.assertIn("物理授权（长按 PB2）", failed(result["steps"]))
        self.assertEqual(ros.mechanism_calls, [])

    async def test_emergency_stop_blocks(self):
        ros = FakeRos()
        runner, _ = build(ros, safety=FakeSafety(emergency_stop=True))
        result = await runner.run("boundaries")
        self.assertFalse(result["passed"])
        self.assertEqual(ros.mechanism_calls, [])

    async def test_chassis_moving_blocks_mechanism_actions(self):
        # C-4：车走的时候不要动爪子。
        ros = FakeRos()
        runner, _ = build(ros, mode="MANUAL")
        result = await runner.run("gripper")
        self.assertFalse(result["passed"])
        self.assertIn("底盘不在运动", failed(result["steps"]))
        self.assertEqual(ros.mechanism_calls, [])

    async def test_busy_mechanism_refuses_without_calling_services(self):
        ros = FakeRos()
        runner, _ = build(ros, busy=True)
        result = await runner.run("arm_path")
        self.assertFalse(result["passed"])
        self.assertIn("机构空闲", failed(result["steps"]))
        self.assertEqual(ros.arm_calls, [])


class LinkStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_good_passes(self):
        runner, _ = build(FakeRos())
        result = await runner.run("link")
        self.assertTrue(result["passed"], failed(result["steps"]))

    async def test_missing_arm_service_is_reported_with_the_fix(self):
        ros = FakeRos(services={"grab": True, "release": True, "stop": True,
                                "arm_set_joint": False})
        runner, _ = build(ros)
        result = await runner.run("link")
        self.assertFalse(result["passed"])
        item = next(i for i in result["steps"] if i["name"] == "机构服务就绪")
        self.assertIn("arm_set_joint", item["actual"])
        self.assertIn("colcon build", item["note"])


class ArmPathStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_the_zero_displacement_angles_for_four_joints(self):
        ros = FakeRos()
        runner, state = build(ros)
        result = await runner.run("arm_path")
        self.assertTrue(result["passed"], failed(result["steps"]))
        # 只发 ARM_SET 允许的四个关节（爪子走 GRAB/RELEASE）。
        self.assertEqual([call[0] for call in ros.arm_calls], [0, 1, 2, 3])
        # 角度必须是整度，且是“回到安全姿态”的角度：
        # 腰 131°→1470µs（安全 1474）、肩 158°→1085（1086）、肘 135°→1500（1500）、
        # 腕 25°→685（683）。误差 ≤ 4µs，肉眼不可见。
        self.assertEqual([call[1] for call in ros.arm_calls], [131.0, 158.0, 135.0, 25.0])
        self.assertEqual(state["released"], 1)

    async def test_unfrozen_joint_is_reported_with_the_fix(self):
        ros = FakeRos(arm_results=[{"success": False, "error_code": NOT_FROZEN_ERROR_CODE,
                                    "detail": "BASE 未冻结"}])
        runner, _ = build(ros)
        result = await runner.run("arm_path")
        self.assertFalse(result["passed"])
        item = next(i for i in result["steps"] if not i["ok"])
        self.assertIn("arm_joint_ranges", item["note"])

    async def test_busy_is_released_even_when_a_call_raises(self):
        class Boom(FakeRos):
            def arm_set_joint(self, joint, angle_deg, timeout_s):
                raise RuntimeError("serial exploded")

        ros = Boom()
        runner, state = build(ros)
        with self.assertRaises(RuntimeError):
            await runner.run("arm_path")
        self.assertEqual(state["released"], 1)
        self.assertFalse(state["busy"])


class GripperStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_grab_then_release_checks_both_flag_transitions(self):
        ros = FakeRos()
        runner, _ = build(ros, flag_sequence=[(True, "True"), (True, "False")])
        result = await runner.run("gripper")
        self.assertTrue(result["passed"], failed(result["steps"]))
        self.assertEqual([c[0] for c in ros.mechanism_calls], ["grab", "release"])

    async def test_flag_that_never_flips_fails(self):
        ros = FakeRos()
        runner, _ = build(ros, flag_sequence=[(False, "False"), (True, "False")])
        result = await runner.run("gripper")
        self.assertFalse(result["passed"])
        self.assertIn("GRAB 后 gripper_closed", failed(result["steps"]))

    async def test_failed_grab_does_not_wait_for_the_flag(self):
        ros = FakeRos(mechanism_results={"grab": {"success": False, "error_code": 3020,
                                                  "detail": "超时"}})
        runner, _ = build(ros)
        result = await runner.run("gripper")
        self.assertFalse(result["passed"])
        self.assertNotIn("GRAB 后 gripper_closed", [i["name"] for i in result["steps"]])


class TimeoutAndBoundaryStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_stage_passes_only_on_3020(self):
        ros = FakeRos(arm_results=[{"success": False, "error_code": STALE_ERROR_CODE,
                                    "detail": "mechanism command ended as FAILED"}])
        runner, _ = build(ros)
        result = await runner.run("timeout")
        self.assertTrue(result["passed"], failed(result["steps"]))
        # 腕（关节 3）是这一级故意制造超时的关节，预算必须远小于真实行程时间。
        self.assertEqual(ros.arm_calls, [(3, 270.0, 0.3)])

    async def test_timeout_stage_fails_if_the_mcu_reports_success(self):
        ros = FakeRos(arm_results=[{"success": True, "error_code": 0, "detail": "ok"}])
        runner, _ = build(ros)
        result = await runner.run("timeout")
        self.assertFalse(result["passed"])

    async def test_boundaries_expects_stop_ok_and_lift_rejected(self):
        ros = FakeRos(mechanism_results={
            "lift": {"success": False, "error_code": NO_LIFT_ERROR_CODE,
                     "detail": "no lift hardware"},
        })
        runner, _ = build(ros)
        result = await runner.run("boundaries")
        self.assertTrue(result["passed"], failed(result["steps"]))
        self.assertEqual(ros.mechanism_calls[0][0], "stop")
        self.assertEqual(ros.mechanism_calls[1], ("lift", 3.0, 0.1))

    async def test_boundaries_fails_if_lift_is_accepted(self):
        ros = FakeRos(mechanism_results={"lift": {"success": True, "error_code": 0}})
        runner, _ = build(ros)
        result = await runner.run("boundaries")
        self.assertFalse(result["passed"])
        item = next(i for i in result["steps"] if i["name"].startswith("升降"))
        self.assertIn("预期行为", item["note"])


class UnknownStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_stage_is_rejected_with_the_available_list(self):
        runner, _ = build(FakeRos())
        with self.assertRaisesRegex(ValueError, "未知自检阶段"):
            await runner.run("nope")


if __name__ == "__main__":
    unittest.main()
