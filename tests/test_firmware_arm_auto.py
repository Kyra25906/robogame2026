"""机械臂自动控制固件的**主机编译 + 行为验证**（gcc，不需要硬件）。

背景：树莓派侧已经有 0x20 ARM_SET 通道，固件侧此前对一切 0x20 回 BAD_STATE。
本轮给 arm.c 加了自动控制状态机（目标/限速/超时/遥控夺权/撤权保持）。
ARM 交叉编译器在本机不存在（无 arm-none-eabi-gcc、无 Keil），无法构建真机镜像；
但 arm.c 的自动控制逻辑是纯算法，可以在主机上用 gcc 编译真实源码并驱动验证。

本测试做两件事：

1. 用 `gcc -Wall -Wextra -Werror` 编译 **真实的** `Core/Src/arm.c`
   （只有 HAL 被 `tests/host_stubs/stm32f4xx_hal.h` 顶替，
   main.h / arm.h / remote.h / safety.h 都是原件），要求零警告；
2. 运行 `tests/host_stubs/arm_auto_test_main.c`，断言自动控制行为：
   角度→脉宽换算、越界拒绝、限速不瞬跳、到位清除、超时放弃并保持姿态、
   遥控夺权清目标（左摇杆不算）、撤权保持姿态不掉电、爪子抓放端点、HOME。

证据边界：**不能**证明 Keil 能编译整个工程、不能证明烧录成功、不能证明舵机
真的会动或角度标定正确。它只证明 arm.c 的自动控制状态机行为符合设计。
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FW = ROOT / "Four_Motor_PID_Test_1" / "Four_Motor_PID_Test"
STUBS = ROOT / "tests" / "host_stubs"


def compiler():
    return shutil.which("gcc")


@unittest.skipUnless(compiler(), "Host gcc is required for the arm auto host test")
class FirmwareArmAutoHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="arm_auto_")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.exe = pathlib.Path(cls.temp.name) / (
            "arm_auto_test.exe" if os.name == "nt" else "arm_auto_test"
        )
        cls.compile = subprocess.run(
            [
                compiler(),
                "-std=gnu99",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(STUBS),
                "-I",
                str(FW / "Core" / "Inc"),
                str(STUBS / "arm_auto_test_main.c"),
                str(FW / "Core" / "Src" / "arm.c"),
                "-o",
                str(cls.exe),
            ],
            capture_output=True,
            text=True,
        )

    def test_real_arm_c_compiles_without_warnings(self):
        self.assertEqual(
            self.compile.returncode,
            0,
            "arm.c host compile failed:\n"
            f"stdout:\n{self.compile.stdout}\nstderr:\n{self.compile.stderr}",
        )

    def test_automatic_control_behaviour_passes_on_the_host(self):
        if self.compile.returncode != 0:
            self.fail(
                "cannot run the behaviour test because arm.c did not compile"
            )
        run = subprocess.run(
            [str(self.exe)], capture_output=True, text=True, timeout=120
        )
        self.assertEqual(
            run.returncode,
            0,
            "arm auto host test reported failures:\n"
            f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}",
        )
        self.assertIn("all checks passed", run.stdout)


if __name__ == "__main__":
    unittest.main()
