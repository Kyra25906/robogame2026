from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from robogame_core.models import CubeColor
from robogame_core.perception import Candidate, DetectionEstimate, estimate_from_candidate


@dataclass
class DetectorConfig:
    orange_hsv: list[int] = field(default_factory=lambda: [5, 100, 80, 25, 255, 255])
    purple_hsv: list[int] = field(default_factory=lambda: [125, 60, 50, 165, 255, 255])
    min_area_px: float = 400.0
    max_aspect_error: float = 0.8
    cube_size_m: float = 0.1
    focal_px: float = 700.0
    morph_kernel: int = 5
    min_confidence: float = 0.45
    confirm_frames: int = 3
    max_missed_frames: int = 3
    match_distance_px: float = 80.0
    smoothing_alpha: float = 0.4
    roi_x_min_ratio: float = 0.0
    roi_x_max_ratio: float = 1.0
    roi_y_min_ratio: float = 0.0
    roi_y_max_ratio: float = 1.0
    max_area_ratio: float = 0.35
    min_rectangularity: float = 0.35
    min_solidity: float = 0.50
    min_side_px: float = 12.0
    max_rotated_aspect_ratio: float = 3.2

    @classmethod
    def from_dict(cls, data: dict) -> "DetectorConfig":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown detector settings: {sorted(unknown)}")
        config = cls(**data)
        config.validate()
        return config

    def to_dict(self) -> dict:
        return {
            "orange_hsv": self.orange_hsv,
            "purple_hsv": self.purple_hsv,
            "min_area_px": self.min_area_px,
            "max_aspect_error": self.max_aspect_error,
            "cube_size_m": self.cube_size_m,
            "focal_px": self.focal_px,
            "morph_kernel": self.morph_kernel,
            "min_confidence": self.min_confidence,
            "confirm_frames": self.confirm_frames,
            "max_missed_frames": self.max_missed_frames,
            "match_distance_px": self.match_distance_px,
            "smoothing_alpha": self.smoothing_alpha,
            "roi_x_min_ratio": self.roi_x_min_ratio,
            "roi_x_max_ratio": self.roi_x_max_ratio,
            "roi_y_min_ratio": self.roi_y_min_ratio,
            "roi_y_max_ratio": self.roi_y_max_ratio,
            "max_area_ratio": self.max_area_ratio,
            "min_rectangularity": self.min_rectangularity,
            "min_solidity": self.min_solidity,
            "min_side_px": self.min_side_px,
            "max_rotated_aspect_ratio": self.max_rotated_aspect_ratio,
        }

    def validate(self) -> None:
        for name in ("orange_hsv", "purple_hsv"):
            values = list(getattr(self, name))
            if len(values) != 6:
                raise ValueError(f"{name} must contain six integers")
            if not all(isinstance(value, int) for value in values):
                raise ValueError(f"{name} must contain integers")
            lower, upper = values[:3], values[3:]
            limits = [179, 255, 255]
            for index, limit in enumerate(limits):
                if not 0 <= lower[index] <= upper[index] <= limit:
                    raise ValueError(f"invalid {name} channel {index}: {lower[index]}..{upper[index]}")
        if self.min_area_px <= 0 or self.focal_px <= 0 or self.cube_size_m <= 0:
            raise ValueError("area, focal length and cube size must be positive")
        if self.morph_kernel < 1:
            raise ValueError("morph_kernel must be positive")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be in [0, 1]")
        if self.confirm_frames < 1:
            raise ValueError("confirm_frames must be at least 1")
        if self.max_missed_frames < 0:
            raise ValueError("max_missed_frames cannot be negative")
        if self.match_distance_px <= 0.0:
            raise ValueError("match_distance_px must be positive")
        if not 0.0 < self.smoothing_alpha <= 1.0:
            raise ValueError("smoothing_alpha must be in (0, 1]")
        for low_name, high_name in (
            ("roi_x_min_ratio", "roi_x_max_ratio"),
            ("roi_y_min_ratio", "roi_y_max_ratio"),
        ):
            low, high = getattr(self, low_name), getattr(self, high_name)
            if not 0.0 <= low < high <= 1.0:
                raise ValueError(f"invalid ROI range {low_name}={low}, {high_name}={high}")
        if not 0.0 < self.max_area_ratio <= 1.0:
            raise ValueError("max_area_ratio must be in (0, 1]")
        if not 0.0 <= self.min_rectangularity <= 1.0:
            raise ValueError("min_rectangularity must be in [0, 1]")
        if not 0.0 <= self.min_solidity <= 1.0:
            raise ValueError("min_solidity must be in [0, 1]")
        if self.min_side_px <= 0.0 or self.max_rotated_aspect_ratio < 1.0:
            raise ValueError("min_side_px must be positive and max aspect ratio at least 1")


