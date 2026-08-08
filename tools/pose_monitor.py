#!/usr/bin/env python3
"""实时位姿质量监控工具。

订阅 /pose、/wheel_odom、/imu/data、/cmd_vel、/motion/goal，
实时显示位姿、速度、IMU 状态、角速度分歧和导航误差。

不需要依赖视觉，纯里程定位质量监控。

用法：
  source /opt/ros/jazzy/setup.bash
  source ~/robogame_git/ros2_ws/install/setup.bash
  python3 tools/pose_monitor.py

按 Ctrl+C 退出。
"""

import math
import time

import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu
from geometry_msgs.msg import Pose2D, Twist


def quat_to_yaw(x, y, z, w):
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def normalize_angle(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


class PoseMonitor(Node):
    def __init__(self):
        super().__init__("pose_monitor")

        self.pose = None
        self.pose_x = 0.0
        self.pose_y = 0.0
        self.pose_yaw = 0.0
        self.pose_vx = 0.0
        self.pose_vy = 0.0
        self.pose_wz = 0.0
        self.pose_count = 0
        self.pose_rate = 0.0
        self._pose_times = []

        self.imu_wz = None
        self.imu_age = None
        self.imu_count = 0
        self.imu_rate = 0.0
        self._imu_times = []
        self._latest_imu_time = None
        self.imu_stale_s = 0.2

        self.wheel_wz = 0.0
        self.wheel_count = 0
        self.wheel_rate = 0.0
        self._wheel_times = []

        self.cmd_vx = 0.0
        self.cmd_wz = 0.0

        self.goal_x = None
        self.goal_y = None
        self.goal_yaw = None

        # 分歧统计
        self._divergence_samples = []
        self._max_divergence = 0.0

        self.create_subscription(Odometry, "/pose", self._on_pose, 10)
        self.create_subscription(Imu, "/imu/data", self._on_imu, 10)
        self.create_subscription(Odometry, "/wheel_odom", self._on_wheel, 10)
        self.create_subscription(Twist, "/cmd_vel", self._on_cmd, 10)
        self.create_subscription(Pose2D, "/motion/goal", self._on_goal, 10)

        self._last_print = time.monotonic()
        self.create_timer(0.2, self._print_status)

    def _on_pose(self, msg):
        self.pose = msg
        self.pose_x = msg.pose.pose.position.x
        self.pose_y = msg.pose.pose.position.y
        q = msg.pose.pose.orientation
        self.pose_yaw = quat_to_yaw(q.x, q.y, q.z, q.w)
        self.pose_vx = msg.twist.twist.linear.x
        self.pose_vy = msg.twist.twist.linear.y
        self.pose_wz = msg.twist.twist.angular.z
        self.pose_count += 1
        now = time.monotonic()
        self._pose_times.append(now)
        self._pose_times = [t for t in self._pose_times if now - t < 2.0]
        if len(self._pose_times) >= 2:
            self.pose_rate = (len(self._pose_times) - 1) / (self._pose_times[-1] - self._pose_times[0])

    def _on_imu(self, msg):
        self.imu_wz = float(msg.angular_velocity.z)
        self.imu_count += 1
        now = time.monotonic()
        self._latest_imu_time = now
        self._imu_times.append(now)
        self._imu_times = [t for t in self._imu_times if now - t < 2.0]
        if len(self._imu_times) >= 2:
            self.imu_rate = (len(self._imu_times) - 1) / (self._imu_times[-1] - self._imu_times[0])

    def _on_wheel(self, msg):
        self.wheel_wz = float(msg.twist.twist.angular.z)
        self.wheel_count += 1
        now = time.monotonic()
        self._wheel_times.append(now)
        self._wheel_times = [t for t in self._wheel_times if now - t < 2.0]
        if len(self._wheel_times) >= 2:
            self.wheel_rate = (len(self._wheel_times) - 1) / (self._wheel_times[-1] - self._wheel_times[0])

    def _on_cmd(self, msg):
        self.cmd_vx = msg.linear.x
        self.cmd_wz = msg.angular.z

    def _on_goal(self, msg):
        self.goal_x = msg.x
        self.goal_y = msg.y
        self.goal_yaw = msg.theta

    def _print_status(self):
        now = time.monotonic()

        if self._latest_imu_time is not None:
            self.imu_age = now - self._latest_imu_time
        else:
            self.imu_age = None

        # 角速度分歧
        divergence = 0.0
        if self.imu_wz is not None and self.imu_age is not None and self.imu_age < self.imu_stale_s:
            divergence = abs(self.wheel_wz - self.imu_wz)
            self._divergence_samples.append(divergence)
            if len(self._divergence_samples) > 100:
                self._divergence_samples.pop(0)
            if divergence > self._max_divergence:
                self._max_divergence = divergence

        avg_div = (sum(self._divergence_samples) / len(self._divergence_samples)) if self._divergence_samples else 0.0

        # 导航误差
        nav_str = ""
        if self.goal_x is not None:
            pos_err = math.hypot(self.goal_x - self.pose_x, self.goal_y - self.pose_y)
            yaw_err = abs(normalize_angle(self.goal_yaw - self.pose_yaw))
            nav_str = (
                f"  导航误差: 位置={pos_err:.3f}m  角度={math.degrees(yaw_err):.1f}°\n"
            )

        # IMU 状态
        if self.imu_age is None:
            imu_str = "未收到"
        elif self.imu_age > self.imu_stale_s:
            imu_str = f"过期 {self.imu_age:.3f}s > {self.imu_stale_s}s"
        else:
            imu_str = f"正常 {self.imu_age:.3f}s"

        # 分歧状态
        div_flag = ""
        if divergence > 0.5:
            div_flag = " ← 分歧过大!"
        elif divergence > 0.2:
            div_flag = " ← 轻微分歧"

        print("\033[2J\033[H", end="")  # 清屏
        print("=" * 60)
        print("  位姿质量监控  (Ctrl+C 退出)")
        print("=" * 60)
        print(f"  位姿: x={self.pose_x:+.4f}  y={self.pose_y:+.4f}  yaw={self.pose_yaw:+.4f} ({math.degrees(self.pose_yaw):+.1f}°)")
        print(f"  速度: vx={self.pose_vx:+.3f}  vy={self.pose_vy:+.3f}  wz={self.pose_wz:+.3f}")
        print(f"  命令: vx={self.cmd_vx:+.3f}  wz={self.cmd_wz:+.3f}")
        print("-" * 60)
        print(f"  IMU 状态: {imu_str}")
        print(f"  角速度: wheel={self.wheel_wz:+.4f}  imu={self.imu_wz if self.imu_wz is not None else 'N/A':>7}")
        print(f"  分歧:   当前={divergence:.4f}  均值={avg_div:.4f}  峰值={self._max_divergence:.4f}{div_flag}")
        print("-" * 60)
        print(f"  频率: /pose={self.pose_rate:.0f}Hz  /imu={self.imu_rate:.0f}Hz  /wheel={self.wheel_rate:.0f}Hz")
        print(f"  帧数: pose={self.pose_count}  imu={self.imu_count}  wheel={self.wheel_count}")
        if nav_str:
            print("-" * 60)
            print(nav_str, end="")
        print("=" * 60)
        if self.pose is None:
            print("  ⚠ 未收到 /pose")
        if self.pose_count == 0 and self.imu_count == 0 and self.wheel_count == 0:
            print("  ⚠ 未收到任何数据，确认 localization/robot_bridge 是否在运行")


def main():
    rclpy.init()
    node = PoseMonitor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        print("\n监控结束")
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
