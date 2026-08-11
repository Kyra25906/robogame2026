from __future__ import annotations

from dataclasses import dataclass
import math

from robogame_core.models import CubeColor
from robogame_core.perception import DetectionEstimate


@dataclass(frozen=True)
class TemporalFilterConfig:
    """Parameters for confirming and smoothing detections across video frames."""

    confirm_frames: int = 3
    max_missed_frames: int = 3
    match_distance_px: float = 80.0
    smoothing_alpha: float = 0.4

    def validate(self) -> None:
        if self.confirm_frames < 1:
            raise ValueError("confirm_frames must be at least 1")
        if self.max_missed_frames < 0:
            raise ValueError("max_missed_frames cannot be negative")
        if self.match_distance_px <= 0.0:
            raise ValueError("match_distance_px must be positive")
        if not 0.0 < self.smoothing_alpha <= 1.0:
            raise ValueError("smoothing_alpha must be in (0, 1]")


@dataclass
class _Track:
    estimate: DetectionEstimate
    streak: int = 1
    missed: int = 0


def _pixel_distance(first: DetectionEstimate, second: DetectionEstimate) -> float:
    return math.hypot(first.pixel_x - second.pixel_x, first.pixel_y - second.pixel_y)


def _smooth(previous: DetectionEstimate, current: DetectionEstimate, alpha: float) -> DetectionEstimate:
    keep = 1.0 - alpha
    return DetectionEstimate(
        color=current.color,
        confidence=keep * previous.confidence + alpha * current.confidence,
        lateral_m=keep * previous.lateral_m + alpha * current.lateral_m,
        distance_m=keep * previous.distance_m + alpha * current.distance_m,
        yaw_error_rad=keep * previous.yaw_error_rad + alpha * current.yaw_error_rad,
        pixel_x=keep * previous.pixel_x + alpha * current.pixel_x,
        pixel_y=keep * previous.pixel_y + alpha * current.pixel_y,
    )


class TemporalDetectionFilter:
    """Confirm one best target per color before exposing it to robot control.

    A detection must appear near its previous position for ``confirm_frames``
    consecutive frames. Missing frames never produce stale output. A short miss
    keeps only the last position for matching; confirmation must start again.
    """

    def __init__(self, config: TemporalFilterConfig | None = None) -> None:
        self.config = config or TemporalFilterConfig()
        self.config.validate()
        self._tracks: dict[CubeColor, _Track] = {}
        self._ambiguous: set[CubeColor] = set()

    def reset(self) -> None:
        self._tracks.clear()
        self._ambiguous.clear()

    def update(self, detections: list[DetectionEstimate]) -> list[DetectionEstimate]:
        confirmed: list[DetectionEstimate] = []
        for color in CubeColor:
            candidates = [item for item in detections if item.color is color]
            track = self._tracks.get(color)

            if not candidates:
                self._ambiguous.discard(color)
                if track is not None:
                    track.missed += 1
                    track.streak = 0
                    if track.missed > self.config.max_missed_frames:
                        del self._tracks[color]
                continue

            candidate = self._choose_candidate(candidates, track)
            if candidate is None:
                self._ambiguous.add(color)
                if track is not None:
                    track.missed += 1
                    track.streak = 0
                    if track.missed > self.config.max_missed_frames:
                        del self._tracks[color]
                continue

            self._ambiguous.discard(color)
            matched = track is not None and _pixel_distance(track.estimate, candidate) <= self.config.match_distance_px
            if not matched:
                track = _Track(candidate)
                self._tracks[color] = track
            else:
                track.estimate = _smooth(track.estimate, candidate, self.config.smoothing_alpha)
                track.streak += 1
                track.missed = 0

            if track.streak >= self.config.confirm_frames:
                confirmed.append(track.estimate)

        confirmed.sort(key=lambda item: (-item.confidence, item.distance_m))
        return confirmed

    def _choose_candidate(
        self, candidates: list[DetectionEstimate], track: _Track | None
    ) -> DetectionEstimate | None:
        if track is not None:
            nearby = [
                item
                for item in candidates
                if _pixel_distance(track.estimate, item) <= self.config.match_distance_px
            ]
            if len(nearby) == 1:
                return nearby[0]
            if len(nearby) > 1:
                return None
        return candidates[0] if len(candidates) == 1 else None

    def debug_state(self) -> dict[str, dict[str, int | bool]]:
        return {
            color.value: {
                "streak": self._tracks[color].streak if color in self._tracks else 0,
                "missed": self._tracks[color].missed if color in self._tracks else 0,
                "confirmed": (
                    color in self._tracks
                    and self._tracks[color].streak >= self.config.confirm_frames
                    and color not in self._ambiguous
                ),
                "ambiguous": color in self._ambiguous,
            }
            for color in self._tracks.keys() | self._ambiguous
        }
