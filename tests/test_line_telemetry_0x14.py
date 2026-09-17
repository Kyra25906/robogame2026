"""0x14 巡线遥测：线上向量 + STM32 固件源码契约检查。

背景：真车巡线链路此前断在"STM32 没有 0x14 发送实现"。
本测试锁定两件事，防止这条通道再次静默断掉：

1. **线上向量冻结**：21 字节 payload 的字节序、12bit 钳位、flags 位置，
   与 `docs/field/LINE_TELEMETRY_0x14_INTERFACE_ALIGNMENT_2026-08-19.md`
   第 2 节的定义逐字节一致（含冻结的十六进制向量）。
2. **固件源码契约**：`rpi_protocol.c` 里 0x14 的载荷长度、消息号、12bit 钳位、
   保留位清零、以及 `analog_valid` 由"在线 且 最近一帧为 $A"共同判定
   ——这几条一旦被改坏，本测试报红。

证据边界：
- 能证明：Python 侧编码与固件源码中的常量/结构确实一致，且冻结向量未被改动。
- **不能证明**：STM32 实际编译通过、USB CDC 实传、真车探头发出的值正确。
  后两者只能由 Keil 编译 + 现场 `ros2 topic echo /line_sensor` 提供证据。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import unittest

from robogame_core.serial_protocol import (
    MSG_TYPE_LINE_TELEMETRY,
    LineTelemetrySample,
    decode_frame,
    decode_line_telemetry,
    encode_frame,
    encode_line_telemetry,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "Four_Motor_PID_Test_1" / "Four_Motor_PID_Test" / "Core"
RPI_PROTOCOL_C = FIRMWARE / "Src" / "rpi_protocol.c"
RPI_PROTOCOL_H = FIRMWARE / "Inc" / "rpi_protocol.h"
LINE_SENSOR_C = FIRMWARE / "Src" / "line_sensor.c"
LINE_SENSOR_H = FIRMWARE / "Inc" / "line_sensor.h"

FIRMWARE_PRESENT = all(
    path.exists()
    for path in (RPI_PROTOCOL_C, RPI_PROTOCOL_H, LINE_SENSOR_C, LINE_SENSOR_H)
)
SKIP_REASON = (
    "STM32 firmware sources are not present in this checkout "
    "(Four_Motor_PID_Test_1 is untracked locally)"
)

# 冻结向量：序号 5，tick=0x01020304，通道 0/1/2/3/4092/4093/4094/4095，flags=1
FROZEN_SAMPLE = LineTelemetrySample(
    0x01020304, (0, 1, 2, 3, 4092, 4093, 4094, 4095), True
)
FROZEN_FRAME_HEX = "AA55011405001500040302010000010002000300FC0FFD0FFE0FFF0F011A3E"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def build_host_harness() -> pathlib.Path | None:
    """用主机 gcc 把固件源码编成可执行文件；不可用时返回 None。

    见 tools/build_stm32_host_harness.py 的说明：它把 `rpi_protocol.c`
    原样编译，仅把 CMSIS 的 ARM 内联汇编与 HAL 外设实现换成主机垫片。
    """
    import shutil

    if shutil.which("gcc") is None:
        return None
    script = ROOT / "tools" / "build_stm32_host_harness.py"
    if not script.exists():
        return None
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    path = pathlib.Path(lines[-1])
    return path if path.exists() else None


HARNESS = build_host_harness()
HARNESS_SKIP_REASON = (
    "host gcc build of the STM32 sources is unavailable in this environment"
)


class LineTelemetryWireVectorTests(unittest.TestCase):
    """线上格式：与冻结文档逐字段对齐。"""

    def test_payload_is_21_bytes_with_specified_layout(self):
        # Arrange
        sample = LineTelemetrySample(
            0xDEADBEEF, (0, 1, 2, 3, 4, 5, 6, 7), True
        )

        # Act
        payload = encode_line_telemetry(sample)

        # Assert：u32 tick(4) + u16 x8(16) + u8 flags(1)
        self.assertEqual(len(payload), 21)
        self.assertEqual(payload[0:4], bytes((0xEF, 0xBE, 0xAD, 0xDE)))
        for index in range(8):
            self.assertEqual(
                payload[4 + 2 * index : 6 + 2 * index],
                bytes((index, 0)),
                msg=f"channel {index} must be little-endian u16",
            )
        self.assertEqual(payload[20], 0x01)

    def test_analog_invalid_sets_flag_bit0_to_zero(self):
        payload = encode_line_telemetry(
            LineTelemetrySample(1, (0,) * 8, False)
        )
        self.assertEqual(payload[20], 0x00)

    def test_frozen_frame_vector_unchanged(self):
        frame = encode_frame(0x14, 5, encode_line_telemetry(FROZEN_SAMPLE))
        self.assertEqual(frame.hex().upper(), FROZEN_FRAME_HEX)

    def test_decoder_accepts_the_frozen_vector(self):
        frame_bytes = bytes.fromhex(FROZEN_FRAME_HEX)
        payload = frame_bytes[8:8 + 21]
        self.assertEqual(decode_line_telemetry(payload), FROZEN_SAMPLE)

    def test_message_type_matches_document(self):
        self.assertEqual(MSG_TYPE_LINE_TELEMETRY, 0x14)


@unittest.skipUnless(HARNESS is not None, HARNESS_SKIP_REASON)
class FirmwareProducedBytesTests(unittest.TestCase):
    """把**真实编译出来的固件产物**跑一遍，拿它发出的字节与 Python 比对。

    这是本文件里证据最强的一组：不是读源码猜，而是让
    `RPI_SendLineTelemetry()` 与 `RPI_SendFrame()`（CRC16 封帧）真的执行。

    仍然不能证明：Keil/ARM 目标上的编译、USB CDC 实传时序、真车探头读数。
    """

    def _run(self, *args: str) -> bytes:
        completed = subprocess.run(
            [str(HARNESS), *args], capture_output=True, text=True
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"harness failed: {completed.stderr}",
        )
        return bytes.fromhex(completed.stdout.strip())

    def test_payload_matches_python_encoder(self):
        """payload 字节必须与 Python 编码器完全相同（序号与帧头另行比较）。"""
        values = (0, 1, 2, 3, 4092, 4093, 4094, 4095)
        frame = self._run("frame", "0x01020304", *[str(v) for v in values], "1")

        expected_payload = encode_line_telemetry(
            LineTelemetrySample(0x01020304, values, True)
        )
        self.assertEqual(frame[8:8 + 21], expected_payload)

    def test_frame_structure_is_a_valid_v1_frame(self):
        frame = self._run("frame", "0x01020304", "0", "1", "2", "3", "4092",
                          "4093", "4094", "4095", "1")
        self.assertEqual(frame[0:2], b"\xAA\x55")
        self.assertEqual(frame[2], 1)
        self.assertEqual(frame[3], 0x14)
        self.assertEqual(int.from_bytes(frame[6:8], "little"), 21)
        self.assertEqual(len(frame), 8 + 21 + 2)

    def test_crc_is_accepted_by_the_python_decoder(self):
        """整帧交给 Python 的帧解析（含 CRC 校验），必须能解出原始样本。"""
        frame = self._run("frame", "0x01020304", "0", "1", "2", "3", "4092",
                          "4093", "4094", "4095", "1")
        decoded = decode_frame(frame)
        self.assertEqual(decoded.message_type, 0x14)
        self.assertEqual(
            decode_line_telemetry(decoded.payload), FROZEN_SAMPLE
        )

    def test_out_of_range_channel_is_clamped_to_12_bits(self):
        frame = self._run("frame", "1", "5000", "0", "0", "0", "0", "0", "0",
                          "0", "1")
        # payload 从帧偏移 8 起：前 4 字节 tick，随后才是 ch0
        self.assertEqual(frame[8 + 4:8 + 6], b"\xFF\x0F")

    def test_offline_flag_zero_is_encoded(self):
        frame = self._run("frame", "1", "100", "200", "300", "400", "500",
                          "600", "700", "800", "0")
        self.assertEqual(frame[8 + 20], 0x00)
        # 掉线时仍然发帧，通道值保留上次采样值
        self.assertEqual(int.from_bytes(frame[8 + 4:8 + 6], "little"), 100)

    def test_scheduled_path_reports_values_from_line_analog(self):
        """走 RPI_Update() 的遥测调度分支，通道值来自 line_analog[]。"""
        frame = self._run("sched")
        self.assertEqual(frame[3], 0x14, "调度路径必须能产出 0x14")
        self.assertEqual(
            int.from_bytes(frame[6:8], "little"),
            21,
            "0x14 的 payload 必须是 21 字节（不能是 STATUS 的 12）",
        )
        channels = [
            int.from_bytes(frame[8 + 4 + 2 * i : 8 + 6 + 2 * i], "little")
            for i in range(8)
        ]
        self.assertEqual(channels, [1000 + 10 * i for i in range(8)])
        self.assertEqual(frame[8 + 20], 0x01, "在线且最近帧为 $A 时 flags=1")

    def test_scheduled_path_clears_flag_when_latest_frame_is_digital(self):
        """模块回传 $D 时 line_online 仍为 1，但模拟值并非本帧刷新。"""
        frame = self._run("sched-invalid")
        self.assertEqual(frame[3], 0x14)
        self.assertEqual(frame[8 + 20], 0x00)


@unittest.skipUnless(FIRMWARE_PRESENT, SKIP_REASON)
class LineTelemetryFirmwareContractTests(unittest.TestCase):
    """固件源码契约：0x14 的发送实现必须还在，且关键约束没被改坏。

    这些是**源码级**检查（读文本正则），能挡住"有人把 20ms 调度删了"或
    "把 4095 钳位改成 65535"这类回归，但挡不住编译错误与运行期行为。
    """

    def setUp(self):
        self.protocol_c = read(RPI_PROTOCOL_C)
        self.protocol_h = read(RPI_PROTOCOL_H)
        self.sensor_c = read(LINE_SENSOR_C)
        self.sensor_h = read(LINE_SENSOR_H)

    def test_message_id_and_send_function_exist(self):
        self.assertRegex(
            self.protocol_c, r"#define\s+RPI_MSG_LINE_TELEMETRY\s+0x14U"
        )
        self.assertRegex(
            self.protocol_h,
            r"uint8_t\s+RPI_SendLineTelemetry\s*\(\s*uint32_t\s+tick_ms",
        )
        self.assertRegex(
            self.protocol_c,
            r"uint8_t\s+RPI_SendLineTelemetry\s*\(\s*uint32_t\s+tick_ms",
        )

    def test_payload_length_is_21_bytes(self):
        # 声明与实现都必须是 uint8_t payload[21]
        self.assertRegex(self.protocol_c, r"uint8_t\s+payload\[21\]")
        self.assertIn("sizeof(payload)", self.protocol_c)

    def test_channels_are_clamped_to_12_bits(self):
        # 越界值会让上位机整帧拒收（decode_line_telemetry 检查 >4095）
        self.assertRegex(self.protocol_c, r"if\s*\(value\s*>\s*4095U\)")
        self.assertRegex(self.protocol_c, r"value\s*=\s*4095U")

    def test_reserved_flag_bits_are_cleared(self):
        # 上位机会校验 (flags & 0xFE) == 0，所以只能写 0 或 1
        self.assertRegex(
            self.protocol_c,
            r"payload\[20\]\s*=\s*\(analog_valid\s*!=\s*0U\)\s*\?\s*1U\s*:\s*0U",
        )

    def test_send_is_scheduled_periodically(self):
        """10ms 节拍交替：两条流各 50Hz，且不会落在同一毫秒。"""
        self.assertRegex(
            self.protocol_c,
            r"now\s*-\s*rpi_last_telemetry_tick\s*\)\s*>=\s*RPI_TELEMETRY_TICK_MS",
        )
        self.assertRegex(self.protocol_c, r"#define\s+RPI_TELEMETRY_TICK_MS\s+10U")
        # 交替发车：奇数拍发 STATUS，偶数拍发巡线
        self.assertRegex(self.protocol_c, r"rpi_telemetry_phase\s*=\s*\(rpi_telemetry_phase")

    def test_analog_valid_requires_online_and_latest_analog_frame(self):
        """只看 line_online 会在模块回传 $D 时误报模拟值有效。"""
        self.assertRegex(
            self.protocol_c,
            r"\(line_online\s*!=\s*0U\)\s*&&\s*\(line_analog_frame_latest\s*!=\s*0U\)",
        )

    def test_sensor_tracks_latest_frame_type(self):
        self.assertRegex(
            self.sensor_h, r"extern\s+volatile\s+uint8_t\s+line_analog_frame_latest"
        )
        self.assertRegex(
            self.sensor_c,
            r"line_analog_frame_latest\s*=\s*\(type\s*==\s*'A'\)\s*\?\s*1U\s*:\s*0U",
        )

    def test_sensor_reports_offline_when_uart7_is_lent_to_the_pi(self):
        """UART7 借给树莓派时必须清掉有效标志，否则上送假有效数据。"""
        self.assertRegex(
            self.sensor_c,
            r"line_online\s*=\s*0U;\s*line_analog_frame_latest\s*=\s*0U;",
        )

    def test_digital_values_are_not_sent_over_the_wire(self):
        """约定：只上送 12bit 模拟原始值，数字 0/1 留在 Keil Watch。"""
        function_body = re.search(
            r"uint8_t\s+RPI_SendLineTelemetry\s*\([^)]*\)\s*\{(.*?)\n\}",
            self.protocol_c,
            re.S,
        )
        self.assertIsNotNone(function_body, "RPI_SendLineTelemetry body not found")
        self.assertNotIn("line_digital", function_body.group(1))


if __name__ == "__main__":
    unittest.main()
