from __future__ import annotations

import argparse
import json
import math
import statistics
from html import escape
from pathlib import Path


COLORS = ("orange", "purple")
SUPPORTED_SCHEMA_VERSION = 2
TIMESTAMP_KINDS = ("media", "monotonic")
REPORT_FORMATS = ("markdown", "html", "text")


def load_records(path: Path) -> list[dict]:
    if not path.is_file():
        raise ValueError(f"JSONL file does not exist: {path}")
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}: {exc.msg}") from exc
            if "frame" not in record or "timestamp_s" not in record or "detections" not in record:
                raise ValueError(f"line {line_number} is missing frame, timestamp_s, or detections")
            records.append(record)
    if not records:
        raise ValueError("JSONL file contains no detection records")
    return records


def select_time_range(records: list[dict], start_s: float | None, end_s: float | None) -> list[dict]:
    if start_s is not None and start_s < 0:
        raise ValueError("start_s cannot be negative")
    if end_s is not None and end_s < 0:
        raise ValueError("end_s cannot be negative")
    if start_s is not None and end_s is not None and end_s < start_s:
        raise ValueError("end_s cannot be earlier than start_s")
    selected = [
        record for record in records
        if (start_s is None or float(record["timestamp_s"]) >= start_s)
        and (end_s is None or float(record["timestamp_s"]) <= end_s)
    ]
    if not selected:
        raise ValueError("selected time range contains no frames")
    return selected


def consecutive_runs(frames: list[int]) -> list[tuple[int, int, int]]:
    if not frames:
        return []
    ordered = sorted(set(frames))
    runs = []
    start = previous = ordered[0]
    for frame in ordered[1:]:
        if frame == previous + 1:
            previous = frame
            continue
        runs.append((start, previous, previous - start + 1))
        start = previous = frame
    runs.append((start, previous, previous - start + 1))
    return runs


def infer_media_fps(records: list[dict]) -> float | None:
    timestamps = [float(record["timestamp_s"]) for record in records]
    deltas = [later - earlier for earlier, later in zip(timestamps, timestamps[1:]) if later > earlier]
    if not deltas:
        return None
    median_delta = statistics.median(deltas)
    return 1.0 / median_delta if median_delta > 0 else None


def timeline_metadata(records: list[dict]) -> dict:
    versions = {record.get("schema_version") for record in records}
    if None in versions:
        raise ValueError(
            "legacy JSONL is not supported; regenerate it with the current cube_detector"
        )
    if versions != {SUPPORTED_SCHEMA_VERSION}:
        rendered = ", ".join(str(item) for item in sorted(versions, key=str))
        raise ValueError(f"unsupported or mixed JSONL schema versions: {rendered}")

    if any("timestamp_kind" not in record or "source_fps" not in record for record in records):
        raise ValueError("schema version 2 records require timestamp_kind and source_fps")
    timestamp_kinds = {record["timestamp_kind"] for record in records}
    if len(timestamp_kinds) != 1:
        rendered = ", ".join(sorted(str(item) for item in timestamp_kinds))
        raise ValueError(f"JSONL mixes timestamp kinds: {rendered}")
    timestamp_kind = next(iter(timestamp_kinds))
    if timestamp_kind not in TIMESTAMP_KINDS:
        raise ValueError(f"unsupported timestamp_kind: {timestamp_kind}")

    source_values = [record["source_fps"] for record in records]
    if all(value is None for value in source_values):
        source_fps = None
    elif any(value is None for value in source_values):
        raise ValueError("JSONL mixes known and unknown source_fps values")
    else:
        numeric_values = [float(value) for value in source_values]
        if any(not math.isfinite(value) or value <= 0 for value in numeric_values):
            raise ValueError("source_fps must be positive and finite, or null")
        if max(numeric_values) - min(numeric_values) > 1e-3:
            raise ValueError("JSONL contains inconsistent source_fps values")
        source_fps = statistics.median(numeric_values)

    return {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "timestamp_kind": timestamp_kind,
        "source_fps": source_fps,
    }


def _range(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "median": statistics.median(values),
        "minimum": min(values),
        "maximum": max(values),
    }


