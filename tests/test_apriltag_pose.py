"""C2 AprilTag 检测器与位姿解算单测（合成图）。

覆盖（执行队列 C2 要求）：缩放/透视/模糊/遮挡下的 ID 正确率与位姿误差；
异常观测拒绝（离群、远距、大角度）。

测试用 OpenCV 生成标准 AprilTag 36h11 标记，合成到黑底/白底画布上——
这是「合成数据单测」，证明检测器在理想条件下正确；真实相机效果待 B3/B4。
"""
import math
import unittest

import cv2
import numpy as np

from robogame_core.apriltag_pose import (
    MAX_TAG_DISTANCE_M,
    build_observation,
    detect_and_pose,
    detect_tags,
    estimate_tag_pose,
    observation_quality,
)
from robogame_core.apriltag_pose import TAG_DICT, TAG_SIZE_M


def _generate_marker(dictionary, tag_id: int, size_px: int) -> np.ndarray:
    """跨 OpenCV 版本生成标记图（4.6 用 drawMarker，4.13 用 generateImageMarker）。"""
    if hasattr(cv2.aruco, "generateImageMarker"):
        return cv2.aruco.generateImageMarker(dictionary, tag_id, size_px)
    return cv2.aruco.drawMarker(dictionary, tag_id, size_px)


def make_tag_image(
    tag_id: int = 1,
    size_px: int = 200,
    canvas: int = 640,
    border_ratio: float = 0.05,
) -> np.ndarray:
    """生成一张包含指定 AprilTag 的灰度图（白底 + 居中标签）。"""
    dictionary = cv2.aruco.getPredefinedDictionary(TAG_DICT)
    marker = _generate_marker(dictionary, tag_id, size_px)
    canvas_img = np.full((canvas, canvas), 255, dtype=np.uint8)
    border = int(size_px * border_ratio)
    x0 = (canvas - size_px - 2 * border) // 2
    y0 = (canvas - size_px - 2 * border) // 2
    canvas_img[y0:y0 + size_px, x0:x0 + size_px] = marker
    return canvas_img


class TagDetectionTests(unittest.TestCase):
    def test_plain_tag_is_detected_with_correct_id(self):
        image = make_tag_image(tag_id=1)
        results = detect_tags(image)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], 1)
        corners = results[0][1]
        self.assertEqual(corners.shape, (4, 2))

    def test_all_six_field_ids_are_detected(self):
        for tag_id in range(1, 7):
            with self.subTest(tag_id=tag_id):
                results = detect_tags(make_tag_image(tag_id=tag_id))
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0][0], tag_id)

    def test_blank_image_has_no_detections(self):
        blank = np.full((640, 640), 255, dtype=np.uint8)
        self.assertEqual(detect_tags(blank), [])

    def test_color_image_is_accepted(self):
        image = make_tag_image(tag_id=3)
        color = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        results = detect_tags(color)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], 3)


