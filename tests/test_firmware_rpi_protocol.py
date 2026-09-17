"""固件 0x20/0x21 机械臂通道的**跨语言**主机验收（gcc + 树莓派侧编解码器）。

这个测试把两边接在一起跑，不需要任何硬件：

```text
robogame_core.serial_protocol.encode_frame        （树莓派侧真编码器）
        ↓ 生成 0x03 HELLO / 0x02 HEARTBEAT / 0x20 MECHANISM_COMMAND / 0x22 EMERGENCY_STOP
真实固件 rpi_protocol.c + arm.c                    （gcc 主机编译，只顶替 HAL/USB/巡线）
        ↓ 回 0x13 ACK / 0x21 MECHANISM_STATUS / 0x12 STATUS
robogame_core.serial_protocol.decode_frame 等      （树莓派侧真解码器）
```

所以它同时证明两件事：
1. 树莓派**发出去的字节**固件能读懂（含 `parameter = joint_id*1000 + angle_deg`）；
2. 固件**发回来的字节**树莓派能读懂（ACK 结果码、0x21 状态与错误码、0x12 标志位）。

覆盖路径：未授权拒绝、正常 ARM_SET 到位、爪子抓放端点、无升降装置明确失败、
参数越界拒绝、超时失败、重发幂等（不重新执行）、STOP 停在半路、断链看门狗停自动动作、
急停停自动动作、以及 STATUS 标志位（爪闭合为命令推算值、方块存在恒为 0）。

证据边界：**不能**证明 Keil 能编译整个工程、不能证明烧录成功、不能证明舵机真的会动、
不能证明角度标定正确。它只证明"协议两层对得上 + 固件状态机行为符合设计"。
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from robogame_core.serial_protocol import (
    MSG_TYPE_ACK,
    MSG_TYPE_EMERGENCY_STOP,
    MSG_TYPE_HELLO,
    MSG_TYPE_MECHANISM_COMMAND,
    MSG_TYPE_MECHANISM_STATUS,
    MSG_TYPE_STATUS,
    STATUS_CUBE_PRESENT,
    STATUS_EMERGENCY_STOP,
    STATUS_GRIPPER_CLOSED,
    MechanismCommand,
    MechanismOperation,
    MechanismState,
    StreamDecoder,
    decode_ack,
    decode_mechanism_status,
    decode_status,
    encode_frame,
    encode_hello,
    encode_mechanism_command,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
FW = ROOT / "Four_Motor_PID_Test_1" / "Four_Motor_PID_Test"
STUBS = ROOT / "tests" / "host_stubs"

ARM_ERROR_NO_LIFT = 3010
ARM_ERROR_TIMEOUT = 3020

ACK_OK = 0
ACK_BAD_PARAMETER = 4
ACK_BAD_STATE = 5


def command_frame(sequence, command_id, operation, parameter=0, timeout_ms=3000):
    return encode_frame(
        MSG_TYPE_MECHANISM_COMMAND,
        sequence,
        encode_mechanism_command(
            MechanismCommand(command_id, operation, parameter, timeout_ms)
        ),
    )


def arm_set_frame(sequence, command_id, joint, angle_deg, timeout_ms):
    return command_frame(
        sequence,
        command_id,
        MechanismOperation.ARM_SET,
        joint * 1000 + angle_deg,
        timeout_ms,
    )


def build_script():
    """按顺序返回 (脚本行列表, 期望值字典)。帧用树莓派侧真编码器生成。"""
    hello = encode_frame(MSG_TYPE_HELLO, 1, encode_hello())
    emergency_stop = encode_frame(MSG_TYPE_EMERGENCY_STOP, 14)

    lines = [
        "# 由 tests/test_firmware_rpi_protocol.py 生成；帧来自树莓派侧真编码器",
        "boot",
        "armed 0",
        "rc 0",
        # --- 1) HELLO 建会话 ---
        f"frame {hello.hex()}",
        "tick 20",
        "check 0 0                 # 未授权：不输出 PWM",
        # --- 2) 未授权时下 ARM_SET 必须被拒（ACK=5），且不产生任何动作 ---
        # command_id 用 100，与后面正常命令区分开：真实运行里 command_id 每条命令唯一。
        f"frame {arm_set_frame(2, 100, 0, 270, 8000).hex()}",
        "tick 20",
        "check 0 0",
        # --- 3) 授权：进入安全姿态，不跟遥控（rc 0） ---
        "armed 1",
        "tick 20",
        "check 0 1474              # ARM_SAFE_PULSE_US[腰]",
        "check_pending 0",
        # --- 4) 正常 ARM_SET：腰 0°->270°（500->2500µs），限速不瞬跳 ---
        f"frame {arm_set_frame(3, 1, 0, 270, 8000).hex()}",
        "tick 20",
        "check_pending 1",
        "tick 4000",
        "check 0 2500",
        "check_pending 0",
        # --- 5) 爪子：GRAB / RELEASE 就是两个脉宽端点 ---
        f"frame {command_frame(4, 2, MechanismOperation.GRAB, 0, 5000).hex()}",
        "tick 500",
        "check 4 1540",
        "check_closed 1",
        f"frame {command_frame(5, 3, MechanismOperation.RELEASE, 0, 5000).hex()}",
        "tick 3000",
        "check 4 1200",
        "check_closed 0",
        # --- 6) 真车没有升降装置：必须明确 FAILED 3010，而不是假装接受 ---
        f"frame {command_frame(6, 4, MechanismOperation.LIFT_ABS, 123, 3000).hex()}",
        "tick 40",
        # --- 7) 参数越界：角度 999°、关节编号 5 都要被拒 ---
        f"frame {arm_set_frame(7, 5, 0, 999, 3000).hex()}",
        "tick 40",
        f"frame {arm_set_frame(8, 6, 5, 10, 3000).hex()}",
        "tick 40",
        # --- 8) 超时：肩 1086->1500µs 需要约 2.5s，只给 200ms ---
        f"frame {arm_set_frame(9, 7, 1, 270, 200).hex()}",
        "tick 600",
        "check_timedout 1",
        "check_pending 0",
        # --- 9) 重发幂等：同一 command_id 再发一次，不许重新执行也不许 BAD_STATE ---
        f"frame {arm_set_frame(10, 8, 3, 270, 8000).hex()}",
        "tick 20",
        "check_timedout 0",
        "check_pending 1",
        f"frame {arm_set_frame(11, 8, 3, 270, 8000).hex()}",
        "tick 20",
        "check_pending 1          # 重发不重新开始，目标仍然在跟踪",
        "tick 500",
        "check_range 3 700 2500   # 腕在移动途中",
        "check_pending 1",
        # --- 10) STOP：停在半路，保持姿态 ---
        f"frame {command_frame(12, 9, MechanismOperation.STOP, 0, 1000).hex()}",
        "tick 40",
        "check_pending 0",
        "check_range 3 700 2499   # 没走到底就停了",
        # --- 11) 断链：关心跳超过 250ms，看门狗必须停自动动作 ---
        "heartbeat 0",
        f"frame {arm_set_frame(13, 10, 2, 270, 8000).hex()}",
        "tick 20",
        "check_pending 1",
        "tick 400",
        "check_pending 0          # 看门狗停了自动目标",
        "heartbeat 1",
        # --- 12) 软件急停：同样停自动动作，并置 STATUS 急停位 ---
        f"frame {emergency_stop.hex()}",
        "tick 40",
        "check_pending 0",
        "print_tx",
    ]
    return lines


def decode_tx(stdout):
    decoder = StreamDecoder()
    frames = []
    for line in stdout.splitlines():
        if line.startswith("TX "):
            frames.extend(decoder.feed(bytes.fromhex(line[3:].strip())))
    return frames


class FirmwareRpiProtocolHostTests(unittest.TestCase):
    """真实固件 + 真实上位机编解码器的跨语言验收。"""

    @classmethod
    def setUpClass(cls):
        cls.skip_reason = None
        compiler = shutil.which("gcc")
        if compiler is None:
            cls.skip_reason = "Host gcc is required for the firmware protocol host test"
            return

        cls.temp = tempfile.TemporaryDirectory(prefix="rpi_proto_")
        cls.addClassCleanup(cls.temp.cleanup)
        directory = pathlib.Path(cls.temp.name)
        exe = directory / ("rpi_proto_test.exe" if os.name == "nt" else "rpi_proto_test")
        script = directory / "scenario.txt"
        script.write_text("\n".join(build_script()) + "\n", encoding="utf-8")

        cls.compile = subprocess.run(
            [
                compiler,
                "-std=gnu99",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(STUBS),
                "-I",
                str(FW / "Core" / "Inc"),
                str(STUBS / "rpi_protocol_test_main.c"),
                str(FW / "Core" / "Src" / "rpi_protocol.c"),
                str(FW / "Core" / "Src" / "arm.c"),
                "-o",
                str(exe),
            ],
            capture_output=True,
            text=True,
        )
        # 注意：不要叫 cls.run —— 那会覆盖 unittest.TestCase.run。
        cls.scenario = None
        if cls.compile.returncode == 0:
            cls.scenario = subprocess.run(
                [str(exe), str(script)], capture_output=True, text=True, timeout=180
            )

    def setUp(self):
        if self.skip_reason:
            self.skipTest(self.skip_reason)

    def _frames(self):
        self.assertEqual(
            self.compile.returncode,
            0,
            "firmware host compile failed:\n"
            f"stdout:\n{self.compile.stdout}\nstderr:\n{self.compile.stderr}",
        )
        self.assertIsNotNone(self.scenario)
        self.assertEqual(
            self.scenario.returncode,
            0,
            "firmware scenario reported failures:\n"
            f"stdout:\n{self.scenario.stdout}\nstderr:\n{self.scenario.stderr}",
        )
        self.assertIn("all checks passed", self.scenario.stdout)
        return decode_tx(self.scenario.stdout)

    def _acks(self):
        return {
            decode_ack(frame.payload)[0]: decode_ack(frame.payload)[1]
            for frame in self._frames()
            if frame.message_type == MSG_TYPE_ACK
        }

    def _mechanism_statuses(self):
        return [
            decode_mechanism_status(frame.payload)
            for frame in self._frames()
            if frame.message_type == MSG_TYPE_MECHANISM_STATUS
        ]

    def _statuses(self):
        return [
            decode_status(frame.payload)
            for frame in self._frames()
            if frame.message_type == MSG_TYPE_STATUS
        ]

    def test_firmware_host_scenario_passes(self):
        # 固件内部断言（脉宽、目标、超时、爪标志）全过，才会走到这里。
        self._frames()

    def test_hello_is_acknowledged(self):
        self.assertEqual(self._acks().get(1), ACK_OK)

    def test_command_without_authorization_is_refused(self):
        # 未授权时 0x20 必须回 BAD_STATE(5)，与固件改动前的行为一致。
        self.assertEqual(self._acks().get(2), ACK_BAD_STATE)
        # 而且一条 0x21 都不该发：这条命令根本没被接受。
        self.assertEqual(
            [s for s in self._mechanism_statuses() if s.command_id == 100], []
        )

    def test_arm_set_reaches_succeeded_with_the_pi_operation_number(self):
        statuses = [
            s for s in self._mechanism_statuses()
            if s.command_id == 1 and s.operation == MechanismOperation.ARM_SET
        ]
        states = [s.state for s in statuses]
        self.assertIn(MechanismState.ACCEPTED, states)
        self.assertIn(MechanismState.RUNNING, states)
        self.assertIn(MechanismState.SUCCEEDED, states)
        # 成功必须带 error_code = 0，否则上位机会判失败。
        succeeded = [s for s in statuses if s.state == MechanismState.SUCCEEDED]
        self.assertEqual([s.error_code for s in succeeded], [0])

    def test_grab_and_release_report_success(self):
        for command_id, operation in ((2, MechanismOperation.GRAB), (3, MechanismOperation.RELEASE)):
            with self.subTest(operation=operation.name):
                states = [
                    s.state for s in self._mechanism_statuses()
                    if s.command_id == command_id and s.operation == operation
                ]
                self.assertIn(MechanismState.ACCEPTED, states)
                self.assertIn(MechanismState.SUCCEEDED, states)

    def test_lift_reports_no_lift_hardware_instead_of_faking_success(self):
        # 机械组 C-3：真车没有升降装置。固件必须明确失败并给出专用错误码。
        lift = [
            s for s in self._mechanism_statuses()
            if s.operation == MechanismOperation.LIFT_ABS
        ]
        self.assertEqual(len(lift), 1)
        self.assertEqual(lift[0].state, MechanismState.FAILED)
        self.assertEqual(lift[0].error_code, ARM_ERROR_NO_LIFT)

    def test_out_of_range_parameters_are_rejected_by_ack(self):
        acks = self._acks()
        # 角度 999° 超出总行程；关节编号 5 不存在。
        self.assertEqual(acks.get(7), ACK_BAD_PARAMETER)
        self.assertEqual(acks.get(8), ACK_BAD_PARAMETER)
        # 被拒的命令不许产生 0x21：不能一半拒绝一半执行。
        rejected_ids = {5, 6}
        self.assertEqual(
            [s for s in self._mechanism_statuses() if s.command_id in rejected_ids], []
        )

    def test_timeout_is_reported_as_failed_with_a_dedicated_code(self):
        timed_out = [
            s for s in self._mechanism_statuses()
            if s.command_id == 7 and s.operation == MechanismOperation.ARM_SET
        ]
        self.assertTrue(timed_out)
        self.assertEqual(timed_out[-1].state, MechanismState.FAILED)
        self.assertEqual(timed_out[-1].error_code, ARM_ERROR_TIMEOUT)

    def test_retransmitted_command_is_acknowledged_without_error(self):
        # 上位机丢 ACK 会重发同一条命令。若固件回 BAD_STATE(5)，
        # robot_bridge 的 tracker 会把整条命令判失败 —— 那才是真故障。
        self.assertEqual(self._acks().get(11), ACK_OK)

    def test_stop_succeeds(self):
        stop = [
            s for s in self._mechanism_statuses()
            if s.operation == MechanismOperation.STOP
        ]
        self.assertEqual(len(stop), 1)
        self.assertEqual(stop[0].state, MechanismState.SUCCEEDED)

    def test_status_reports_gripper_flag_as_a_command_value(self):
        statuses = self._statuses()
        self.assertTrue(statuses, "firmware must publish 0x12 STATUS")
        closed = [s for s in statuses if s.has(STATUS_GRIPPER_CLOSED)]
        # GRAB 之后必须出现过"闭合"标志，RELEASE 之后最终回到"张开"。
        self.assertTrue(closed, "GRIPPER_CLOSED must be set after GRAB")
        self.assertFalse(statuses[-1].has(STATUS_GRIPPER_CLOSED))

    def test_status_never_claims_a_cube_sensor_it_does_not_have(self):
        # 本车没有任何方块存在传感器。固件绝不能把没测到的东西报成测到了。
        statuses = self._statuses()
        self.assertFalse(
            [s for s in statuses if s.has(STATUS_CUBE_PRESENT)],
            "CUBE_PRESENT must stay 0: there is no cube sensor on this car",
        )

    def test_emergency_stop_is_reflected_in_status(self):
        statuses = self._statuses()
        self.assertTrue(statuses[-1].has(STATUS_EMERGENCY_STOP))


if __name__ == "__main__":
    unittest.main()
