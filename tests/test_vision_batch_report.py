import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cube_perception.batch_report import (
    evaluate_manifest,
    format_html,
    load_manifest,
    main,
)


def record(frame, timestamp, colors=()):
    return {
        "schema_version": 2,
        "frame": frame,
        "timestamp_s": timestamp,
        "timestamp_kind": "media",
        "source_fps": 10.0,
        "detections": [
            {"color": color, "confidence": 0.9, "distance_m": 0.5}
            for color in colors
        ],
    }


class VisionBatchReportTests(unittest.TestCase):
    def _fixture(self, folder: str, *, failing=False) -> Path:
        root = Path(folder)
        timeline = root / "timeline.jsonl"
        records = [
            record(index, index / 10, ("orange",) if index < 9 else ())
            for index in range(10)
        ]
        records.extend(record(10 + index, 1 + index / 10) for index in range(5))
        timeline.write_text(
            "".join(json.dumps(item) + "\n" for item in records),
            encoding="utf-8",
        )
        manifest = {
            "schema_version": 1,
            "name": "Batch smoke <safe>",
            "defaults": {
                "min_detection_ratio": 0.9,
                "max_unexpected_detection_ratio": 0.0,
            },
            "datasets": [{
                "name": "Synthetic dataset",
                "jsonl": "timeline.jsonl",
                "segments": [
                    {
                        "name": "Orange present",
                        "start_s": 0.0,
                        "end_s": 0.9,
                        "expected": "orange",
                        **({"min_detection_ratio": 1.0} if failing else {}),
                    },
                    {
                        "name": "Empty interval",
                        "start_s": 1.0,
                        "end_s": 1.4,
                        "expected": "absent",
                    },
                ],
            }],
        }
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_manifest_evaluates_present_and_absent_segments(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._fixture(folder)
            result = evaluate_manifest(path, load_manifest(path))

        self.assertTrue(result["passed"])
        self.assertEqual(result["passed_segments"], 2)
        present, absent = result["datasets"][0]["segments"]
        self.assertAlmostEqual(present["correct_ratio"], 0.9)
        self.assertAlmostEqual(present["unexpected_ratio"], 0.0)
        self.assertIsNone(absent["correct_ratio"])
        self.assertAlmostEqual(absent["unexpected_ratio"], 0.0)

    def test_segment_override_can_fail_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._fixture(folder, failing=True)
            result = evaluate_manifest(path, load_manifest(path))

        self.assertFalse(result["passed"])
        self.assertEqual(result["passed_segments"], 1)
        self.assertIn("below", result["datasets"][0]["segments"][0]["message"])

    def test_html_escapes_names_and_contains_segment_table(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._fixture(folder)
            report = format_html(evaluate_manifest(path, load_manifest(path)))

        self.assertIn("<!doctype html>", report)
        self.assertIn("Batch smoke &lt;safe&gt;", report)
        self.assertNotIn("Batch smoke <safe>", report)
        self.assertIn("Orange present", report)
        self.assertIn("2 of 2 segments passed", report)

    def test_cli_writes_passing_report(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._fixture(folder)
            output = Path(folder) / "report.html"

            self.assertEqual(main(["--manifest", str(path), "--output", str(output)]), 0)
            self.assertIn("summary pass", output.read_text(encoding="utf-8"))

    def test_cli_writes_failing_report_and_returns_two(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._fixture(folder, failing=True)
            output = Path(folder) / "report.html"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--manifest", str(path), "--output", str(output)])

            self.assertEqual(code, 2)
            self.assertTrue(output.is_file())
            self.assertIn("summary fail", output.read_text(encoding="utf-8"))
            self.assertIn("FAIL:", stdout.getvalue())

    def test_rejects_unknown_manifest_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "manifest.json"
            path.write_text(
                json.dumps({"schema_version": 2, "name": "bad", "datasets": []}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_manifest(path)

    def test_rejects_invalid_expected_value(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._fixture(folder)
            manifest = load_manifest(path)
            manifest["datasets"][0]["segments"][0]["expected"] = "blue"
            with self.assertRaisesRegex(ValueError, "expected"):
                evaluate_manifest(path, manifest)


if __name__ == "__main__":
    unittest.main()
