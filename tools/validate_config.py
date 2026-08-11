#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from robogame_core.config_validation import validate_config_bundle


def _load_yaml(path: Path):
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit(
            "PyYAML is required to read ROS2 config files. On Ubuntu install "
            "python3-yaml; on Windows use the same Python environment and install PyYAML."
        ) from exc
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must contain a YAML mapping")
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate RoboGame config layers.")
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("ros2_ws/src/robogame_bringup/config"),
    )
    args = parser.parse_args(argv)
    root = args.config_dir
    configs = {
        name: _load_yaml(root / name)
        for name in (
            "robot.yaml", "robot_mock.yaml", "robot_field.yaml",
            "single_cube.yaml", "hardware.yaml",
        )
    }
    issues = validate_config_bundle(
        common=configs["robot.yaml"],
        mock=configs["robot_mock.yaml"],
        field=configs["robot_field.yaml"],
        single_cube=configs["single_cube.yaml"],
        legacy_hardware=configs["hardware.yaml"],
    )
    for issue in issues:
        print(f"[{issue.level}] {issue.path}: {issue.message}")
    errors = sum(issue.level == "ERROR" for issue in issues)
    warnings = sum(issue.level == "WARNING" for issue in issues)
    if errors:
        print(f"CONFIG FAIL: errors={errors} warnings={warnings}")
        return 1
    print(f"CONFIG PASS: errors=0 warnings={warnings}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
