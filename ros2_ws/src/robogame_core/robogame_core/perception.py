from __future__ import annotations

from dataclasses import dataclass

from .models import CubeColor


@dataclass(frozen=True)
class HsvRange:
    h_min: int
    s_min: int
    v_min: int
    h_max: int
    s_max: int
    v_max: int


@dataclass(frozen=True)
class Candidate:
    color: CubeColor
    center_x: float
    center_y: float
    width_px: float
    height_px: float
    area_px: float
    contour_area_px: float


@dataclass(frozen=True)
class DetectionEstimate:
    color: CubeColor
    confidence: float
    lateral_m: float
    distance_m: float
    yaw_error_rad: float
    pixel_x: float
    pixel_y: float


def candidate_confidence(candidate: Candidate, min_area: float, max_aspect_error: float) -> float:
    if candidate.area_px <= 0 or candidate.contour_area_px < min_area:
        return 0.0
    aspect = candidate.width_px / max(candidate.height_px, 1e-6)
    aspect_score = max(0.0, 1.0 - abs(aspect - 1.0) / max_aspect_error)
    rectangularity = min(1.0, candidate.contour_area_px / candidate.area_px)
    area_score = min(1.0, candidate.contour_area_px / (4.0 * min_area))
    return 0.45 * rectangularity + 0.35 * aspect_score + 0.20 * area_score


def estimate_from_candidate(
    candidate: Candidate,
    image_width: int,
    focal_px: float,
    cube_size_m: float = 0.1,
    min_area: float = 400.0,
    max_aspect_error: float = 0.8,
) -> DetectionEstimate | None:
    confidence = candidate_confidence(candidate, min_area, max_aspect_error)
    if confidence <= 0.0 or candidate.width_px <= 1.0 or focal_px <= 0.0:
        return None
    distance = cube_size_m * focal_px / candidate.width_px
    pixel_error = candidate.center_x - image_width / 2.0
    lateral = pixel_error * distance / focal_px
    yaw_error = pixel_error / focal_px
    return DetectionEstimate(
        candidate.color,
        confidence,
        lateral,
        distance,
        yaw_error,
        candidate.center_x,
        candidate.center_y,
    )


def select_target(detections: list[DetectionEstimate], color: CubeColor) -> DetectionEstimate | None:
    candidates = [d for d in detections if d.color is color]
    if not candidates:
        return None
    return max(candidates, key=lambda d: (d.confidence, -d.distance_m))

