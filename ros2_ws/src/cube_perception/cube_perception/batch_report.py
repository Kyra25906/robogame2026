from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path

from .report import COLORS, load_records, select_time_range, summarize, timeline_metadata


MANIFEST_SCHEMA_VERSION = 1
EXPECTED_VALUES = COLORS + ("absent",)


def _ratio(value: object, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number between 0 and 1") from exc
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return number


def load_manifest(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"manifest does not exist: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid manifest JSON: {exc.msg}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("manifest root must be an object")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported manifest schema_version: {manifest.get('schema_version')}; "
            f"expected {MANIFEST_SCHEMA_VERSION}"
        )
    if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
        raise ValueError("manifest name must be a non-empty string")
    defaults = manifest.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("manifest defaults must be an object")
    defaults["min_detection_ratio"] = _ratio(
        defaults.get("min_detection_ratio", 0.9), "min_detection_ratio"
    )
    defaults["max_unexpected_detection_ratio"] = _ratio(
        defaults.get("max_unexpected_detection_ratio", 0.0),
        "max_unexpected_detection_ratio",
    )
    datasets = manifest.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("manifest datasets must be a non-empty list")
    manifest["defaults"] = defaults
    return manifest


def _segment_threshold(segment: dict, defaults: dict, name: str) -> float:
    return _ratio(segment.get(name, defaults[name]), name)


def evaluate_segment(
    records: list[dict], segment: dict, defaults: dict
) -> dict:
    if not isinstance(segment, dict):
        raise ValueError("each segment must be an object")
    name = segment.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("segment name must be a non-empty string")
    expected = segment.get("expected")
    if expected not in EXPECTED_VALUES:
        raise ValueError(
            f"segment {name!r} expected must be one of: {', '.join(EXPECTED_VALUES)}"
        )
    if "start_s" not in segment or "end_s" not in segment:
        raise ValueError(f"segment {name!r} requires start_s and end_s")
    start_s = float(segment["start_s"])
    end_s = float(segment["end_s"])
    selected = select_time_range(records, start_s, end_s)
    summary = summarize(selected)
    minimum = _segment_threshold(segment, defaults, "min_detection_ratio")
    maximum = _segment_threshold(
        segment, defaults, "max_unexpected_detection_ratio"
    )

    if expected == "absent":
        correct_frames = None
        correct_ratio = None
        unexpected_frames = summary["frames_with_any_detection"]
        longest_run_frames = None
        longest_run_s = None
        passed = unexpected_frames / summary["total_frames"] <= maximum
    else:
        other_color = next(color for color in COLORS if color != expected)
        correct = summary["colors"][expected]
        unexpected = summary["colors"][other_color]
        correct_frames = correct["detected_frames"]
        correct_ratio = correct["detection_ratio"]
        unexpected_frames = unexpected["detected_frames"]
        longest_run_frames = correct["longest_run_frames"]
        longest_run_s = correct["longest_run_s"]
        passed = correct_ratio >= minimum and unexpected["detection_ratio"] <= maximum

    unexpected_ratio = unexpected_frames / summary["total_frames"]
    reasons = []
    if correct_ratio is not None and correct_ratio < minimum:
        reasons.append(
            f"correct detection {correct_ratio:.1%} is below {minimum:.1%}"
        )
    if unexpected_ratio > maximum:
        reasons.append(
            f"unexpected detection {unexpected_ratio:.1%} exceeds {maximum:.1%}"
        )
    return {
        "name": name,
        "expected": expected,
        "start_s": start_s,
        "end_s": end_s,
        "frames": summary["total_frames"],
        "correct_frames": correct_frames,
        "correct_ratio": correct_ratio,
        "unexpected_frames": unexpected_frames,
        "unexpected_ratio": unexpected_ratio,
        "min_detection_ratio": minimum,
        "max_unexpected_detection_ratio": maximum,
        "longest_run_frames": longest_run_frames,
        "longest_run_s": longest_run_s,
        "passed": passed,
        "message": "all segment thresholds passed" if passed else "; ".join(reasons),
    }


def evaluate_manifest(path: Path, manifest: dict) -> dict:
    base = path.parent
    dataset_results = []
    for dataset in manifest["datasets"]:
        if not isinstance(dataset, dict):
            raise ValueError("each dataset must be an object")
        name = dataset.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("dataset name must be a non-empty string")
        jsonl_value = dataset.get("jsonl")
        if not isinstance(jsonl_value, str) or not jsonl_value.strip():
            raise ValueError(f"dataset {name!r} jsonl must be a non-empty string")
        segments = dataset.get("segments")
        if not isinstance(segments, list) or not segments:
            raise ValueError(f"dataset {name!r} segments must be a non-empty list")
        source = (base / jsonl_value).resolve()
        records = load_records(source)
        timeline_metadata(records)
        evaluated = [
            evaluate_segment(records, segment, manifest["defaults"])
            for segment in segments
        ]
        dataset_results.append(
            {
                "name": name,
                "source": source,
                "segments": evaluated,
                "passed": all(item["passed"] for item in evaluated),
            }
        )

    all_segments = [
        segment
        for dataset in dataset_results
        for segment in dataset["segments"]
    ]
    return {
        "name": manifest["name"],
        "manifest": path.resolve(),
        "datasets": dataset_results,
        "segment_count": len(all_segments),
        "passed_segments": sum(item["passed"] for item in all_segments),
        "passed": all(item["passed"] for item in all_segments),
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _duration(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f} s"


def format_html(result: dict) -> str:
    dataset_sections = []
    for dataset in result["datasets"]:
        rows = []
        for segment in dataset["segments"]:
            status = "pass" if segment["passed"] else "fail"
            longest = (
                "n/a"
                if segment["longest_run_frames"] is None
                else f"{segment['longest_run_frames']} frames / "
                f"{_duration(segment['longest_run_s'])}"
            )
            rows.append(
                f'<tr class="{status}">'
                f"<td><strong>{escape(segment['name'])}</strong><br>"
                f"<small>{segment['start_s']:.3f} - {segment['end_s']:.3f} s</small></td>"
                f"<td>{escape(segment['expected'])}</td>"
                f"<td>{segment['frames']}</td>"
                f"<td>{_percent(segment['correct_ratio'])}<br>"
                f"<small>minimum {_percent(segment['min_detection_ratio'])}</small></td>"
                f"<td>{_percent(segment['unexpected_ratio'])}<br>"
                f"<small>maximum {_percent(segment['max_unexpected_detection_ratio'])}</small></td>"
                f"<td>{escape(longest)}</td>"
                f'<td><span class="badge {status}">{status.upper()}</span><br>'
                f"<small>{escape(segment['message'])}</small></td></tr>"
            )
        status = "pass" if dataset["passed"] else "fail"
        dataset_sections.append(
            f'<section class="dataset"><h2>{escape(dataset["name"])} '
            f'<span class="badge {status}">{status.upper()}</span></h2>'
            f'<p class="path">{escape(str(dataset["source"]))}</p>'
            '<div class="table-wrap"><table><thead><tr>'
            '<th>Segment / time</th><th>Expected</th><th>Frames</th>'
            '<th>Correct detection</th><th>Unexpected</th><th>Longest run</th>'
            f'<th>Result</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
        )
    status = "pass" if result["passed"] else "fail"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cube Perception Batch Acceptance Report</title>
  <style>
    :root {{ color-scheme: light; font-family: "Segoe UI", Arial, sans-serif; }}
    body {{ max-width: 1280px; margin: auto; padding: 30px; color: #1f2937; background: #f4f6f8; }}
    h1, h2 {{ color: #111827; }} .path {{ color: #6b7280; overflow-wrap: anywhere; }}
    .summary {{ padding: 20px; border-left: 7px solid; border-radius: 8px; background: white; }}
    .summary.pass {{ border-color: #15803d; background: #f0fdf4; }}
    .summary.fail {{ border-color: #b91c1c; background: #fef2f2; }}
    .dataset {{ margin-top: 24px; padding: 20px; border-radius: 9px; background: white; }}
    .table-wrap {{ overflow-x: auto; }} table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px; border: 1px solid #d1d5db; text-align: left; vertical-align: top; }}
    th {{ background: #e5e7eb; }} tr.fail {{ background: #fff1f2; }}
    .badge {{ display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: .8rem; }}
    .badge.pass {{ color: #166534; background: #dcfce7; }}
    .badge.fail {{ color: #991b1b; background: #fee2e2; }} small {{ color: #6b7280; }}
    .note {{ margin-top: 20px; padding: 15px; border-radius: 8px; background: #fff7ed; }}
    @media print {{ body {{ max-width: none; padding: 0; background: white; }} .dataset, table {{ break-inside: avoid; }} }}
  </style>
</head>
<body>
  <h1>{escape(result['name'])}</h1>
  <p class="path"><strong>Manifest:</strong> {escape(str(result['manifest']))}</p>
  <section class="summary {status}">
    <strong>{status.upper()}</strong>
    <div>{result['passed_segments']} of {result['segment_count']} segments passed across {len(result['datasets'])} datasets.</div>
  </section>
  {''.join(dataset_sections)}
  <aside class="note"><strong>Interpretation:</strong> Present segments measure correct-color and wrong-color detections. Absent segments treat every detection as unexpected. Transition frames should be excluded from annotated segments.</aside>
</body>
</html>
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate multiple annotated cube-perception timeline segments"
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        result = evaluate_manifest(args.manifest, manifest)
        report = format_html(result)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(f"wrote {args.output}")
    if not result["passed"]:
        print(
            f"FAIL: {result['passed_segments']} of "
            f"{result['segment_count']} segments passed"
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
