import unittest
from unittest.mock import Mock, patch

import cv2

from cube_perception.standalone import capture_timestamp_s, open_capture, parse_source


class StandaloneCaptureTests(unittest.TestCase):
    def test_parse_source_preserves_path_and_converts_camera_index(self):
        self.assertEqual(parse_source("0"), 0)
        self.assertEqual(parse_source("12"), 12)
        self.assertEqual(parse_source("video.mp4"), "video.mp4")

    @patch("cube_perception.standalone.cv2.VideoCapture")
    def test_camera_capture_applies_requested_format(self, video_capture):
        capture = Mock()
        video_capture.return_value = capture

        result = open_capture(0, "msmf", width=1280, height=720, fps=30.0)

        video_capture.assert_called_once_with(0, cv2.CAP_MSMF)
        self.assertIs(result, capture)
        capture.set.assert_any_call(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        capture.set.assert_any_call(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        capture.set.assert_any_call(cv2.CAP_PROP_FPS, 30.0)

    @patch("cube_perception.standalone.cv2.VideoCapture")
    def test_file_capture_does_not_apply_camera_format(self, video_capture):
        capture = Mock()
        video_capture.return_value = capture

        open_capture("video.mp4", "any", width=1280, height=720, fps=30.0)

        video_capture.assert_called_once_with("video.mp4")
        capture.set.assert_not_called()

    def test_video_timestamp_uses_media_position(self):
        capture = Mock()
        capture.get.return_value = 1234.0

        self.assertAlmostEqual(capture_timestamp_s(capture, False, 10.0), 1.234)
        capture.get.assert_called_once_with(cv2.CAP_PROP_POS_MSEC)


if __name__ == "__main__":
    unittest.main()
