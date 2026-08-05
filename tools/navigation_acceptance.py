#!/usr/bin/env python3
"""导航验收脚本：5 组目标 x N 轮，记录位姿误差和结果到 CSV。

前置条件（4 个终端已启动，不要开 mission_manager）：
  T1: ros2 run robot_bridge robot_bridge --ros-args --params-file .../robot.yaml
  T2: ros2 run localization localization_node
  T3: ros2 run motion_control motion_controller --ros-args --params-file .../robot.yaml
  T4: (可选) ros2 topic echo /motion/result

运行：
  cd ~/robogame_git
  python3 tools/navigation_acceptance.py
  python3 tools/navigation_acceptance.py --rounds 10 --output navigation_results.csv
  python3 tools/navigation_acceptance.py --result-topic /motion/result   # 指定结果话题
"""

import argparse
import csv
import math
import sys

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Pose2D
from nav_msgs.msg import Odometry

DEFAULT_TARGETS = [
    (1.0, 0.0, 0.0),        # 正向运动
    (1.0, 0.5, 0.0),        # 麦轮横移
    (1.0, 0.5, 1.5708),     # 旋转 90°
    (0.2, 0.2, -1.5708),    # 负角度 + 归一化
    (0.0, 0.0, 0.0),        # 返回起点
]

DEFAULT_ROUNDS = 10
GOAL_TIMEOUT_S = 30.0           # 单个目标等待结果的最长时间
PASS_POS_TOL = 0.05            # 5 cm
PASS_YAW_TOL = math.radians(5.0)   # 5°

CSV_FIELDS = [
    "run_id", "round", "target_idx", "target_x", "target_y", "target_yaw",
    "final_x", "final_y", "final_yaw",
    "position_error_m", "yaw_error_rad", "duration_s", "result", "passed",
]


