from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CubeColor(str, Enum):
    ORANGE = "orange"
    PURPLE = "purple"


class MissionResult(str, Enum):
    SUCCESS = "SUCCESS"
    RUNNING = "RUNNING"
    TIMEOUT = "TIMEOUT"
    TARGET_LOST = "TARGET_LOST"
    COMMUNICATION_ERROR = "COMMUNICATION_ERROR"
    MECHANISM_ERROR = "MECHANISM_ERROR"
    LOCALIZATION_ERROR = "LOCALIZATION_ERROR"
    SAFETY_STOP = "SAFETY_STOP"


@dataclass(frozen=True)
class Pose2D:
    x: float
    y: float
    yaw: float


@dataclass(frozen=True)
class Velocity2D:
    vx: float
    vy: float
    wz: float


@dataclass
class Cargo:
    orange: int = 0
    purple: int = 0
    valid: bool = True

    @property
    def total(self) -> int:
        return self.orange + self.purple

    def can_add(self, color: CubeColor) -> bool:
        if not self.valid or self.total >= 3:
            return False
        return color is not CubeColor.PURPLE or self.purple < 1

    def add(self, color: CubeColor) -> None:
        if not self.can_add(color):
            raise ValueError(f"cargo limit exceeded for {color.value}")
        if color is CubeColor.ORANGE:
            self.orange += 1
        else:
            self.purple += 1

    def remove(self, color: CubeColor) -> None:
        field = "orange" if color is CubeColor.ORANGE else "purple"
        count = getattr(self, field)
        if count <= 0:
            raise ValueError(f"no {color.value} cube onboard")
        setattr(self, field, count - 1)