class CubeDetector:
    def __init__(self, config: DetectorConfig | None = None) -> None:
        self.config = config or DetectorConfig()
        self.config.validate()
        self.last_debug: dict[str, dict[str, int]] = {}

    def roi_bounds(self, image_width: int, image_height: int) -> tuple[int, int, int, int]:
        x0 = int(round(self.config.roi_x_min_ratio * image_width))
        x1 = int(round(self.config.roi_x_max_ratio * image_width))
        y0 = int(round(self.config.roi_y_min_ratio * image_height))
        y1 = int(round(self.config.roi_y_max_ratio * image_height))
        return x0, y0, x1, y1

    def detect(self, frame) -> tuple[list[DetectionEstimate], dict[CubeColor, object]]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        kernel_size = max(1, int(self.config.morph_kernel))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
        estimates: list[DetectionEstimate] = []
        masks: dict[CubeColor, object] = {}
        image_height, image_width = frame.shape[:2]
        image_area = float(image_width * image_height)
        x0, y0, x1, y1 = self.roi_bounds(image_width, image_height)
        roi_mask = cv2.rectangle(
            np.zeros((image_height, image_width), dtype=np.uint8),
            (x0, y0), (max(x0, x1 - 1), max(y0, y1 - 1)), 255, -1,
        )
        self.last_debug = {}

        for color, values in (
            (CubeColor.ORANGE, self.config.orange_hsv),
            (CubeColor.PURPLE, self.config.purple_hsv),
        ):
            lower, upper = tuple(values[:3]), tuple(values[3:])
            mask = cv2.inRange(hsv, lower, upper)
            mask = cv2.bitwise_and(mask, roi_mask)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            mask = cv2.bitwise_and(mask, roi_mask)
            masks[color] = mask
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            stats = {"contours": len(contours), "small": 0, "large": 0, "shape": 0, "accepted": 0}
            for contour in contours:
                contour_area = float(cv2.contourArea(contour))
                if contour_area < self.config.min_area_px:
                    stats["small"] += 1
                    continue
                if contour_area / image_area > self.config.max_area_ratio:
                    stats["large"] += 1
                    continue
                (center_x, center_y), (raw_width, raw_height), _angle = cv2.minAreaRect(contour)
                short_side = min(float(raw_width), float(raw_height))
                long_side = max(float(raw_width), float(raw_height))
                rotated_area = short_side * long_side
                hull_area = float(cv2.contourArea(cv2.convexHull(contour)))
                rectangularity = contour_area / max(rotated_area, 1e-6)
                solidity = contour_area / max(hull_area, 1e-6)
                aspect_ratio = long_side / max(short_side, 1e-6)
                if (
                    short_side < self.config.min_side_px
                    or rectangularity < self.config.min_rectangularity
                    or solidity < self.config.min_solidity
                    or aspect_ratio > self.config.max_rotated_aspect_ratio
                ):
                    stats["shape"] += 1
                    continue
                candidate = Candidate(
                    color=color,
                    center_x=float(center_x),
                    center_y=float(center_y),
                    width_px=long_side,
                    height_px=short_side,
                    area_px=rotated_area,
                    contour_area_px=contour_area,
                )
                estimate = estimate_from_candidate(
                    candidate,
                    image_width=frame.shape[1],
                    focal_px=self.config.focal_px,
                    cube_size_m=self.config.cube_size_m,
                    min_area=self.config.min_area_px,
                    max_aspect_error=self.config.max_aspect_error,
                )
                if estimate and estimate.confidence >= self.config.min_confidence:
                    estimates.append(estimate)
                    stats["accepted"] += 1
                else:
                    stats["shape"] += 1
            self.last_debug[color.value] = stats
        estimates.sort(key=lambda detection: (-detection.confidence, detection.distance_m))
        return estimates, masks


def annotate_frame(frame, detections: list[DetectionEstimate]):
    annotated = frame.copy()
    height, width = annotated.shape[:2]
    cv2.line(annotated, (width // 2, 0), (width // 2, height), (230, 230, 230), 1)
    for detection in detections:
        color = (0, 140, 255) if detection.color is CubeColor.ORANGE else (220, 70, 180)
        center = (int(detection.pixel_x), int(detection.pixel_y))
        radius = max(12, int(0.05 * detection.distance_m ** -1 * 100))
        cv2.circle(annotated, center, radius, color, 2)
        label = (
            f"{detection.color.value} "
            f"c={detection.confidence:.2f} "
            f"d={detection.distance_m:.2f}m "
            f"lat={detection.lateral_m:+.2f}m"
        )
        cv2.putText(
            annotated,
            label,
            (max(0, center[0] - 120), max(20, center[1] - radius - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            color,
            2,
            cv2.LINE_AA,
        )
    return annotated