def normalize_angle(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def yaw_error(target_yaw: float, actual_yaw: float) -> float:
    return abs(normalize_angle(target_yaw - actual_yaw))


def quat_to_yaw(x: float, y: float, z: float, w: float) -> float:
    """四元数转偏航角（rad）。"""
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def parse_result(msg) -> str:
    """从结果消息里提取结果字符串。

    兼容 std_msgs/String（取 .data）和自定义消息（取 .result/.code）。
    """
    if isinstance(msg, str):
        return msg
    for attr in ("data", "result", "code"):
        if hasattr(msg, attr):
            return str(getattr(msg, attr))
    return str(msg)


class NavigationAcceptance(Node):
    def __init__(self, targets, rounds, output, timeout, result_topic):
        super().__init__("navigation_acceptance")
        self.targets = targets
        self.rounds = rounds
        self.output = output
        self.timeout = timeout
        self.result_topic = result_topic

        self.goal_pub = self.create_publisher(Pose2D, "/motion/goal", 10)

        # /pose 用最简单的 QoS（深度 10），兼容 RELIABLE+VOLATILE 发布者
        self.pose_sub = self.create_subscription(Odometry, "/pose", self._on_pose, 10)

        self.latest_x = None
        self.latest_y = None
        self.latest_yaw = None
        self.current_result = None
        self._pose_received_count = 0

        self.result_sub = None

    def _on_pose(self, msg: Odometry):
        self.latest_x = float(msg.pose.pose.position.x)
        self.latest_y = float(msg.pose.pose.position.y)
        q = msg.pose.pose.orientation
        self.latest_yaw = quat_to_yaw(q.x, q.y, q.z, q.w)
        self._pose_received_count += 1
        if self._pose_received_count == 1:
            self.get_logger().info(
                f"收到首帧 /pose: x={self.latest_x:.4f} y={self.latest_y:.4f} yaw={self.latest_yaw:.4f}"
            )

    def _on_result(self, msg):
        self.current_result = parse_result(msg)

    def _try_subscribe_result(self, topic: str):
        """尝试订阅结果话题，动态兼容 String 和自定义消息类型。"""
        try:
            from std_msgs.msg import String
            self.result_sub = self.create_subscription(String, topic, self._on_result, 10)
            self.get_logger().info(f"结果话题 {topic} 按 std_msgs/String 订阅")
            return True
        except Exception:
            pass

        try:
            from robogame_interfaces.msg import MotionResult
            self.result_sub = self.create_subscription(MotionResult, topic, self._on_result, 10)
            self.get_logger().info(f"结果话题 {topic} 按 robogame_interfaces/MotionResult 订阅")
            return True
        except Exception:
            pass

        self.get_logger().warn(
            f"无法订阅 {topic}，请用 --result-topic 指定正确话题名，"
            f"或确认消息类型后修改本脚本。"
        )
        return False

    def run(self):
        if not self.result_topic:
            self.get_logger().warn(
                "未指定结果话题。将只记录位姿和误差，result 列为 NO_RESULT_TOPIC。"
            )
        else:
            self._try_subscribe_result(self.result_topic)

        rows = []
        run_id = 0
        total = self.rounds * len(self.targets)
        done = 0
        for round_i in range(1, self.rounds + 1):
            for ti, target in enumerate(self.targets, start=1):
                run_id += 1
                done += 1
                self.get_logger().info(
                    f"[{done}/{total}] round={round_i} target={ti} {target}"
                )
                row = self._run_one(run_id, round_i, ti, target)
                rows.append(row)
                self._write(rows)  # 每跑完一个就落盘，防中途丢失
        self._write(rows)
        self._summary(rows)

    def _run_one(self, run_id, round_i, ti, target):
        tx, ty, tyaw = target

        # 发目标前清掉上一轮残留结果
        self.current_result = None

        # 等一个初始 pose（避免用上轮残留位姿算误差）
        wait_start = self.get_clock().now()
        while self.latest_x is None and rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.1)
            if (self.get_clock().now() - wait_start).nanoseconds / 1e9 > 3.0:
                self.get_logger().warn("3秒内未收到 /pose，用 NaN 占位")
                break

        start = self.get_clock().now()
        goal = Pose2D(x=tx, y=ty, theta=tyaw)
        self.goal_pub.publish(goal)

        # 等待结果或超时
        timed_out = False
        while rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.1)
            elapsed = (self.get_clock().now() - start).nanoseconds / 1e9
            if self.current_result is not None:
                break
            if elapsed > self.timeout:
                timed_out = True
                break

        duration = (self.get_clock().now() - start).nanoseconds / 1e9

        fx = fy = fyaw = float("nan")
        if self.latest_x is not None:
            fx = self.latest_x
            fy = self.latest_y
            fyaw = self.latest_yaw
        else:
            self.get_logger().warn(
                f"本轮未收到任何 /pose 消息（累计收到 {self._pose_received_count} 帧）"
            )

        pos_err = math.hypot(tx - fx, ty - fy) if not math.isnan(fx) else float("inf")
        yaw_err = yaw_error(tyaw, fyaw) if not math.isnan(fyaw) else float("inf")

        if timed_out and self.current_result is None:
            result_str = "TEST_TIMEOUT"
        else:
            result_str = self.current_result or "NO_RESULT_TOPIC"

        passed = (
            result_str == "SUCCESS"
            and pos_err < PASS_POS_TOL
            and yaw_err < PASS_YAW_TOL
        )

        return {
            "run_id": run_id,
            "round": round_i,
            "target_idx": ti,
            "target_x": tx,
            "target_y": ty,
            "target_yaw": tyaw,
            "final_x": round(fx, 4) if not math.isnan(fx) else "",
            "final_y": round(fy, 4) if not math.isnan(fy) else "",
            "final_yaw": round(fyaw, 4) if not math.isnan(fyaw) else "",
            "position_error_m": round(pos_err, 4) if math.isfinite(pos_err) else "",
            "yaw_error_rad": round(yaw_err, 4) if math.isfinite(yaw_err) else "",
            "duration_s": round(duration, 3),
            "result": result_str,
            "passed": "1" if passed else "0",
        }

    def _write(self, rows):
        with open(self.output, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            w.writerows(rows)

    def _summary(self, rows):
        total = len(rows)
        ok = sum(1 for r in rows if r["passed"] == "1")
        success = sum(1 for r in rows if r["result"] == "SUCCESS")
        timeouts = sum(1 for r in rows if r["result"] == "TEST_TIMEOUT")
        self.get_logger().info("=" * 50)
        self.get_logger().info(f"总测试数: {total}")
        self.get_logger().info(f"SUCCESS: {success}   通过(误差达标): {ok}   超时: {timeouts}")
        if total:
            self.get_logger().info(f"通过率: {ok / total * 100:.1f}%")
        self.get_logger().info(f"CSV 已写入: {self.output}")
        self.get_logger().info(
            f"通过标准: SUCCESS 且 位置误差<{PASS_POS_TOL}m 且 角度误差<{math.degrees(PASS_YAW_TOL):.1f}°"
        )


def main():
    parser = argparse.ArgumentParser(description="导航验收记录器")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="每组目标测试轮数")
    parser.add_argument("--output", default="navigation_results.csv", help="CSV 输出路径")
    parser.add_argument("--timeout", type=float, default=GOAL_TIMEOUT_S, help="单个目标超时秒数")
    parser.add_argument("--result-topic", default="/motion/result",
                        help="结果话题名，默认 /motion/result；找不到时先用 ros2 topic list 查")
    args = parser.parse_args()

    rclpy.init()
    node = NavigationAcceptance(
        DEFAULT_TARGETS, args.rounds, args.output, args.timeout, args.result_topic
    )
    try:
        node.run()
    except KeyboardInterrupt:
        node.get_logger().info("被中断，已保存当前结果")
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