def resolve_format(output: Path | None, requested_format: str = "auto") -> str:
    if requested_format != "auto":
        if requested_format not in REPORT_FORMATS:
            raise ValueError(
                f"unsupported report format: {requested_format}; "
                f"choose auto, {', '.join(REPORT_FORMATS)}"
            )
        return requested_format
    if output is None:
        return "text"
    suffix = output.suffix.lower()
    extension_formats = {
        ".md": "markdown",
        ".markdown": "markdown",
        ".html": "html",
        ".htm": "html",
        ".txt": "text",
    }
    if suffix not in extension_formats:
        raise ValueError(
            f"cannot infer report format from extension {suffix or '(none)'}; "
            "use --format markdown, html, or text"
        )
    return extension_formats[suffix]


def evaluate_acceptance(
    summary: dict,
    expected_color: str | None,
    min_detection_ratio: float | None,
) -> dict:
    if min_detection_ratio is not None and expected_color is None:
        raise ValueError("--min-detection-ratio requires --expected-color")
    if min_detection_ratio is not None and not 0.0 <= min_detection_ratio <= 1.0:
        raise ValueError("min_detection_ratio must be between 0 and 1")
    if expected_color is not None and expected_color not in COLORS:
        raise ValueError(f"unsupported expected color: {expected_color}")
    if min_detection_ratio is None:
        return {
            "status": "informational",
            "color": expected_color,
            "actual_ratio": None,
            "required_ratio": None,
            "message": "No acceptance threshold was requested.",
        }

    actual_ratio = summary["colors"][expected_color]["detection_ratio"]
    passed = actual_ratio >= min_detection_ratio
    comparison = ">=" if passed else "<"
    return {
        "status": "pass" if passed else "fail",
        "color": expected_color,
        "actual_ratio": actual_ratio,
        "required_ratio": min_detection_ratio,
        "message": (
            f"{expected_color} detection ratio {actual_ratio:.1%} "
            f"{comparison} {min_detection_ratio:.1%}"
        ),
    }


def summarize(records: list[dict]) -> dict:
    total_frames = len(records)
    timeline = timeline_metadata(records)
    observed_rate_hz = infer_media_fps(records)
    run_rate_hz = timeline["source_fps"] or observed_rate_hz
    first_timestamp = float(records[0]["timestamp_s"])
    last_timestamp = float(records[-1]["timestamp_s"])
    summary = {
        "total_frames": total_frames,
        "first_timestamp_s": first_timestamp,
        "last_timestamp_s": last_timestamp,
        "duration_s": max(0.0, last_timestamp - first_timestamp),
        "timeline": timeline,
        "observed_rate_hz": observed_rate_hz,
        "frames_with_any_detection": sum(bool(record["detections"]) for record in records),
        "colors": {},
    }
    for color in COLORS:
        frames = []
        confidences = []
        distances = []
        for record in records:
            matching = [item for item in record["detections"] if item.get("color") == color]
            if matching:
                frames.append(int(record["frame"]))
                confidences.extend(float(item["confidence"]) for item in matching)
                distances.extend(float(item["distance_m"]) for item in matching)
        runs = consecutive_runs(frames)
        longest_run = max((run[2] for run in runs), default=0)
        summary["colors"][color] = {
            "detected_frames": len(frames),
            "detection_ratio": len(frames) / total_frames,
            "run_count": len(runs),
            "longest_run_frames": longest_run,
            "longest_run_s": longest_run / run_rate_hz if run_rate_hz else None,
            "confidence": _range(confidences),
            "distance_m": _range(distances),
        }
    return summary


