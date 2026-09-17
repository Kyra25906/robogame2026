"""合成 8 路灰度数据源（无硬件联调用，0x14 冻结前的输入替身）。

按 `pattern` 参数周期发布 `/line_sensor`（LineSensor，8 路原始值
0..4095，与 robot_bridge 真车解码一致），模拟线在不同位置/状态下的读数，
用于验证 line_follow_controller 的状态机与纠偏输出。

pattern：
- centered    线居中（通道 3/4 附近黑）
- left        线偏左
- right       线偏右
- sine        线中心在 -1..+1 间正弦摆动（模拟贴线修正过程，最接近真实）
- lost        无黑线（出线）
- intersection 6 路激活（交叉口）
- all_black   8 路全激活

极性约定：`line_value`（黑线）必须**大于** `floor_value`（白底），与
`line_follow_controller` 的默认标定同向，且与
`docs/line_follow/CALIBRATION_AND_HARDWARE.md:53-58` 的
`(raw - white_ref)/(black_ref - white_ref)`、1.0=黑线 一致。
方向搞反的后果不是“读数不准”，而是**语义翻转**：出线会被判成全黑并继续前进、
全黑会被判成出线而停车，`sine` 下转向输出恒为 0（验收假通过）。
`tests/test_line_mock_polarity.py` 专门守住这一条。
"""

from __future__ import annotations

import math
import time

import rclpy
from rclpy.node import Node
from robogame_interfaces.msg import LineSensor

# 8 路传感器归一化位置（-1=最左，+1=最右），与 robogame_core.line_follow 一致
SENSOR_POSITIONS = tuple((i - 3.5) / 3.5 for i in range(8))

PATTERNS = frozenset({
    "centered", "left", "right", "sine", "lost", "intersection", "all_black",
})


class LineSensorMockNode(Node):
    def __init__(self) -> None:
        super().__init__("line_sensor_mock")
        self.declare_parameter("pattern", "sine")
        self.declare_parameter("period_s", 0.02)
        # 黑线读数 > 白底读数（模块黑线反光弱→模拟值大）。方向必须与
        # line_follow_controller 的默认 white_ref/black_ref 一致，见模块 docstring。
        self.declare_parameter("line_value", 0.9)
        self.declare_parameter("floor_value", 0.1)
        self.declare_parameter("line_half_width", 0.28)
        self.declare_parameter("sweep_hz", 0.5)

        self.pattern = str(self.get_parameter("pattern").value)
        if self.pattern not in PATTERNS:
            raise ValueError(f"unknown mock pattern: {self.pattern}")
        self.period_s = float(self.get_parameter("period_s").value)
        self.line_value = float(self.get_parameter("line_value").value)
        self.floor_value = float(self.get_parameter("floor_value").value)
        self.line_half_width = float(
            self.get_parameter("line_half_width").value
        )
        self.sweep_hz = float(self.get_parameter("sweep_hz").value)
        if self.period_s <= 0.0:
            raise ValueError("period_s must be positive")

        self.pub = self.create_publisher(LineSensor, "/line_sensor", 10)
        self._phase = 0.0
        self.create_timer(self.period_s, self._tick)

    def _tick(self) -> None:
        values = self._values()
        msg = LineSensor()
        msg.stamp = self.get_clock().now().to_msg()
        msg.mcu_tick_ms = int(time.monotonic() * 1000.0) & 0xFFFFFFFF
        msg.channels = [int(round(v * 4095.0)) for v in values]
        msg.analog_valid = True
        self.pub.publish(msg)
        self._phase += self.period_s

    def _values(self) -> list[float]:
        if self.pattern == "lost":
            return [self.floor_value] * 8
        if self.pattern == "all_black":
            return [self.line_value] * 8
        if self.pattern == "intersection":
            # 前 6 路激活 → active_count == 6（INTERSECTION）
            return [self.line_value] * 6 + [self.floor_value] * 2
        if self.pattern == "left":
            center = -0.6
        elif self.pattern == "right":
            center = 0.6
        elif self.pattern == "centered":
            center = 0.0
        else:  # sine
            center = math.sin(2.0 * math.pi * self.sweep_hz * self._phase)

        values = []
        for pos in SENSOR_POSITIONS:
            distance = abs(pos - center)
            if distance <= self.line_half_width:
                # 线内：越靠近线心越黑（给加权法连续值）
                weight = 1.0 - distance / self.line_half_width
                values.append(
                    self.floor_value
                    + (self.line_value - self.floor_value) * weight
                )
            else:
                values.append(self.floor_value)
        return values


def main(args=None) -> None:
    rclpy.init(args=args)
    node = LineSensorMockNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
