from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2

from robogame_core.models import CubeColor

from .opencv_detector import CubeDetector, DetectorConfig, annotate_frame
from .temporal_filter import TemporalDetectionFilter, TemporalFilterConfig


def parse_source(value: str):
    return int(value) if value.isdigit() else value


def load_config(path: Path) -> DetectorConfig:
    with path.open("r", encoding="utf-8") as handle:
        return DetectorConfig.from_dict(json.load(handle))


def open_writer(path: Path | None, capture, first_frame):
    if path is None:
        return None
    fps = capture.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 1.0:
        fps = 30.0
    height, width = first_frame.shape[:2]
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"cannot open output video {path}")
    return writer


def detection_record(frame_index: int, timestamp_s: float, detections, tracking=None, filtering=None) -> dict:
    return {
        "frame": frame_index,
        "timestamp_s": round(timestamp_s, 6),
        "detections": [
            {
                "color": detection.color.value,
                "confidence": round(detection.confidence, 5),
                "distance_m": round(detection.distance_m, 5),
                "lateral_m": round(detection.lateral_m, 5),
                "yaw_error_rad": round(detection.yaw_error_rad, 5),
                "pixel_x": round(detection.pixel_x, 2),
                "pixel_y": round(detection.pixel_y, 2),
            }
            for detection in detections
        ],
        "tracking": tracking or {},
        "filtering": filtering or {},
    }


def annotate_tracking(annotated, tracking: dict, confirm_frames: int):
    y = 24
    for color in ("orange", "purple"):
        state = tracking.get(color, {})
        streak = int(state.get("streak", 0))
        confirmed = bool(state.get("confirmed", False))
        label = f"{color}: {'CONFIRMED' if confirmed else f'{streak}/{confirm_frames}'}"
        draw_color = (70, 210, 70) if confirmed else (40, 190, 240)
        cv2.putText(
            annotated, label, (12, y), cv2.FONT_HERSHEY_SIMPLEX,
            0.58, draw_color, 2, cv2.LINE_AA,
        )
        y += 25
    return annotated


def annotate_filtering(annotated, detector: CubeDetector):
    height, width = annotated.shape[:2]
    x0, y0, x1, y1 = detector.roi_bounds(width, height)
    cv2.rectangle(annotated, (x0, y0), (max(x0, x1 - 1), max(y0, y1 - 1)), (255, 220, 60), 2)
    y = max(75, y0 + 22)
    for color in ("orange", "purple"):
        stats = detector.last_debug.get(color, {})
        text = (
            f"{color} contours={stats.get('contours', 0)} "
            f"accepted={stats.get('accepted', 0)}"
        )
        cv2.putText(annotated, text, (max(12, x0 + 8), y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.52, (255, 220, 60), 2, cv2.LINE_AA)
        y += 22
    return annotated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone RoboGame cube detector")
    parser.add_argument("--source", default="0", help="camera index, image, or video path")
    parser.add_argument("--config", required=True, type=Path, help="detector JSON configuration")
    parser.add_argument("--jsonl", type=Path, help="write one JSON record per processed frame")
    parser.add_argument("--output-video", type=Path, help="write annotated MP4 video")
    parser.add_argument("--headless", action="store_true", help="do not open preview windows")
    parser.add_argument("--max-frames", type=int, default=0, help="stop after N frames; 0 means unlimited")
    parser.add_argument("--desired-color", choices=["orange", "purple", "all"], default="all")
    parser.add_argument(
        "--raw-detections", action="store_true",
        help="bypass consecutive-frame confirmation for threshold debugging",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    detector = CubeDetector(config)
    temporal_filter = TemporalDetectionFilter(TemporalFilterConfig(
        confirm_frames=config.confirm_frames,
        max_missed_frames=config.max_missed_frames,
        match_distance_px=config.match_distance_px,
        smoothing_alpha=config.smoothing_alpha,
    ))
    source = parse_source(args.source)
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open source {args.source}")

    json_handle = None
    writer = None
    frame_index = 0
    started = time.monotonic()
    try:
        if args.jsonl:
            args.jsonl.parent.mkdir(parents=True, exist_ok=True)
            json_handle = args.jsonl.open("w", encoding="utf-8")
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            raw_detections, masks = detector.detect(frame)
            detections = raw_detections if args.raw_detections else temporal_filter.update(raw_detections)
            if args.desired_color != "all":
                wanted = CubeColor(args.desired_color)
                detections = [detection for detection in detections if detection.color is wanted]
            annotated = annotate_frame(frame, detections)
            annotated = annotate_filtering(annotated, detector)
            if not args.raw_detections:
                annotated = annotate_tracking(
                    annotated, temporal_filter.debug_state(), config.confirm_frames
                )
            if writer is None:
                if args.output_video:
                    args.output_video.parent.mkdir(parents=True, exist_ok=True)
                writer = open_writer(args.output_video, capture, annotated)
            timestamp = time.monotonic() - started
            if json_handle:
                json.dump(
                    detection_record(
                        frame_index, timestamp, detections,
                        temporal_filter.debug_state(), detector.last_debug,
                    ),
                    json_handle, ensure_ascii=False,
                )
                json_handle.write("\n")
            if writer:
                writer.write(annotated)
            if not args.headless:
                cv2.imshow("RoboGame cube detector", annotated)
                cv2.imshow("orange mask", masks[CubeColor.ORANGE])
                cv2.imshow("purple mask", masks[CubeColor.PURPLE])
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    break
            frame_index += 1
            if args.max_frames and frame_index >= args.max_frames:
                break
    finally:
        capture.release()
        if writer:
            writer.release()
        if json_handle:
            json_handle.close()
        cv2.destroyAllWindows()
    elapsed = max(1e-6, time.monotonic() - started)
    print(f"processed {frame_index} frames, average {frame_index / elapsed:.1f} FPS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
