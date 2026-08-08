#!/usr/bin/env python3
"""Single action acceptance entry for GRAB, LIFT, RELEASE, STOP.

Records request, duration, response and RobotStatus snapshots to CSV.
Only connects to mock services until the MCU protocol is frozen.

Usage:
  python3 tools/mechanism_acceptance.py --action GRAB
  python3 tools/mechanism_acceptance.py --action LIFT --height 0.2
  python3 tools/mechanism_acceptance.py --action STOP
  python3 tools/mechanism_acceptance.py --all --repeat 3
  python3 tools/mechanism_acceptance.py --action RELEASE --timeout 8.0 --output /tmp/result.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import rclpy
from rclpy.node import Node
from robogame_core.hardware_readiness import format_mechanism_status_summary
from robogame_interfaces.msg import RobotStatus
from robogame_interfaces.srv import ExecuteMechanism, SetLiftHeight


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionSpec:
    """Describes one mechanism action the acceptance script can invoke."""

    name: str
    service_name: str
    ros_type: type
    defaults: dict[str, Any] = field(default_factory=dict)


# The four actions currently exposed by robot_bridge.
_ACTIONS: list[ActionSpec] = [
    ActionSpec(
        name="GRAB",
        service_name="/gripper/grab",
        ros_type=ExecuteMechanism,
        defaults={"command": "GRAB", "timeout_s": 3.0},
    ),
    ActionSpec(
        name="RELEASE",
        service_name="/gripper/release",
        ros_type=ExecuteMechanism,
        defaults={"command": "RELEASE", "timeout_s": 3.0},
    ),
    ActionSpec(
        name="STOP",
        service_name="/chassis/stop",
        ros_type=ExecuteMechanism,
        defaults={"command": "STOP", "timeout_s": 1.0},
    ),
    ActionSpec(
        name="LIFT",
        service_name="/lift/set_height",
        ros_type=SetLiftHeight,
        defaults={"height_m": 0.2, "timeout_s": 3.0},
    ),
]

_ACTION_BY_NAME: dict[str, ActionSpec] = {a.name: a for a in _ACTIONS}

# RobotStatus fields to flatten into CSV columns.
_STATUS_FIELDS: list[str] = [
    "communication_ok",
    "emergency_stop",
    "physical_start",
    "gripper_closed",
    "cube_present",
    "mechanism_fault",
    "battery_voltage",
    "error_code",
    "calibrating",
    "imu_valid",
    "boot_id",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snapshot(status: RobotStatus | None) -> dict[str, Any]:
    """Extract a plain dict from a RobotStatus message so it can be logged."""
    if status is None:
        return {f: None for f in _STATUS_FIELDS}
    return {
        "communication_ok": status.communication_ok,
        "emergency_stop": status.emergency_stop,
        "physical_start": status.physical_start,
        "gripper_closed": status.gripper_closed,
        "cube_present": status.cube_present,
        "mechanism_fault": status.mechanism_fault,
        "battery_voltage": status.battery_voltage,
        "error_code": status.error_code,
        "calibrating": status.calibrating,
        "imu_valid": status.imu_valid,
        "boot_id": status.boot_id,
    }


def _flatten(prefix: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Prefix keys so 'before' and 'after' snapshots fit in one CSV row."""
    return {f"{prefix}_{k}": v for k, v in snapshot.items()}


