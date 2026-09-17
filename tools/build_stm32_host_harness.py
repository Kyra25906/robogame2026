"""用主机 gcc 编译 STM32 固件源码，验证 0x14 巡线遥测的**真实编码字节**。

为什么值得做：`RPI_SendLineTelemetry()` 的字节序、12bit 钳位、flags 位置
如果只靠 Python 侧镜像实现来验证，等于用同一份理解检查自己。这里把
**固件源码原样编译**，真实调用该函数，再把它交给 CDC 的字节拿出来与
冻结的线上向量比对。

做法：`-S` 生成汇编 → 过滤掉 ARM 内联汇编助记符行（固件被测代码本身没有
内联汇编，只有测试垫片里的临界区函数有；x86 汇编器认不了它们）→ 汇编链接。

用法：
    python tools/build_stm32_host_harness.py
    python tools/build_stm32_host_harness.py --print-cmd

退出码 0 表示构建成功，产物路径写到 stdout。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "Four_Motor_PID_Test_1" / "Four_Motor_PID_Test"
STUBS = ROOT / "tests" / "stm32_host_stubs"

SOURCES = (
    FIRMWARE / "Core" / "Src" / "line_sensor.c",
    FIRMWARE / "Core" / "Src" / "rpi_protocol.c",
    STUBS / "line_telemetry_harness.c",
)

INCLUDE_DIRS = (
    STUBS,
    FIRMWARE / "Core" / "Inc",
    FIRMWARE / "Drivers" / "STM32F4xx_HAL_Driver" / "Inc",
    FIRMWARE / "Drivers" / "STM32F4xx_HAL_Driver" / "Inc" / "Legacy",
    FIRMWARE / "Drivers" / "CMSIS" / "Device" / "ST" / "STM32F4xx" / "Include",
    FIRMWARE / "Drivers" / "CMSIS" / "Include",
)

# 与 Keil 工程 UV4 的 Define 一致（见 MDK-ARM/*.uvprojx）。
DEFINES = ("USE_HAL_DRIVER", "STM32F427xx")

# CMSIS 里的 ARM 内联汇编助记符；测试垫片函数会被生成出来，但 x86 汇编器不认。
ARM_MNEMONIC = re.compile(
    r"\b(cpsie|cpsid|cpsidif|cpsief|mrs|msr|isb|dsb|dmb)\b", re.IGNORECASE
)


def base_command() -> list[str]:
    cmd = ["gcc", "-std=c99", "-w", "-O0", "-include", "host_arm_compat.h"]
    cmd += [f"-D{define}" for define in DEFINES]
    cmd += [f"-I{directory}" for directory in INCLUDE_DIRS]
    return cmd


def run(cmd: list[str], *, step: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if result.returncode != 0:
        sys.stderr.write(f"[{step}] failed (exit {result.returncode})\n")
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(1)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print-cmd", action="store_true", help="只打印编译命令，不执行"
    )
    parser.add_argument("--out", help="可执行文件输出路径")
    args = parser.parse_args()

    compiler = shutil.which("gcc")
    if compiler is None:
        sys.stderr.write("gcc not found on PATH\n")
        return 2

    missing = [str(path) for path in SOURCES if not path.exists()]
    if missing:
        sys.stderr.write("missing sources: " + ", ".join(missing) + "\n")
        return 2

    if args.print_cmd:
        print(" ".join(base_command()))
        return 0

    out_dir = pathlib.Path(tempfile.mkdtemp(prefix="stm32_host_"))
    out_exe = pathlib.Path(args.out) if args.out else out_dir / "line_telemetry_test.exe"

    objects: list[str] = []
    for index, source in enumerate(SOURCES):
        asm_path = out_dir / f"unit{index}.s"
        run(
            base_command() + ["-S", "-o", str(asm_path), str(source)],
            step=f"-S {source.name}",
        )
        kept = [
            line
            for line in asm_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if not ARM_MNEMONIC.search(line)
        ]
        asm_path.write_text("\n".join(kept) + "\n", encoding="ascii")

        object_path = out_dir / f"unit{index}.o"
        run([compiler, "-c", "-o", str(object_path), str(asm_path)], step=f"as {source.name}")
        objects.append(str(object_path))

    run([compiler, "-o", str(out_exe), *objects], step="link")

    print(out_exe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
