"""第 1 课：读取 ROS2 接口合同，并运行不依赖 ROS2 的纯逻辑实验。"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


DEFAULT_PROJECT = Path(r"C:\Users\dahli\Documents\Codex\2026-07-13\xu")


def load_core(project: Path):
    package = project / "ros2_ws" / "src" / "robogame_core"
    if not package.is_dir():
        raise FileNotFoundError(f"找不到核心包：{package}")
    sys.path.insert(0, str(package))
    from robogame_core.models import Cargo, CubeColor, Pose2D
    from robogame_core.navigation import GoToPoseController, OdometryIntegrator

    return Cargo, CubeColor, Pose2D, GoToPoseController, OdometryIntegrator


def read_interfaces(project: Path) -> dict[str, str]:
    root = project / "ros2_ws" / "src" / "robogame_interfaces"
    relative_paths = [
        Path("msg/CargoState.msg"),
        Path("msg/CubeDetection.msg"),
        Path("srv/ExecuteMechanism.srv"),
    ]
    return {
        path.as_posix(): (root / path).read_text(encoding="utf-8").strip()
        for path in relative_paths
    }


def navigation_experiment(Pose2D, GoToPoseController, OdometryIntegrator):
    controller = GoToPoseController()
    odometry = OdometryIntegrator()
    target = Pose2D(1.0, 0.5, 0.0)
    trajectory = []

    for step in range(1, 101):
        command = controller.command(odometry.pose, target)
        pose = odometry.update(command, dt=0.1)
        trajectory.append({
            "step": step,
            "x": pose.x,
            "y": pose.y,
            "yaw": pose.yaw,
            "vx": command.vx,
            "vy": command.vy,
            "wz": command.wz,
        })
        if controller.at_goal(pose, target):
            break

    assert trajectory[-1]["step"] == 27
    assert controller.at_goal(odometry.pose, target)
    return target, odometry.pose, trajectory


def cargo_experiment(Cargo, CubeColor):
    cargo = Cargo()
    operations = []
    for color in (CubeColor.ORANGE, CubeColor.ORANGE, CubeColor.PURPLE):
        allowed = cargo.can_add(color)
        cargo.add(color)
        operations.append({"try": color.value, "allowed": allowed, "total_after": cargo.total})

    full_rejects_orange = not cargo.can_add(CubeColor.ORANGE)
    full_rejects_purple = not cargo.can_add(CubeColor.PURPLE)

    second_cargo = Cargo()
    second_cargo.add(CubeColor.PURPLE)
    second_purple_rejected = not second_cargo.can_add(CubeColor.PURPLE)

    assert (cargo.orange, cargo.purple, cargo.total) == (2, 1, 3)
    assert full_rejects_orange and full_rejects_purple and second_purple_rejected
    return {
        "accepted_sequence": operations,
        "final": {"orange": cargo.orange, "purple": cargo.purple, "total": cargo.total},
        "full_cargo_rejects_more_orange": full_rejects_orange,
        "full_cargo_rejects_more_purple": full_rejects_purple,
        "one_purple_already_rejects_second_purple": second_purple_rejected,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT, help="原项目 xu 的路径")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "output")
    args = parser.parse_args()

    Cargo, CubeColor, Pose2D, GoToPoseController, OdometryIntegrator = load_core(args.project)
    interfaces = read_interfaces(args.project)
    target, final_pose, trajectory = navigation_experiment(
        Pose2D, GoToPoseController, OdometryIntegrator
    )
    cargo = cargo_experiment(Cargo, CubeColor)

    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "trajectory.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=trajectory[0].keys())
        writer.writeheader()
        writer.writerows(trajectory)

    result = {
        "interfaces": interfaces,
        "navigation": {
            "input": {"start": [0.0, 0.0, 0.0], "target": [target.x, target.y, target.yaw], "dt_s": 0.1},
            "processing": "GoToPoseController command -> OdometryIntegrator update",
            "output": {
                "arrived_step": trajectory[-1]["step"],
                "final_pose": [round(final_pose.x, 6), round(final_pose.y, 6), round(final_pose.yaw, 6)],
            },
        },
        "cargo": cargo,
    }
    (args.output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\n实验通过：第 27 步到达目标；Cargo 接受橙、橙、紫，并正确拒绝超容量组合。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
