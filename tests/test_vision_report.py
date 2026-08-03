import json
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from cube_perception.report import (
    consecutive_runs,
    evaluate_acceptance,
    format_html,
    format_markdown,
    format_text,
    load_records,
    main,
    select_time_range,
    summarize,
    timeline_metadata,
    resolve_format,
)


def record(frame, timestamp, detections, timestamp_kind="media", source_fps=10.0):
    return {
        "schema_version": 2,
        "frame": frame,
        "timestamp_s": timestamp,
        "timestamp_kind": timestamp_kind,
        "source_fps": source_fps,
        "detections": detections,
    }


def detection(color, confidence=0.8, distance=0.5):
    return {"color": color, "confidence": confidence, "distance_m": distance}


class VisionReportTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            record(0, 0.0, [detection("orange", 0.7, 1.0)]),
            record(1, 0.1, [detection("orange", 0.8, 0.9)]),
            record(2, 0.2, [detection("purple", 0.9, 0.8)]),
            record(3, 0.3, [detection("orange", 0.9, 0.7)]),
            record(4, 0.4, []),
        ]

    def test_consecutive_runs_groups_adjacent_frames(self):
        self.assertEqual(consecutive_runs([1, 2, 4, 5, 6]), [(1, 2, 2), (4, 6, 3)])

    def test_summary_reports_color_ratios_and_longest_run(self):
        summary = summarize(self.records)

        self.assertEqual(summary["total_frames"], 5)
        self.assertEqual(summary["timeline"]["timestamp_kind"], "media")
        self.assertAlmostEqual(summary["timeline"]["source_fps"], 10.0)
        self.assertAlmostEqual(summary["observed_rate_hz"], 10.0)
        self.assertEqual(summary["frames_with_any_detection"], 4)
        self.assertEqual(summary["colors"]["orange"]["detected_frames"], 3)
        self.assertAlmostEqual(summary["colors"]["orange"]["detection_ratio"], 0.6)
        self.assertEqual(summary["colors"]["orange"]["longest_run_frames"], 2)
        self.assertAlmostEqual(summary["colors"]["orange"]["confidence"]["median"], 0.8)

    def test_time_range_is_inclusive(self):
        selected = select_time_range(self.records, 0.1, 0.3)
        self.assertEqual([item["frame"] for item in selected], [1, 2, 3])

    def test_markdown_contains_summary_table(self):
        report = format_markdown(summarize(self.records), Path("detections.jsonl"))
        self.assertIn("# Vision acceptance report", report)
        self.assertIn("Timeline: media", report)
        self.assertIn("Source FPS: 10.00", report)
        self.assertIn("| orange | 3 | 60.0%", report)
        self.assertIn("| purple | 1 | 20.0%", report)

    def test_load_records_rejects_missing_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.jsonl"
            path.write_text(json.dumps({"frame": 0}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing"):
                load_records(path)

    def test_load_records_reports_missing_file_clearly(self):
        missing = Path("definitely_missing_vision_results.jsonl")
        with self.assertRaisesRegex(ValueError, "JSONL file does not exist"):
            load_records(missing)

    def test_legacy_records_are_rejected(self):
        legacy = [{"frame": 0, "timestamp_s": 0.0, "detections": []}]
        with self.assertRaisesRegex(ValueError, "legacy JSONL is not supported"):
            timeline_metadata(legacy)

    def test_live_records_use_monotonic_timeline(self):
        live = [record(0, 0.0, [], timestamp_kind="monotonic", source_fps=30.0)]
        summary = summarize(live)
        report = format_markdown(summary, Path("live.jsonl"))

        self.assertEqual(summary["timeline"]["timestamp_kind"], "monotonic")
        self.assertIn("Timeline: live monotonic", report)
        self.assertIn("Reported camera FPS: 30.00", report)

    def test_mixed_timestamp_kinds_are_rejected(self):
        mixed = [
            record(0, 0.0, [], timestamp_kind="media"),
            record(1, 0.1, [], timestamp_kind="monotonic"),
        ]
        with self.assertRaisesRegex(ValueError, "mixes timestamp kinds"):
            timeline_metadata(mixed)

    def test_inconsistent_source_fps_is_rejected(self):
        inconsistent = [
            record(0, 0.0, [], source_fps=30.0),
            record(1, 0.1, [], source_fps=25.0),
        ]
        with self.assertRaisesRegex(ValueError, "inconsistent source_fps"):
            timeline_metadata(inconsistent)

    def test_report_format_is_inferred_from_output_extension(self):
        cases = {
            "report.md": "markdown",
            "report.markdown": "markdown",
            "report.html": "html",
            "report.htm": "html",
            "report.txt": "text",
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(resolve_format(Path(filename)), expected)
        self.assertEqual(resolve_format(None), "text")

    def test_explicit_report_format_overrides_extension(self):
        self.assertEqual(resolve_format(Path("report.data"), "html"), "html")

    def test_unknown_report_extension_requires_explicit_format(self):
        with self.assertRaisesRegex(ValueError, "cannot infer report format"):
            resolve_format(Path("report.data"))

    def test_acceptance_without_threshold_is_informational(self):
        result = evaluate_acceptance(summarize(self.records), None, None)
        self.assertEqual(result["status"], "informational")

    def test_acceptance_passes_at_or_above_threshold(self):
        result = evaluate_acceptance(summarize(self.records), "orange", 0.60)
        self.assertEqual(result["status"], "pass")
        self.assertAlmostEqual(result["actual_ratio"], 0.60)
        self.assertIn(">= 60.0%", result["message"])

    def test_acceptance_fails_below_threshold(self):
        result = evaluate_acceptance(summarize(self.records), "orange", 0.90)
        self.assertEqual(result["status"], "fail")
        self.assertIn("< 90.0%", result["message"])

    def test_acceptance_threshold_requires_expected_color(self):
        with self.assertRaisesRegex(ValueError, "requires --expected-color"):
            evaluate_acceptance(summarize(self.records), None, 0.90)

    def test_html_report_has_standalone_document_structure(self):
        summary = summarize(self.records)
        acceptance = evaluate_acceptance(summary, None, None)
        report = format_html(summary, Path("detections.jsonl"), acceptance)

        self.assertIn("<!doctype html>", report)
        self.assertIn('<meta charset="utf-8">', report)
        self.assertIn("<table>", report)
        self.assertIn("status informational", report)
        self.assertIn("INFORMATIONAL", report)

    def test_html_report_escapes_dynamic_source_path(self):
        summary = summarize(self.records)
        acceptance = evaluate_acceptance(summary, None, None)
        report = format_html(
            summary,
            Path("results/<script>alert(1)</script>.jsonl"),
            acceptance,
        )

        self.assertNotIn("<script>alert(1)</script>", report)
        self.assertIn("&lt;script&gt;", report)
        self.assertNotIn("<script>", report)

    def test_html_report_shows_pass_status(self):
        summary = summarize(self.records)
        acceptance = evaluate_acceptance(summary, "orange", 0.60)
        report = format_html(summary, Path("detections.jsonl"), acceptance)

        self.assertIn("status pass", report)
        self.assertIn(">PASS<", report)
        self.assertIn("orange detection ratio 60.0% &gt;= 60.0%", report)

    def test_html_report_shows_fail_status(self):
        summary = summarize(self.records)
        acceptance = evaluate_acceptance(summary, "orange", 0.90)
        report = format_html(summary, Path("detections.jsonl"), acceptance)

        self.assertIn("status fail", report)
        self.assertIn(">FAIL<", report)
        self.assertIn("orange detection ratio 60.0% &lt; 90.0%", report)

    def test_text_report_is_readable_without_markup(self):
        summary = summarize(self.records)
        acceptance = evaluate_acceptance(summary, None, None)
        report = format_text(summary, Path("detections.jsonl"), acceptance)

        self.assertIn("CUBE PERCEPTION ACCEPTANCE REPORT", report)
        self.assertIn("Status: INFORMATIONAL", report)
        self.assertIn("orange", report)
        self.assertIn("purple", report)
        self.assertNotIn("<table>", report)
        self.assertNotIn("|---|", report)

    def test_reports_show_na_for_missing_color_statistics(self):
        empty_summary = summarize([record(0, 0.0, [])])
        acceptance = evaluate_acceptance(empty_summary, None, None)

        self.assertIn("n/a", format_html(empty_summary, Path("empty.jsonl"), acceptance))
        self.assertIn("n/a", format_text(empty_summary, Path("empty.jsonl"), acceptance))

    def _write_jsonl(self, folder: str, filename: str = "detections.jsonl") -> Path:
        path = Path(folder) / filename
        path.write_text(
            "".join(json.dumps(item) + "\n" for item in self.records),
            encoding="utf-8",
        )
        return path

    def test_cli_writes_html_text_and_markdown_by_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            jsonl = self._write_jsonl(folder)
            cases = {
                "report.html": "<!doctype html>",
                "report.txt": "CUBE PERCEPTION ACCEPTANCE REPORT",
                "report.md": "# Vision acceptance report",
            }
            for filename, marker in cases.items():
                with self.subTest(filename=filename):
                    output = Path(folder) / filename
                    self.assertEqual(main(["--jsonl", str(jsonl), "--output", str(output)]), 0)
                    self.assertIn(marker, output.read_text(encoding="utf-8"))

    def test_cli_defaults_to_text_on_stdout(self):
        with tempfile.TemporaryDirectory() as folder:
            jsonl = self._write_jsonl(folder)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--jsonl", str(jsonl)])

            self.assertEqual(code, 0)
            self.assertIn("CUBE PERCEPTION ACCEPTANCE REPORT", stdout.getvalue())

    def test_cli_failure_still_saves_report_and_returns_two(self):
        with tempfile.TemporaryDirectory() as folder:
            jsonl = self._write_jsonl(folder)
            output = Path(folder) / "failed.html"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main([
                    "--jsonl", str(jsonl),
                    "--output", str(output),
                    "--expected-color", "orange",
                    "--min-detection-ratio", "0.90",
                ])

            self.assertEqual(code, 2)
            self.assertTrue(output.is_file())
            self.assertIn("status fail", output.read_text(encoding="utf-8"))
            self.assertIn("FAIL:", stdout.getvalue())

    def test_cli_explicit_format_supports_unknown_extension_and_unicode_path(self):
        with tempfile.TemporaryDirectory() as folder:
            jsonl = self._write_jsonl(folder, "检测结果.jsonl")
            output = Path(folder) / "验收报告.data"

            self.assertEqual(main([
                "--jsonl", str(jsonl), "--output", str(output), "--format", "html"
            ]), 0)
            self.assertIn("<!doctype html>", output.read_text(encoding="utf-8"))

    def test_cli_unknown_extension_without_format_is_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            jsonl = self._write_jsonl(folder)
            with self.assertRaises(SystemExit) as caught:
                main(["--jsonl", str(jsonl), "--output", str(Path(folder) / "report.data")])
            self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
