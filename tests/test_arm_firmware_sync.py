"""树莓派侧机械臂表 ↔ STM32 固件源码的一致性测试。

为什么要这个测试：`robogame_core/arm.py` 里的关节编号、脉宽上下限、总行程是
**从固件 `arm.h` / `arm.c` 抄过来的**。抄来的东西会漂移——固件改了肩的上限，
而树莓派继续用旧值预测脉宽，两侧就悄悄不一致了。这里直接解析固件源码逐项比对，
固件改一项而树莓派没跟，测试就红。

证据边界：本测试只证明“固件源码里的数字与 Python 常量相等”，**不证明**固件
被编译过、烧录过或有任何硬件行为。固件工程当前未被 git 跟踪，在缺少源码的
检出上整个文件跳过。
"""
import pathlib
import re
import unittest

from robogame_core.arm import (
    ARM_JOINT_ANGLE_RANGE_DEG,
    ARM_JOINT_PULSE_LIMITS_US,
    ArmJoint,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "Four_Motor_PID_Test_1" / "Four_Motor_PID_Test" / "Core"
ARM_H = FIRMWARE / "Inc" / "arm.h"
ARM_C = FIRMWARE / "Src" / "arm.c"

FIRMWARE_PRESENT = ARM_H.exists() and ARM_C.exists()
SKIP_REASON = (
    "STM32 firmware sources (arm.h / arm.c) are not present in this checkout"
)


def enum_members(source: str, enum_name: str) -> dict[str, int]:
    """解析 `typedef enum { ... } <enum_name>;`，返回 名称→取值（含隐含递增）。"""
    block = re.search(
        r"typedef\s+enum\s*\{(.*?)\}\s*" + re.escape(enum_name) + r"\s*;",
        source,
        re.S,
    )
    if block is None:
        raise AssertionError(f"enum {enum_name} not found in firmware source")
    body = re.sub(r"/\*.*?\*/", "", block.group(1), flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    members: dict[str, int] = {}
    value = 0
    for raw in body.split(","):
        entry = raw.strip()
        if not entry:
            continue
        match = re.fullmatch(r"(\w+)\s*(?:=\s*(\d+))?", entry)
        if match is None:
            continue
        if match.group(2) is not None:
            value = int(match.group(2))
        members[match.group(1)] = value
        value += 1
    return members


def array_values(source: str, name: str) -> list[int]:
    """解析 `static const uint16_t NAME[...] = { v, v, ... };` 的整数列表。"""
    block = re.search(
        re.escape(name) + r"\s*\[[^\]]*\]\s*=\s*\{(.*?)\}", source, re.S
    )
    if block is None:
        raise AssertionError(f"array {name} not found in firmware source")
    body = re.sub(r"/\*.*?\*/", "", block.group(1), flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    values = []
    for raw in body.split(","):
        entry = raw.strip().rstrip("Uu").strip()
        if not entry:
            continue
        values.append(int(entry))
    return values


def joint_names_in_firmware_order(source: str) -> list[str]:
    """返回 arm.h 里 ARM_JOINT_* 的关节名（去掉 COUNT 哨兵），按编号排序。"""
    members = enum_members(source, "Arm_Joint")
    count = members.get("ARM_JOINT_COUNT")
    names = {
        name: value
        for name, value in members.items()
        if name.startswith("ARM_JOINT_") and name != "ARM_JOINT_COUNT"
    }
    if count is not None and count != len(names):
        raise AssertionError(
            f"ARM_JOINT_COUNT={count} but {len(names)} joint members exist"
        )
    return [name for name, _ in sorted(names.items(), key=lambda pair: pair[1])]


@unittest.skipUnless(FIRMWARE_PRESENT, SKIP_REASON)
class FirmwareParserTests(unittest.TestCase):
    """先证明解析器真的读到了东西，否则下面的比对等于没测。"""

    @classmethod
    def setUpClass(cls):
        cls.header = ARM_H.read_text(encoding="utf-8")
        cls.source = ARM_C.read_text(encoding="utf-8")

    def test_joint_enum_is_parsed_in_order(self):
        self.assertEqual(
            joint_names_in_firmware_order(self.header),
            [
                "ARM_JOINT_BASE",
                "ARM_JOINT_SHOULDER",
                "ARM_JOINT_ELBOW",
                "ARM_JOINT_WRIST",
                "ARM_JOINT_GRIPPER",
            ],
        )

    def test_all_three_tables_are_parsed(self):
        for name in (
            "ARM_JOINT_PULSE_MIN",
            "ARM_JOINT_PULSE_MAX",
            "ARM_JOINT_ANGLE_RANGE_DEG",
        ):
            with self.subTest(array=name):
                self.assertEqual(len(array_values(self.source, name)), 5)


@unittest.skipUnless(FIRMWARE_PRESENT, SKIP_REASON)
class ArmFirmwareSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.header = ARM_H.read_text(encoding="utf-8")
        cls.source = ARM_C.read_text(encoding="utf-8")

    def test_python_joint_ids_equal_the_firmware_enum(self):
        names = joint_names_in_firmware_order(self.header)
        self.assertEqual(
            {name.removeprefix("ARM_JOINT_"): index for index, name in enumerate(names)},
            {item.name: int(item) for item in ArmJoint},
        )

    def test_python_pulse_limits_equal_the_firmware_tables(self):
        names = joint_names_in_firmware_order(self.header)
        lows = array_values(self.source, "ARM_JOINT_PULSE_MIN")
        highs = array_values(self.source, "ARM_JOINT_PULSE_MAX")
        mirrored = {
            ArmJoint[name.removeprefix("ARM_JOINT_")]: (low, high)
            for name, low, high in zip(names, lows, highs)
        }
        self.assertEqual(dict(ARM_JOINT_PULSE_LIMITS_US), mirrored)

    def test_python_angle_ranges_equal_the_firmware_table(self):
        names = joint_names_in_firmware_order(self.header)
        ranges = array_values(self.source, "ARM_JOINT_ANGLE_RANGE_DEG")
        mirrored = {
            ArmJoint[name.removeprefix("ARM_JOINT_")]: float(value)
            for name, value in zip(names, ranges)
        }
        self.assertEqual(dict(ARM_JOINT_ANGLE_RANGE_DEG), mirrored)

    def test_gripper_channel_still_uses_the_firmware_open_close_endpoints(self):
        # 爪不是角度关节：固件里就是 1200（开）/1540（闭）两个端点。
        self.assertEqual(ARM_JOINT_PULSE_LIMITS_US[ArmJoint.GRIPPER], (1200, 1540))
        self.assertRegex(self.header, r"#define\s+ARM_GRIPPER_OPEN_PULSE_US\s+1200U")
        self.assertRegex(self.header, r"#define\s+ARM_GRIPPER_CLOSE_PULSE_US\s+1540U")


@unittest.skipUnless(FIRMWARE_PRESENT, SKIP_REASON)
class FirmwareStillRejectsMechanismCommandsTests(unittest.TestCase):
    """固件现状：0x20 一律回 BAD_STATE，机械臂尚未接入。

    这个断言不是“验收标准”，而是**现状锚点**：等电控真的实现了 ARM_SET 通道，
    这条测试会红——那时应当把它改成对实现内容的断言，而不是继续假设“固件还不支持”。
    """

    def test_mechanism_command_is_dispatched_to_the_handler(self):
        """0x20 必须路由到 RPI_HandleMechanismCommand，而不是被静默忽略。

        现状（2026-08-19 复核）：机械臂通道已实现（GRAB/RELEASE/LIFT_ABS/
        STOP/HOME/ARM_SET），因此**不再**一律回 BAD_STATE——旧断言
        "case 之后 200 字符内出现 RPI_ACK_BAD_STATE" 已过期并长期为红。
        这里改为锚定路由本身，另加一条"安全门仍在"的检查。
        """
        source = (FIRMWARE / "Src" / "rpi_protocol.c").read_text(encoding="utf-8")
        self.assertIn("case RPI_MSG_MECHANISM_COMMAND:", source)
        block = source.split("case RPI_MSG_MECHANISM_COMMAND:", 1)[1][:200]
        self.assertIn("RPI_HandleMechanismCommand(", block)
        self.assertNotIn("RPI_ACK_BAD_STATE", block)

    def test_mechanism_handler_keeps_the_authorization_gate(self):
        """占位/未授权时必须回 BAD_STATE，机械臂不能裸控。"""
        source = (FIRMWARE / "Src" / "rpi_protocol.c").read_text(encoding="utf-8")
        handler = source.split("static void RPI_HandleMechanismCommand(", 1)[1]
        self.assertIn("RPI_ACK_BAD_STATE", handler[:4000])


if __name__ == "__main__":
    unittest.main()
