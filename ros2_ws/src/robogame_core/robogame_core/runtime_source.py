from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


CRITICAL_TOPICS = ("/robot/status", "/wheel_odom", "/imu/data")


class RuntimeSource(str, Enum):
    MOCK = "mock"
    FIELD = "field"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PublisherIdentity:
    node_name: str
    node_namespace: str = "/"


@dataclass(frozen=True)
class SourceGuardResult:
    passed: bool
    issues: tuple[str, ...]


def classify_status_detail(detail: str) -> RuntimeSource:
    if detail == "mock hardware":
        return RuntimeSource.MOCK
    if detail == "decoded MCU V1 STATUS" or detail.startswith("MCU transport "):
        return RuntimeSource.FIELD
    return RuntimeSource.UNKNOWN


def validate_runtime_sources(
    expected_mode: str,
    publishers: dict[str, list[PublisherIdentity]],
    status_details: list[str],
) -> SourceGuardResult:
    if expected_mode not in {"mock", "field"}:
        raise ValueError("expected_mode must be mock or field")
    issues: list[str] = []
    for topic in CRITICAL_TOPICS:
        endpoints = publishers.get(topic, [])
        if len(endpoints) != 1:
            issues.append(f"{topic} must have exactly one publisher, got {len(endpoints)}")
            continue
        endpoint = endpoints[0]
        if endpoint.node_name != "robot_bridge":
            issues.append(
                f"{topic} publisher must be robot_bridge, got "
                f"{endpoint.node_namespace}{endpoint.node_name}"
            )
    if not status_details:
        issues.append("no /robot/status samples received")
    expected_source = RuntimeSource(expected_mode)
    observed = {classify_status_detail(detail) for detail in status_details}
    if RuntimeSource.UNKNOWN in observed:
        issues.append("unknown RobotStatus.detail source marker observed")
    if RuntimeSource.MOCK in observed and RuntimeSource.FIELD in observed:
        issues.append("mixed mock and field RobotStatus sources observed")
    if observed and observed != {expected_source}:
        issues.append(
            f"expected {expected_mode} RobotStatus source, observed "
            f"{','.join(sorted(source.value for source in observed))}"
        )
    return SourceGuardResult(not issues, tuple(issues))
