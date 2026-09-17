"""line_follow_mock_smoke：巡线 mock 链路验收（launch 内自动退出）。

订阅 /line_sensor、/line_follow/status 与 /cmd_vel，采样 `sample_duration_s`
秒，断言：

1. /line_sensor 持续收到 8 路数据（mock 在发）；
2. /cmd_vel 出现**非零**命令（sine 摆动下巡线纠偏在输出，且仲裁门控放行）；
3. **转向通道真的在动**（`angular.z != 0` 或状态里 `dev != 0`）——这一条是
   2026-08-19 补的：mock 与控制器黑白极性相反时，控制器全程只直行不转向，
   而第 2 条仅凭 `vx_base` 就能满足，验收会假通过；
4. /line_follow/status 出现且状态字段可解析。

通过打印 PASS 并以 0 退出；失败打印 FAIL 细节并以 1 退出。
"""

from __future__ import annotations

import json
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from robogame_interfaces.msg import LineSensor
from std_msgs.msg import String


class LineFollowMockSmoke(Node):
    def __init__(self) -> None:
        super().__init__("line_follow_mock_smoke")
        self.declare_parameter("sample_duration_s", 3.0)
        self.sample_duration_s = float(
            self.get_parameter("sample_duration_s").value
        )
        self.started_at = time.monotonic()
        self.sensor_frames = 0
        self.nonzero_cmd_vel = 0
        self.steering_frames = 0
        self.status_frames = 0
        self.exit_code: int | None = None

        self.create_subscription(
            LineSensor, "/line_sensor", self._on_sensor, 10
        )
        self.create_subscription(
            Twist, "/cmd_vel", self._on_cmd_vel, 10
        )
        self.create_subscription(
            String, "/line_follow/status", self._on_status, 10
        )
        self.create_timer(0.05, self._tick)

    def _on_sensor(self, msg: LineSensor) -> None:
        if len(msg.channels) == 8 and msg.analog_valid:
            self.sensor_frames += 1

    def _on_cmd_vel(self, msg: Twist) -> None:
        if any(abs(v) > 1e-6 for v in (
            msg.linear.x, msg.linear.y, msg.angular.z
        )):
            self.nonzero_cmd_vel += 1
        # 转向通道必须活着。只断言"有非零命令"会被 vx_base 的直行速度永远满足：
        # 黑白极性接反时（2026-08-19 实际发生过）控制器一次都不转向，验收却 PASS。
        if abs(msg.angular.z) > 1e-6:
            self.steering_frames += 1

    def _on_status(self, msg: String) -> None:
        if msg.data.startswith("state="):
            self.status_frames += 1
        # 偏差非零＝算法真的在纠偏（不是在直行）。dev 为 NaN 时 json 里是 null。
        marker = "#diag#"
        if marker in msg.data:
            try:
                diag = json.loads(msg.data.split(marker, 1)[1])
            except (ValueError, TypeError):
                return
            if abs(diag.get("dev") or 0.0) > 1e-6:
                self.steering_frames += 1

    def _tick(self) -> None:
        if self.exit_code is not None:
            return
        if time.monotonic() - self.started_at < self.sample_duration_s:
            return
        if (
            self.sensor_frames > 0
            and self.status_frames > 0
            and self.nonzero_cmd_vel > 0
            and self.steering_frames > 0
        ):
            self.get_logger().info(
                f"PASS sensor_frames={self.sensor_frames} "
                f"status_frames={self.status_frames} "
                f"nonzero_cmd_vel={self.nonzero_cmd_vel} "
                f"steering_frames={self.steering_frames}"
            )
            self.exit_code = 0
            rclpy.shutdown()
        else:
            self.get_logger().error(
                f"FAIL sensor_frames={self.sensor_frames} "
                f"status_frames={self.status_frames} "
                f"nonzero_cmd_vel={self.nonzero_cmd_vel} "
                f"steering_frames={self.steering_frames}"
                + (
                    "；转向通道一次都没动——先查 line_sensor_mock 与"
                    " line_follow_controller 的黑白极性是否同向"
                    if self.steering_frames == 0
                    else ""
                )
            )
            self.exit_code = 1
            rclpy.shutdown()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = LineFollowMockSmoke()
    try:
        rclpy.spin(node)
    except ExternalShutdownException:
        pass
    finally:
        exit_code = 1 if node.exit_code is None else node.exit_code
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