def _status_from_row(row: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Rebuild one status snapshot from the flattened CSV row."""
    return {field: row.get(f"{prefix}_{field}") for field in _STATUS_FIELDS}


# ---------------------------------------------------------------------------
# Core tester
# ---------------------------------------------------------------------------


class MechanismTester:
    """Thin wrapper around a temporary ROS2 node that calls one mechanism
    service per invocation and records everything needed for acceptance."""

    def __init__(self, service_wait_timeout_s: float = 5.0) -> None:
        if not rclpy.ok():
            rclpy.init()
        self._node = Node("mechanism_acceptance", allow_undeclared_parameters=True)
        self._status: RobotStatus | None = None
        self._node.create_subscription(
            RobotStatus, "/robot/status", self._on_status, 10
        )
        self._service_wait_timeout = service_wait_timeout_s
        # Give the subscription a moment to receive the first message.
        self._spin_until(
            lambda: self._status is not None,
            timeout_s=min(service_wait_timeout_s, 2.0),
            label="initial RobotStatus",
        )

    # -- status subscription -------------------------------------------------

    def _on_status(self, msg: RobotStatus) -> None:
        self._status = msg

    def capture_status(self) -> dict[str, Any]:
        """Return the freshest RobotStatus as a flat dict."""
        return _snapshot(self._status)

    # -- service helpers -----------------------------------------------------

    def wait_for_service(self, service_name: str, timeout_s: float) -> bool:
        """Block until *service_name* is available, or return False."""
        deadline = time.monotonic() + timeout_s
        cli = None
        while time.monotonic() < deadline:
            # Re-resolve the client each iteration so we pick up newly
            # registered services without restarting the node.
            try:
                srv_type = self._resolve_service_type(service_name)
                cli = self._node.create_client(
                    srv_type,
                    service_name,
                )
            except KeyError:
                time.sleep(0.05)
                continue
            if cli.service_is_ready():
                return True
            time.sleep(0.05)
        return False

    def _resolve_service_type(self, service_name: str) -> type:
        for spec in _ACTIONS:
            if spec.service_name == service_name:
                return spec.ros_type
        raise KeyError(service_name)

    # -- main entry point ----------------------------------------------------

    def execute(
        self, spec: ActionSpec, *, call_timeout_s: float = 5.0, **overrides: Any
    ) -> dict[str, Any]:
        """Run one action end-to-end and return a flat result dict (one CSV row)."""

        request_args = {**spec.defaults, **overrides}
        row: dict[str, Any] = {
            "action": spec.name,
            "service": spec.service_name,
            "request_time_iso": datetime.now().isoformat(timespec="seconds"),
            "request_args": str(request_args),
        }

        # 1. Capture status before the call.
        row.update(_flatten("status_before", self.capture_status()))

        # 2. Wait for the service.
        t_wait_start = time.monotonic()
        ready = self.wait_for_service(
            spec.service_name, timeout_s=self._service_wait_timeout
        )
        row["wait_for_service_s"] = round(time.monotonic() - t_wait_start, 4)

        if not ready:
            row["success"] = False
            row["error_code"] = -1
            row["duration_s"] = row["wait_for_service_s"]
            row["detail"] = "SERVICE_UNAVAILABLE: did you start robot_bridge?"
            row.update(_flatten("status_after", self.capture_status()))
            return row

        # 3. Build request and call.
        cli = self._node.create_client(spec.ros_type, spec.service_name)
        request = spec.ros_type.Request(**request_args)

        t_call_start = time.monotonic()
        future = cli.call_async(request)
        completed = self._spin_until(
            lambda: future.done(),
            timeout_s=call_timeout_s,
            label=f"{spec.name} service call",
        )
        row["duration_s"] = round(time.monotonic() - t_call_start, 4)

        if not completed:
            row["success"] = False
            row["error_code"] = -2
            row["detail"] = (
                f"SERVICE_TIMEOUT: no response within {call_timeout_s:.1f}s"
            )
            future.cancel()
            row.update(_flatten("status_after", self.capture_status()))
            return row

        # 4. Read response.
        response = future.result()
        if response is None:
            row["success"] = False
            row["error_code"] = -3
            row["detail"] = "SERVICE_ERROR: future result is None"
        else:
            row["success"] = response.success
            row["error_code"] = response.error_code
            row["detail"] = response.detail

        row.update(_flatten("status_after", self.capture_status()))
        return row

    # -- internal spin helper ------------------------------------------------

    def _spin_until(self, condition, *, timeout_s: float, label: str) -> bool:
        """Spin until *condition* is truthy or the deadline passes."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            rclpy.spin_once(self._node, timeout_sec=0.02)
            if condition():
                return True
        return False

    def destroy(self) -> None:
        self._node.destroy_node()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_acceptance(args: argparse.Namespace) -> int:
    tester = MechanismTester(service_wait_timeout_s=args.wait_timeout)

    specs: list[ActionSpec]
    if args.all:
        specs = list(_ACTIONS)
    else:
        specs = [_ACTION_BY_NAME[args.action]]

    # Resolve output path.
    output_path: Path | None = None
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    csv_columns = _build_columns()

    # Open the file once and write all rows (including repeats).
    file_handle = output_path.open("a", newline="") if output_path else None
    writer = csv.DictWriter(
        file_handle or sys.stdout, fieldnames=csv_columns, extrasaction="ignore"
    )

    wrote_header = False
    failures = 0
    final_status = tester.capture_status()

    try:
        for spec in specs:
            for trial in range(1, args.repeat + 1):
                overrides: dict[str, Any] = {}
                if spec.name == "LIFT" and args.height is not None:
                    overrides["height_m"] = args.height
                if args.timeout is not None:
                    overrides["timeout_s"] = args.timeout

                row = tester.execute(
                    spec, call_timeout_s=args.call_timeout, **overrides
                )
                row["trial"] = trial
                row["timestamp_iso"] = datetime.now().isoformat(
                    timespec="seconds"
                )

                if not wrote_header:
                    writer.writeheader()
                    wrote_header = True

                writer.writerow(row)
                if file_handle is not None:
                    file_handle.flush()

                if not row["success"]:
                    failures += 1

                final_status = _status_from_row(row, "status_after")

                # Print a one-liner to stderr so the user can watch progress
                # even when CSV goes to a file.
                status_icon = "PASS" if row["success"] else "FAIL"
                print(
                    f"[{status_icon}] {row['action']}  "
                    f"trial={trial}  "
                    f"duration={row['duration_s']}s  "
                    f"error_code={row['error_code']}  "
                    f"detail={row['detail']}  "
                    f"{format_mechanism_status_summary(final_status)}",
                    file=sys.stderr,
                )
    finally:
        if file_handle is not None:
            file_handle.close()
        tester.destroy()

    total = args.repeat * len(specs)
    result = "FAIL" if failures else "PASS"
    print(
        f"[SUMMARY] result={result} actions={total} failures={failures} "
        f"{format_mechanism_status_summary(final_status)}",
        file=sys.stderr,
    )
    return 1 if failures else 0


def _build_columns() -> list[str]:
    cols = [
        "action",
        "service",
        "trial",
        "timestamp_iso",
        "request_time_iso",
        "request_args",
        "wait_for_service_s",
        "duration_s",
        "success",
        "error_code",
        "detail",
    ]
    for prefix in ("status_before", "status_after"):
        for field in _STATUS_FIELDS:
            cols.append(f"{prefix}_{field}")
    return cols


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Single mechanism action acceptance tool."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--action",
        choices=list(_ACTION_BY_NAME.keys()),
        help="Run a single action type.",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Run GRAB, LIFT, RELEASE, and STOP in order.",
    )

    parser.add_argument(
        "--height",
        type=float,
        default=None,
        help="Target height in metres for LIFT (default: 0.2).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Per-service timeout_s override (uses the action default if omitted).",
    )
    parser.add_argument(
        "--call-timeout",
        type=float,
        default=5.0,
        help="Max seconds to wait for a service response after the request (default: 5.0).",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=5.0,
        help="Max seconds to wait for the service to become ready (default: 5.0).",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of times to repeat each action (default: 1).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="CSV file path. Writes to stdout when omitted.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    sys.exit(run_acceptance(args))


if __name__ == "__main__":
    main()
