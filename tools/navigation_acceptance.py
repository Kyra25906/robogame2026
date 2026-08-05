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
"""

import argparse
import csv
import math
import sys

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from geometry_msgs.msg import Pose2D

# /motion/result 消息类型：按 robogame_interfaces 实际定义调整。
# 若是自定义消息（带 result 字段），替换导入并在 parse_result 里取对应字段。
from std_msgs.msg import String as ResultMsg

DEFAULT_TARGETS = [
    (1.0, 0.0, 0.0),        # 正向运动
    (1.0, 0.5, 0.0),        # 麦轮横移
    (1.0, 0.5, 1.5708),     # 旋转 90°
    (0.2, 0.2, -1.5708),    # 负角度 + 归一化
    (0.0, 0.0, 0.0),        # 返回起点
]

DEFAULT_ROUNDS = 10
GOAL_TIMEOUT_S = 30.0           # 单个目标等待结果的最长时间
SETTLE_DELAY_S = 0.5           # 发目标前短暂等待
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


def parse_result(msg) -> str:
    """从 /motion/result 消息里提取结果字符串。

    默认按 std_msgs/String 处理；若 robogame_interfaces 改用自定义消息，
    在这里改成 return str(msg.result) 之类即可。
    """
    if isinstance(msg, str):
        return msg
    for attr in ("data", "result", "code"):
        if hasattr(msg, attr):
            return str(getattr(msg, attr))
    return str(msg)


class NavigationAcceptance(Node):
    def __init__(self, targets, rounds, output, timeout):
        super().__init__("navigation_acceptance")
        self.targets = targets
        self.rounds = rounds
        self.output = output
        self.timeout = timeout

        self.goal_pub = self.create_publisher(Pose2D, "/motion/goal", 10)

        # /pose 若是 nav_msgs/Odometry，把消息类型换成 Odometry 并在 _on_pose 提取
        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )
        self.pose_sub = self.create_subscription(Pose2D, "/pose", self._on_pose, qos)
        self.result_sub = self.create_subscription(
            ResultMsg, "/motion/result", self._on_result, 10
        )

        self.latest_pose = None
        self.current_result = None

    def _on_pose(self, msg):
        self.latest_pose = msg

    def _on_result(self, msg):
        self.current_result = parse_result(msg)

    def run(self):
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
        while self.latest_pose is None and rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.05)
            if (self.get_clock().now() - wait_start).nanoseconds / 1e9 > 2.0:
                self.get_logger().warn("未收到 /pose，用 NaN 占位")
                break

        start = self.get_clock().now()
        goal = Pose2D(x=tx, y=ty, theta=tyaw)
        self.goal_pub.publish(goal)

        # 等待 /motion/result 或超时
        timed_out = False
        while rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.05)
            elapsed = (self.get_clock().now() - start).nanoseconds / 1e9
            if self.current_result is not None:
                break
            if elapsed > self.timeout:
                timed_out = True
                break

        duration = (self.get_clock().now() - start).nanoseconds / 1e9

        fx = fy = fyaw = float("nan")
        if self.latest_pose is not None:
            fx = float(self.latest_pose.x)
            fy = float(self.latest_pose.y)
            fyaw = float(self.latest_pose.theta)

        pos_err = math.hypot(tx - fx, ty - fy) if not math.isnan(fx) else float("inf")
        yaw_err = yaw_error(tyaw, fyaw) if not math.isnan(fyaw) else float("inf")

        if timed_out and self.current_result is None:
            result_str = "TEST_TIMEOUT"
        else:
            result_str = self.current_result or "UNKNOWN"

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
    args = parser.parse_args()

    rclpy.init()
    node = NavigationAcceptance(DEFAULT_TARGETS, args.rounds, args.output, args.timeout)
    try:
        node.run()
    except KeyboardInterrupt:
        node.get_logger().info("被中断，已保存当前结果")
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
