import json
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from pathlib import Path

from cube_perception.segment_annotator import (
    load_editor_state,
    make_handler,
    parse_byte_range,
    render_editor_page,
    resolve_detector_config,
    save_editor_state,
    validate_segments,
)


class VisionSegmentAnnotatorTests(unittest.TestCase):
    def test_default_detector_config_is_available(self):
        config = resolve_detector_config(None)
        self.assertTrue(config.is_file())
        self.assertEqual(config.name, "vision_default.json")

    def test_page_render_does_not_import_opencv_detector(self):
        state = {
            "manifest_name": "test", "dataset_name": "video",
            "jsonl": "", "video": "", "segments": [],
            "video_ready": False, "jsonl_ready": False,
        }
        with patch.dict("sys.modules", {"cv2": None}):
            page = render_editor_page(state)
        self.assertIn("处理新视频并生成检测时间线", page)

    def test_validate_segments_normalizes_values(self):
        result = validate_segments([
            {"name": " Orange ", "start_s": "1.23456", "end_s": 2, "expected": "orange"}
        ])
        self.assertEqual(result, [{
            "name": "Orange", "start_s": 1.235, "end_s": 2.0, "expected": "orange"
        }])

    def test_validate_segments_rejects_empty_or_reversed_range(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            validate_segments([])
        with self.assertRaisesRegex(ValueError, "later"):
            validate_segments([
                {"name": "bad", "start_s": 2, "end_s": 1, "expected": "absent"}
            ])

    def test_new_manifest_uses_relative_paths_and_utf8(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "输入" / "camera.mp4"
            jsonl_path = root / "输入" / "detections.jsonl"
            manifest = root / "results" / "vision_acceptance.json"
            video.parent.mkdir()
            video.write_bytes(b"video")
            jsonl_path.write_text("{}\n", encoding="utf-8")

            saved = save_editor_state(
                manifest_path=manifest,
                video_path=video,
                jsonl_path=jsonl_path,
                payload={
                    "manifest_name": "现场验收",
                    "dataset_name": "前置相机",
                    "segments": [{
                        "name": "空场景", "start_s": 0, "end_s": 1, "expected": "absent"
                    }],
                },
            )

            self.assertEqual(saved["schema_version"], 1)
            self.assertEqual(saved["datasets"][0]["jsonl"], "../输入/detections.jsonl")
            self.assertIn("现场验收", manifest.read_text(encoding="utf-8"))

    def test_existing_manifest_updates_matching_dataset_and_preserves_others(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "camera.mp4"
            jsonl_path = root / "current.jsonl"
            manifest = root / "vision_acceptance.json"
            video.write_bytes(b"video")
            jsonl_path.write_text("{}\n", encoding="utf-8")
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "name": "old",
                "defaults": {"min_detection_ratio": 0.8, "max_unexpected_detection_ratio": 0.1},
                "datasets": [
                    {"name": "keep", "jsonl": "other.jsonl", "segments": [{"name": "x"}]},
                    {"name": "replace", "jsonl": "current.jsonl", "segments": [{"name": "old"}]},
                ],
            }), encoding="utf-8")

            saved = save_editor_state(
                manifest_path=manifest,
                video_path=video,
                jsonl_path=jsonl_path,
                payload={
                    "manifest_name": "new",
                    "dataset_name": "updated",
                    "segments": [{
                        "name": "purple", "start_s": 1, "end_s": 2, "expected": "purple"
                    }],
                },
            )

            self.assertEqual(len(saved["datasets"]), 2)
            self.assertEqual(saved["datasets"][0]["name"], "keep")
            self.assertEqual(saved["datasets"][1]["name"], "updated")
            self.assertEqual(saved["defaults"]["min_detection_ratio"], 0.8)

    def test_load_state_reopens_existing_matching_segments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "video.mp4"
            jsonl_path = root / "data.jsonl"
            manifest = root / "manifest.json"
            segment = {"name": "orange", "start_s": 0, "end_s": 1, "expected": "orange"}
            manifest.write_text(json.dumps({
                "schema_version": 1, "name": "job", "defaults": {},
                "datasets": [{"name": "camera", "jsonl": "data.jsonl", "segments": [segment]}],
            }), encoding="utf-8")

            state = load_editor_state(
                manifest_path=manifest, video_path=video, jsonl_path=jsonl_path,
                dataset_name="fallback",
            )

            self.assertEqual(state["dataset_name"], "camera")
            self.assertEqual(state["segments"], [segment])

    def test_editor_page_escapes_script_termination_and_has_controls(self):
        page = render_editor_page({
            "manifest_name": "</script><script>alert(1)</script>",
            "dataset_name": "camera", "jsonl": "data.jsonl", "video": "video.mp4",
            "segments": [],
        })
        self.assertNotIn("</script><script>alert(1)</script>", page)
        self.assertIn("<\\/script>", page)
        self.assertIn("把当前播放位置设为开始", page)
        self.assertIn("保存 manifest", page)

    def test_byte_range_supports_browser_video_requests(self):
        self.assertIsNone(parse_byte_range(None, 100))
        self.assertEqual(parse_byte_range("bytes=10-19", 100), (10, 19))
        self.assertEqual(parse_byte_range("bytes=90-", 100), (90, 99))
        self.assertEqual(parse_byte_range("bytes=-10", 100), (90, 99))
        with self.assertRaisesRegex(ValueError, "outside"):
            parse_byte_range("bytes=100-", 100)

    def test_local_http_menu_serves_video_range_and_saves_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / "manifest.json"
            state = load_editor_state(
                manifest_path=manifest, video_path=None, jsonl_path=None,
                dataset_name="camera",
            )
            handler = make_handler(
                manifest_path=manifest, video_path=None, jsonl_path=None,
                initial_state=state,
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(base + "/") as response:
                    self.assertIn("视觉时间段标注菜单", response.read().decode("utf-8"))
                video_upload = urllib.request.Request(
                    base + "/upload/video", data=bytes(range(100)), method="POST",
                    headers={"X-Filename": "camera.mp4", "Content-Type": "application/octet-stream"},
                )
                with urllib.request.urlopen(video_upload) as response:
                    self.assertEqual(json.loads(response.read())["kind"], "video")
                timeline_record = {
                    "schema_version": 2, "frame": 0, "timestamp_s": 0.0,
                    "timestamp_kind": "media", "source_fps": 30.0,
                    "detections": [],
                }
                jsonl_upload = urllib.request.Request(
                    base + "/upload/jsonl",
                    data=(json.dumps(timeline_record) + "\n").encode("utf-8"),
                    method="POST",
                    headers={"X-Filename": "detections.jsonl", "Content-Type": "application/octet-stream"},
                )
                with urllib.request.urlopen(jsonl_upload) as response:
                    self.assertEqual(json.loads(response.read())["kind"], "jsonl")
                request = urllib.request.Request(
                    base + "/video", headers={"Range": "bytes=10-19"}
                )
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.read(), bytes(range(10, 20)))
                body = json.dumps({
                    "manifest_name": "acceptance",
                    "dataset_name": "camera",
                    "segments": [{
                        "name": "empty", "start_s": 0, "end_s": 1,
                        "expected": "absent",
                    }],
                }).encode("utf-8")
                request = urllib.request.Request(
                    base + "/save", data=body, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request) as response:
                    saved = json.loads(response.read().decode("utf-8"))
                self.assertEqual(saved["segment_count"], 1)
                self.assertTrue(manifest.is_file())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_web_flow_processes_new_video_and_generates_html_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / "vision_acceptance.json"

            def fake_detector(source, output):
                self.assertTrue(source.is_file())
                records = [
                    {"schema_version": 2, "frame": frame, "timestamp_s": float(frame),
                     "timestamp_kind": "media", "source_fps": 1.0, "detections": []}
                    for frame in range(2)
                ]
                output.write_text(
                    "".join(json.dumps(item) + "\n" for item in records),
                    encoding="utf-8",
                )

            state = load_editor_state(
                manifest_path=manifest, video_path=None, jsonl_path=None,
                dataset_name="new video",
            )
            handler = make_handler(
                manifest_path=manifest, video_path=None, jsonl_path=None,
                initial_state=state, process_video_callback=fake_detector,
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                upload = urllib.request.Request(
                    base + "/upload/video", data=b"new video", method="POST",
                    headers={"X-Filename": "new.mp4"},
                )
                urllib.request.urlopen(upload).close()
                process = urllib.request.Request(base + "/process", data=b"", method="POST")
                with urllib.request.urlopen(process) as response:
                    processed = json.loads(response.read())
                self.assertEqual(processed["record_count"], 2)

                save_body = json.dumps({
                    "manifest_name": "web closed loop", "dataset_name": "new video",
                    "segments": [{"name": "empty", "start_s": 0, "end_s": 1,
                                  "expected": "absent"}],
                }).encode("utf-8")
                save = urllib.request.Request(
                    base + "/save", data=save_body, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(save).close()

                report = urllib.request.Request(base + "/report", data=b"", method="POST")
                with urllib.request.urlopen(report) as response:
                    report_result = json.loads(response.read())
                self.assertTrue(report_result["passed"])
                with urllib.request.urlopen(base + report_result["url"]) as response:
                    html = response.read().decode("utf-8")
                self.assertIn("web closed loop", html)
                self.assertIn("1 of 1 segments passed", html)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