def _number(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def format_markdown(summary: dict, source: Path, acceptance: dict | None = None) -> str:
    if acceptance is None:
        acceptance = evaluate_acceptance(summary, None, None)
    timeline = summary["timeline"]
    timeline_label = "media" if timeline["timestamp_kind"] == "media" else "live monotonic"
    fps_label = "Source FPS" if timeline["timestamp_kind"] == "media" else "Reported camera FPS"
    lines = [
        "# Vision acceptance report",
        "",
        f"- Source: `{source}`",
        f"- Status: **{acceptance['status'].upper()}**",
        f"- Result: {acceptance['message']}",
        f"- Frames: {summary['total_frames']}",
        f"- Time range: {_number(summary['first_timestamp_s'], 3)}–{_number(summary['last_timestamp_s'], 3)} s",
        f"- Duration: {_number(summary['duration_s'], 3)} s",
        f"- Timeline: {timeline_label}",
        f"- {fps_label}: {_number(timeline['source_fps'])}",
        f"- Observed record rate: {_number(summary['observed_rate_hz'])} Hz",
        f"- Frames with any confirmed detection: {summary['frames_with_any_detection']}",
        "",
        "| Color | Detected frames | Detection ratio | Longest run | Confidence median | Distance median |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for color in COLORS:
        item = summary["colors"][color]
        confidence = item["confidence"]
        distance = item["distance_m"]
        lines.append(
            f"| {color} | {item['detected_frames']} | {item['detection_ratio']:.1%} | "
            f"{item['longest_run_frames']} frames / {_number(item['longest_run_s'])} s | "
            f"{_number(confidence['median'] if confidence else None)} | "
            f"{_number(distance['median'] if distance else None)} m |"
        )
    lines.extend([
        "",
        "> Detection ratio is only a recall-like measure when the selected time range is known to contain the target in every frame.",
        "",
    ])
    return "\n".join(lines)


def _range_text(values: dict | None, unit: str = "") -> str:
    if values is None:
        return "n/a"
    suffix = f" {unit}" if unit else ""
    return (
        f"median {_number(values['median'])}{suffix}, "
        f"min {_number(values['minimum'])}{suffix}, "
        f"max {_number(values['maximum'])}{suffix}"
    )


def format_text(summary: dict, source: Path, acceptance: dict) -> str:
    timeline = summary["timeline"]
    timeline_label = "media" if timeline["timestamp_kind"] == "media" else "live monotonic"
    fps_label = "Source FPS" if timeline["timestamp_kind"] == "media" else "Reported camera FPS"
    lines = [
        "CUBE PERCEPTION ACCEPTANCE REPORT",
        "=================================",
        "",
        f"Source: {source}",
        f"Status: {acceptance['status'].upper()}",
        f"Result: {acceptance['message']}",
        "",
        "Timeline",
        "--------",
        f"Kind: {timeline_label}",
        f"{fps_label}: {_number(timeline['source_fps'])}",
        f"Observed record rate: {_number(summary['observed_rate_hz'])} Hz",
        f"Frames: {summary['total_frames']}",
        f"Time range: {_number(summary['first_timestamp_s'], 3)} - {_number(summary['last_timestamp_s'], 3)} s",
        f"Duration: {_number(summary['duration_s'], 3)} s",
        f"Frames with any confirmed detection: {summary['frames_with_any_detection']}",
        "",
        "Color statistics",
        "----------------",
    ]
    for color in COLORS:
        item = summary["colors"][color]
        lines.extend([
            color,
            f"  Detected frames: {item['detected_frames']}",
            f"  Detection ratio: {item['detection_ratio']:.1%}",
            (
                f"  Longest run: {item['longest_run_frames']} frames / "
                f"{_number(item['longest_run_s'])} s"
            ),
            f"  Confidence: {_range_text(item['confidence'])}",
            f"  Distance: {_range_text(item['distance_m'], 'm')}",
            "",
        ])
    lines.extend([
        "Notes",
        "-----",
        "Detection ratio is only a recall-like measure when the selected time range",
        "is known to contain the target in every frame.",
        "Source FPS and observed record rate are not pure detector processing speed.",
        "",
    ])
    return "\n".join(lines)


def format_html(summary: dict, source: Path, acceptance: dict) -> str:
    timeline = summary["timeline"]
    timeline_label = "media" if timeline["timestamp_kind"] == "media" else "live monotonic"
    fps_label = "Source FPS" if timeline["timestamp_kind"] == "media" else "Reported camera FPS"
    rows = []
    for color in COLORS:
        item = summary["colors"][color]
        rows.append(
            "<tr>"
            f"<th scope=\"row\">{escape(color)}</th>"
            f"<td>{item['detected_frames']}</td>"
            f"<td>{item['detection_ratio']:.1%}</td>"
            f"<td>{item['longest_run_frames']} frames / {_number(item['longest_run_s'])} s</td>"
            f"<td>{escape(_range_text(item['confidence']))}</td>"
            f"<td>{escape(_range_text(item['distance_m'], 'm'))}</td>"
            "</tr>"
        )
    status = escape(acceptance["status"])
    status_title = escape(acceptance["status"].upper())
    status_message = escape(acceptance["message"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cube Perception Acceptance Report</title>
  <style>
    :root {{ color-scheme: light; font-family: "Segoe UI", Arial, sans-serif; }}
    body {{ max-width: 1100px; margin: 0 auto; padding: 32px; color: #1f2937; background: #f6f8fb; }}
    h1, h2 {{ color: #111827; }}
    .source {{ overflow-wrap: anywhere; color: #4b5563; }}
    .status {{ margin: 24px 0; padding: 18px 20px; border-left: 6px solid; border-radius: 8px; background: white; }}
    .status.pass {{ border-color: #15803d; background: #f0fdf4; }}
    .status.fail {{ border-color: #b91c1c; background: #fef2f2; }}
    .status.informational {{ border-color: #0369a1; background: #f0f9ff; }}
    .status strong {{ display: block; margin-bottom: 6px; font-size: 1.2rem; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ padding: 16px; border: 1px solid #dbe2ea; border-radius: 8px; background: white; break-inside: avoid; }}
    .card span {{ display: block; color: #6b7280; font-size: .85rem; }}
    .card strong {{ display: block; margin-top: 5px; font-size: 1.2rem; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ padding: 10px; border: 1px solid #d1d5db; text-align: left; vertical-align: top; }}
    thead th {{ background: #e5e7eb; }}
    .note {{ margin-top: 20px; padding: 14px; border-radius: 8px; background: #fff7ed; }}
    @media print {{
      body {{ max-width: none; padding: 0; background: white; }}
      .status, .card, table {{ box-shadow: none; break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <h1>Cube Perception Acceptance Report</h1>
  <p class="source"><strong>Source:</strong> {escape(str(source))}</p>
  <section class="status {status}" aria-label="Acceptance status">
    <strong>{status_title}</strong>
    <span>{status_message}</span>
  </section>
  <h2>Timeline</h2>
  <section class="cards">
    <div class="card"><span>Timeline</span><strong>{escape(timeline_label)}</strong></div>
    <div class="card"><span>{escape(fps_label)}</span><strong>{_number(timeline['source_fps'])}</strong></div>
    <div class="card"><span>Observed record rate</span><strong>{_number(summary['observed_rate_hz'])} Hz</strong></div>
    <div class="card"><span>Frames</span><strong>{summary['total_frames']}</strong></div>
    <div class="card"><span>Time range</span><strong>{_number(summary['first_timestamp_s'], 3)} - {_number(summary['last_timestamp_s'], 3)} s</strong></div>
    <div class="card"><span>Any confirmed detection</span><strong>{summary['frames_with_any_detection']}</strong></div>
  </section>
  <h2>Color statistics</h2>
  <table>
    <thead>
      <tr><th>Color</th><th>Detected frames</th><th>Detection ratio</th><th>Longest run</th><th>Confidence</th><th>Distance</th></tr>
    </thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <aside class="note">
    <strong>Interpretation:</strong> Detection ratio is only a recall-like measure when the selected time range is known to contain the target in every frame. Source FPS and observed record rate are not pure detector processing speed.
  </aside>
</body>
</html>
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize cube_detector JSONL output")
    parser.add_argument("--jsonl", required=True, type=Path, help="cube_detector JSONL file")
    parser.add_argument("--start-s", type=float, help="first media timestamp to include")
    parser.add_argument("--end-s", type=float, help="last media timestamp to include")
    parser.add_argument("--output", type=Path, help="write report to a file instead of stdout")
    parser.add_argument(
        "--format", choices=("auto",) + REPORT_FORMATS, default="auto",
        help="report format; auto infers it from --output (default: text on stdout)",
    )
    parser.add_argument("--expected-color", choices=COLORS, help="color expected throughout selected range")
    parser.add_argument(
        "--min-detection-ratio", type=float,
        help="return failure if expected color is detected below this fraction (0 to 1)",
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        all_records = load_records(args.jsonl)
        timeline_metadata(all_records)
        records = select_time_range(all_records, args.start_s, args.end_s)
        summary = summarize(records)
        acceptance = evaluate_acceptance(
            summary, args.expected_color, args.min_detection_ratio
        )
        report_format = resolve_format(args.output, args.format)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    renderers = {
        "markdown": format_markdown,
        "html": format_html,
        "text": format_text,
    }
    report = renderers[report_format](summary, args.jsonl, acceptance)
    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report, encoding="utf-8")
        except OSError as exc:
            parser.error(f"cannot write report {args.output}: {exc}")
        print(f"wrote {args.output}")
    else:
        print(report)
    if acceptance["status"] == "fail":
        print(f"FAIL: {acceptance['message']}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
