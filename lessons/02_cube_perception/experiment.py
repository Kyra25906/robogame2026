"""第 2 课：用合成图像探索 cube_perception，不需要 ROS2 或摄像头。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np


DEFAULT_PROJECT = Path(r"C:\Users\dahli\Documents\Codex\2026-07-13\xu")


def load_project(project: Path):
    """把原项目中的两个 Python 包加入搜索路径，再导入真实实现。"""
    source = project / "ros2_ws" / "src"
    for package in ("robogame_core", "cube_perception"):
        package_path = source / package
        if not package_path.is_dir():
            raise FileNotFoundError(f"找不到包目录：{package_path}")
        sys.path.insert(0, str(package_path))

    from cube_perception.opencv_detector import CubeDetector, annotate_frame
    from cube_perception.temporal_filter import TemporalDetectionFilter

    return CubeDetector, annotate_frame, TemporalDetectionFilter


def make_scene() -> np.ndarray:
    """在 HSV 空间画图，保证颜色落在默认阈值内，再转换为相机常用的 BGR。"""
    hsv = np.zeros((240, 320, 3), dtype=np.uint8)
    cv2.rectangle(hsv, (40, 60), (120, 140), (15, 220, 220), -1)   # 橙色
    cv2.rectangle(hsv, (190, 70), (280, 160), (145, 200, 200), -1) # 紫色
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def as_record(detection) -> dict:
    return {
        "color": detection.color.value,
        "confidence": round(detection.confidence, 3),
        "distance_m": round(detection.distance_m, 3),
        "lateral_m": round(detection.lateral_m, 3),
        "pixel": [round(detection.pixel_x, 1), round(detection.pixel_y, 1)],
    }


def write_png(path: Path, image) -> None:
    """兼容 Windows 中文路径；cv2.imwrite 在这类路径上可能静默失败。"""
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError(f"图片编码失败：{path}")
    encoded.tofile(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT, help="原项目 xu 的路径")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "output")
    args = parser.parse_args()

    CubeDetector, annotate_frame, TemporalDetectionFilter = load_project(args.project)
    frame = make_scene()
    detector = CubeDetector()
    temporal_filter = TemporalDetectionFilter()  # 默认 confirm_frames=3

    raw, masks = detector.detect(frame)
    frame_results = []
    for frame_number in range(1, 4):
        confirmed = temporal_filter.update(raw)
        frame_results.append({
            "frame": frame_number,
            "confirmed": [item.color.value for item in confirmed],
            "tracking": temporal_filter.debug_state(),
        })

    args.output.mkdir(parents=True, exist_ok=True)
    write_png(args.output / "scene.png", frame)
    write_png(args.output / "orange_mask.png", masks[next(item.color for item in raw if item.color.value == "orange")])
    write_png(args.output / "purple_mask.png", masks[next(item.color for item in raw if item.color.value == "purple")])
    write_png(args.output / "annotated.png", annotate_frame(frame, raw))

    result = {
        "input": {"size": [320, 240], "objects": ["orange", "purple"]},
        "raw_detections": [as_record(item) for item in raw],
        "detector_debug": detector.last_debug,
        "temporal_confirmation": frame_results,
    }
    (args.output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))

    colors = {item.color.value for item in raw}
    assert colors == {"orange", "purple"}, f"单帧颜色识别异常：{colors}"
    assert frame_results[0]["confirmed"] == []
    assert frame_results[1]["confirmed"] == []
    assert set(frame_results[2]["confirmed"]) == {"orange", "purple"}
    print("\n实验通过：两种颜色均被识别，并在第 3 帧通过连续帧确认。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