class TagPoseTests(unittest.TestCase):
    def test_frontal_tag_pose_distance_matches_geometry(self):
        # 生成 1.0m 远处正对相机的标签：用相机矩阵投影合成，再反解。
        camera_matrix = np.array(
            [[1275.0, 0.0, 320.0], [0.0, 1275.0, 240.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        dist = np.zeros((4, 1), dtype=np.float64)
        half = TAG_SIZE_M / 2.0
        object_points = np.array(
            [[-half, -half, 0.0], [half, -half, 0.0],
             [half, half, 0.0], [-half, half, 0.0]],
            dtype=np.float64,
        )
        # 标签在相机前方 1.0m，无旋转
        rvec_true = np.zeros((3, 1), dtype=np.float64)
        tvec_true = np.array([[0.0], [0.0], [1.0]], dtype=np.float64)
        image_points, _ = cv2.projectPoints(
            object_points, rvec_true, tvec_true, camera_matrix, dist
        )
        rvec, tvec = estimate_tag_pose(
            image_points.reshape(4, 2), camera_matrix=camera_matrix
        )
        distance, _ = observation_quality(tvec, rvec)
        self.assertAlmostEqual(distance, 1.0, places=2)

    def test_pose_recovers_simulated_offset(self):
        # 标签在相机右前方 0.5m、前 2.0m（即相机相对标签偏左）
        camera_matrix = np.array(
            [[1275.0, 0.0, 320.0], [0.0, 1275.0, 240.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        dist = np.zeros((4, 1), dtype=np.float64)
        half = TAG_SIZE_M / 2.0
        object_points = np.array(
            [[-half, -half, 0.0], [half, -half, 0.0],
             [half, half, 0.0], [-half, half, 0.0]],
            dtype=np.float64,
        )
        tvec_true = np.array([[0.5], [0.0], [2.0]], dtype=np.float64)
        rvec_true = np.zeros((3, 1), dtype=np.float64)
        image_points, _ = cv2.projectPoints(
            object_points, rvec_true, tvec_true, camera_matrix, dist
        )
        rvec, tvec = estimate_tag_pose(
            image_points.reshape(4, 2), camera_matrix=camera_matrix
        )
        t = tvec.flatten()
        self.assertAlmostEqual(t[0], 0.5, places=2)
        self.assertAlmostEqual(t[2], 2.0, places=2)


class TagRobustnessTests(unittest.TestCase):
    """缩放/透视/模糊/遮挡下的检测鲁棒性（合成图）。"""

    def _detect_in_frame(self, frame: np.ndarray) -> list[int]:
        return [tag_id for tag_id, _ in detect_tags(frame)]

    def test_scaled_down_tag_still_detected(self):
        image = make_tag_image(tag_id=2, size_px=300, canvas=640)
        small = cv2.resize(image, (320, 320))
        self.assertEqual(self._detect_in_frame(small), [2])

    def test_blurred_tag_still_detected(self):
        image = make_tag_image(tag_id=4)
        blurred = cv2.GaussianBlur(image, (5, 5), 0)
        self.assertEqual(self._detect_in_frame(blurred), [4])

    def test_perspective_warp_still_detected(self):
        image = make_tag_image(tag_id=5)
        h, w = image.shape
        src = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
        dst = np.float32([[w * 0.05, h * 0.05], [w * 0.95, h * 0.1],
                          [w * 0.9, h * 0.95], [w * 0.1, h * 0.9]])
        matrix = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(image, matrix, (w, h))
        self.assertEqual(self._detect_in_frame(warped), [5])

    def test_partial_occlusion_still_detected(self):
        image = make_tag_image(tag_id=6)
        occluded = image.copy()
        h, w = occluded.shape
        # 遮挡右下角 ~20% 区域（AprilTag 解码有一定容错）
        occluded[int(h * 0.8):, int(w * 0.8):] = 255
        self.assertEqual(self._detect_in_frame(occluded), [6])


class TagObservationFilterTests(unittest.TestCase):
    def test_far_tag_is_rejected(self):
        # 生成 5m 远处标签（超过 MAX_TAG_DISTANCE_M=4.0）
        camera_matrix = np.array(
            [[1275.0, 0.0, 320.0], [0.0, 1275.0, 240.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        half = TAG_SIZE_M / 2.0
        object_points = np.array(
            [[-half, -half, 0.0], [half, -half, 0.0],
             [half, half, 0.0], [-half, half, 0.0]],
            dtype=np.float64,
        )
        tvec = np.array([[0.0], [0.0], [5.0]], dtype=np.float64)
        rvec = np.zeros((3, 1), dtype=np.float64)
        image_points, _ = cv2.projectPoints(
            object_points, rvec, tvec, camera_matrix,
            np.zeros((4, 1), dtype=np.float64),
        )
        obs = build_observation(
            1, image_points.reshape(4, 2), camera_matrix=camera_matrix
        )
        self.assertFalse(obs.valid)
        self.assertGreater(obs.distance_m, MAX_TAG_DISTANCE_M)

    def test_steep_angle_tag_is_rejected(self):
        camera_matrix = np.array(
            [[1275.0, 0.0, 320.0], [0.0, 1275.0, 240.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        half = TAG_SIZE_M / 2.0
        object_points = np.array(
            [[-half, -half, 0.0], [half, -half, 0.0],
             [half, half, 0.0], [-half, half, 0.0]],
            dtype=np.float64,
        )
        # 相机大幅侧偏（标签在相机侧面 1m、前方 0.5m → 斜视角很大）
        tvec = np.array([[1.0], [0.0], [0.5]], dtype=np.float64)
        rvec = np.zeros((3, 1), dtype=np.float64)
        image_points, _ = cv2.projectPoints(
            object_points, rvec, tvec, camera_matrix,
            np.zeros((4, 1), dtype=np.float64),
        )
        obs = build_observation(
            1, image_points.reshape(4, 2), camera_matrix=camera_matrix
        )
        self.assertFalse(obs.valid)
        self.assertGreater(obs.yaw_error_rad, math.radians(60.0))

    def test_near_frontal_tag_is_valid(self):
        camera_matrix = np.array(
            [[1275.0, 0.0, 320.0], [0.0, 1275.0, 240.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        half = TAG_SIZE_M / 2.0
        object_points = np.array(
            [[-half, -half, 0.0], [half, -half, 0.0],
             [half, half, 0.0], [-half, half, 0.0]],
            dtype=np.float64,
        )
        tvec = np.array([[0.0], [0.0], [1.0]], dtype=np.float64)
        rvec = np.zeros((3, 1), dtype=np.float64)
        image_points, _ = cv2.projectPoints(
            object_points, rvec, tvec, camera_matrix,
            np.zeros((4, 1), dtype=np.float64),
        )
        obs = build_observation(
            1, image_points.reshape(4, 2), camera_matrix=camera_matrix
        )
        self.assertTrue(obs.valid)
        self.assertAlmostEqual(obs.distance_m, 1.0, places=2)


class DetectAndPoseEndToEndTests(unittest.TestCase):
    def test_detect_and_pose_returns_observation(self):
        image = make_tag_image(tag_id=2)
        observations = detect_and_pose(image)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].tag_id, 2)
        self.assertTrue(observations[0].valid)
        # 合成图里标签贴得很近（几百像素大），距离应较小
        self.assertLess(observations[0].distance_m, 1.0)


if __name__ == "__main__":
    unittest.main()
