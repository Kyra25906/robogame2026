from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

from .opencv_detector import DetectorConfig
from .standalone import load_config, parse_source


CHANNELS = (
    ("H min", 179),
    ("S min", 255),
    ("V min", 255),
    ("H max", 179),
    ("S max", 255),
    ("V max", 255),
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Interactive HSV range tuner")
    parser.add_argument("--source", default="0", help="camera index, image, or video path")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--color", choices=["orange", "purple"], required=True)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    field = f"{args.color}_hsv"
    values = list(getattr(config, field))
    capture = cv2.VideoCapture(parse_source(args.source))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open source {args.source}")

    window = f"HSV tuner: {args.color}"
    cv2.namedWindow(window)
    for index, (name, maximum) in enumerate(CHANNELS):
        cv2.createTrackbar(name, window, values[index], maximum, lambda _value: None)

    last_frame = None
    try:
        while True:
            ok, frame = capture.read()
            if ok:
                last_frame = frame
            elif last_frame is None:
                break
            else:
                frame = last_frame
            current = [cv2.getTrackbarPos(name, window) for name, _maximum in CHANNELS]
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, tuple(current[:3]), tuple(current[3:]))
            preview = cv2.bitwise_and(frame, frame, mask=mask)
            cv2.putText(
                preview,
                "S: save  Q/ESC: quit",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow(window, preview)
            cv2.imshow("binary mask", mask)
            key = cv2.waitKey(25) & 0xFF
            if key in (27, ord("q")):
                break
            if key == ord("s"):
                setattr(config, field, current)
                config.validate()
                with args.config.open("w", encoding="utf-8") as handle:
                    json.dump(config.to_dict(), handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                print(f"saved {field}={current} to {args.config}")
    finally:
        capture.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

