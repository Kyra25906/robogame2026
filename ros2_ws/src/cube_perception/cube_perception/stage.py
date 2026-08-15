from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PerceptionStage(str, Enum):
    SEARCH = "SEARCH"
    ACQUIRE = "ACQUIRE"
    VERIFY = "VERIFY"


@dataclass(frozen=True)
class StageProfile:
    max_working_distance_m: float


DEFAULT_STAGE_PROFILES = {
    PerceptionStage.SEARCH: StageProfile(max_working_distance_m=1.2),
    PerceptionStage.ACQUIRE: StageProfile(max_working_distance_m=0.8),
    PerceptionStage.VERIFY: StageProfile(max_working_distance_m=0.8),
}


def stage_profile(stage: PerceptionStage) -> StageProfile:
    return DEFAULT_STAGE_PROFILES[stage]


def parse_perception_stage(value: str) -> PerceptionStage:
    normalized = value.strip().upper()
    try:
        return PerceptionStage(normalized)
    except ValueError as exc:
        allowed = ", ".join(stage.value for stage in PerceptionStage)
        raise ValueError(
            f"unsupported perception stage {value!r}; expected one of: {allowed}"
        ) from exc
