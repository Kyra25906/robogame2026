import unittest

import cv2
import numpy as np

from cube_perception.opencv_detector import CubeDetector, DetectorConfig
from robogame_core.models import CubeColor


class OpenCvDetectorTests(unittest.TestCase):
    def test_synthetic_orange_and_purple_are_detected(self):
        # Build the scene in HSV so the test colors are known to be inside the
        # default orange and purple thresholds, independent of display color.
        hsv = np.zeros((240, 320, 3), dtype=np.uint8)
        cv2.rectangle(hsv, (40, 60), (120, 140), (15, 220, 220), -1)
        cv2.rectangle(hsv, (190, 70), (280, 160), (145, 200, 200), -1)
        frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

        detections, masks = CubeDetector().detect(frame)

        self.assertEqual({item.color for item in detections}, {CubeColor.ORANGE, CubeColor.PURPLE})
        self.assertGreater(int(np.count_nonzero(masks[CubeColor.ORANGE])), 0)
        self.assertGreater(int(np.count_nonzero(masks[CubeColor.PURPLE])), 0)
        self.assertTrue(all(item.confidence >= 0.45 for item in detections))

    def test_roi_rejects_colored_object_outside_allowed_region(self):
        hsv = np.zeros((240, 320, 3), dtype=np.uint8)
        cv2.rectangle(hsv, (40, 20), (120, 90), (15, 220, 220), -1)
        cv2.rectangle(hsv, (180, 150), (270, 230), (145, 200, 200), -1)
        frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        config = DetectorConfig(roi_y_min_ratio=0.5)

        detections, masks = CubeDetector(config).detect(frame)

        self.assertEqual([item.color for item in detections], [CubeColor.PURPLE])
        self.assertEqual(int(np.count_nonzero(masks[CubeColor.ORANGE])), 0)

    def test_thin_colored_strip_is_rejected_by_shape(self):
        hsv = np.zeros((240, 320, 3), dtype=np.uint8)
        cv2.rectangle(hsv, (30, 100), (290, 118), (15, 220, 220), -1)
        frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        config = DetectorConfig(max_rotated_aspect_ratio=2.0)

        detections, _masks = CubeDetector(config).detect(frame)

        self.assertEqual(detections, [])

    def test_rotated_square_is_accepted(self):
        hsv = np.zeros((240, 320, 3), dtype=np.uint8)
        box = cv2.boxPoints(((160, 120), (80, 80), 32)).astype(np.int32)
        cv2.fillConvexPoly(hsv, box, (145, 200, 200))
        frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

        detections, _masks = CubeDetector().detect(frame)

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].color, CubeColor.PURPLE)

    def test_object_clipped_by_image_border_is_rejected(self):
        hsv = np.zeros((240, 320, 3), dtype=np.uint8)
        cv2.rectangle(hsv, (0, 70), (80, 160), (15, 220, 220), -1)
        frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        detector = CubeDetector()

        detections, _masks = detector.detect(frame)

        self.assertEqual(detections, [])
        self.assertEqual(detector.last_debug[CubeColor.ORANGE.value]["clipped"], 1)

    def test_object_near_image_border_is_still_accepted(self):
        hsv = np.zeros((240, 320, 3), dtype=np.uint8)
        cv2.rectangle(hsv, (6, 70), (86, 160), (15, 220, 220), -1)
        frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        detector = CubeDetector()

        detections, _masks = detector.detect(frame)

        self.assertEqual(len(detections), 1)
        self.assertEqual(detector.last_debug[CubeColor.ORANGE.value]["clipped"], 0)

    def test_complete_candidate_uses_short_side_for_distance(self):
        hsv = np.zeros((240, 320, 3), dtype=np.uint8)
        cv2.rectangle(hsv, (100, 60), (180, 160), (15, 220, 220), -1)
        frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        config = DetectorConfig(focal_px=800.0, cube_size_m=0.1)

        detections, _masks = CubeDetector(config).detect(frame)

        self.assertEqual(len(detections), 1)
        self.assertAlmostEqual(detections[0].distance_m, 1.0, places=2)


    def test_candidate_beyond_max_working_distance_is_rejected(self):
        hsv = np.zeros((240, 480, 3), dtype=np.uint8)
        cv2.rectangle(hsv, (190, 110), (215, 130), (15, 220, 220), -1)
        frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        config = DetectorConfig(focal_px=400.0, cube_size_m=0.1, max_working_distance_m=1.0)

        detections, _masks = CubeDetector(config).detect(frame)

        self.assertEqual(detections, [])

    def test_candidate_within_max_working_distance_is_accepted(self):
        hsv = np.zeros((240, 480, 3), dtype=np.uint8)
        cv2.rectangle(hsv, (100, 40), (220, 160), (15, 220, 220), -1)
        frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        config = DetectorConfig(focal_px=400.0, cube_size_m=0.1, max_working_distance_m=0.5)

        detections, _masks = CubeDetector(config).detect(frame)

        self.assertEqual(len(detections), 1)

    def test_max_working_distance_none_does_not_filter(self):
        hsv = np.zeros((240, 480, 3), dtype=np.uint8)
        cv2.rectangle(hsv, (190, 110), (215, 130), (15, 220, 220), -1)
        frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        config = DetectorConfig(focal_px=400.0, cube_size_m=0.1, max_working_distance_m=None)

        detections, _masks = CubeDetector(config).detect(frame)

        self.assertEqual(len(detections), 1)


if __name__ == "__main__":
    unittest.main()
