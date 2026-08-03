from __future__ import annotations

import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from robogame_core.models import CubeColor
from robogame_interfaces.msg import CubeDetection, CubeDetectionArray
from sensor_msgs.msg import CameraInfo, Image

from .opencv_detector import CubeDetector, DetectorConfig
from .temporal_filter import TemporalDetectionFilter, TemporalFilterConfig


class CubePerceptionNode(Node):
    def __init__(self) -> None:
        super().__init__("cube_perception")
        defaults = {
            "orange_hsv": [5, 100, 80, 25, 255, 255],
            "purple_hsv": [125, 60, 50, 165, 255, 255],
            "min_area_px": 400.0,
            "max_aspect_error": 0.8,
            "cube_size_m": 0.1,
            "fallback_focal_px": 700.0,
            "morph_kernel": 5,
            "min_confidence": 0.45,
            "confirm_frames": 3,
            "max_missed_frames": 3,
            "match_distance_px": 80.0,
            "smoothing_alpha": 0.4,
            "roi_x_min_ratio": 0.0,
            "roi_x_max_ratio": 1.0,
            "roi_y_min_ratio": 0.0,
            "roi_y_max_ratio": 1.0,
            "max_area_ratio": 0.35,
            "min_rectangularity": 0.35,
            "min_solidity": 0.50,
            "min_side_px": 12.0,
            "max_rotated_aspect_ratio": 3.2,
        }
        for name, value in defaults.items():
            self.declare_parameter(name, value)
        self.bridge = CvBridge()
        self.detector = CubeDetector(self._read_config())
        self.temporal_filter = TemporalDetectionFilter(self._read_temporal_config())
        self.publisher = self.create_publisher(CubeDetectionArray, "/cubes", 10)
        self.create_subscription(Image, "/camera/image_raw", self._on_image, 10)
        self.create_subscription(CameraInfo, "/camera/camera_info", self._on_camera_info, 10)

    def _read_config(self) -> DetectorConfig:
        return DetectorConfig(
            orange_hsv=list(self.get_parameter("orange_hsv").value),
            purple_hsv=list(self.get_parameter("purple_hsv").value),
            min_area_px=float(self.get_parameter("min_area_px").value),
            max_aspect_error=float(self.get_parameter("max_aspect_error").value),
            cube_size_m=float(self.get_parameter("cube_size_m").value),
            focal_px=float(self.get_parameter("fallback_focal_px").value),
            morph_kernel=int(self.get_parameter("morph_kernel").value),
            min_confidence=float(self.get_parameter("min_confidence").value),
            confirm_frames=int(self.get_parameter("confirm_frames").value),
            max_missed_frames=int(self.get_parameter("max_missed_frames").value),
            match_distance_px=float(self.get_parameter("match_distance_px").value),
            smoothing_alpha=float(self.get_parameter("smoothing_alpha").value),
            roi_x_min_ratio=float(self.get_parameter("roi_x_min_ratio").value),
            roi_x_max_ratio=float(self.get_parameter("roi_x_max_ratio").value),
            roi_y_min_ratio=float(self.get_parameter("roi_y_min_ratio").value),
            roi_y_max_ratio=float(self.get_parameter("roi_y_max_ratio").value),
            max_area_ratio=float(self.get_parameter("max_area_ratio").value),
            min_rectangularity=float(self.get_parameter("min_rectangularity").value),
            min_solidity=float(self.get_parameter("min_solidity").value),
            min_side_px=float(self.get_parameter("min_side_px").value),
            max_rotated_aspect_ratio=float(self.get_parameter("max_rotated_aspect_ratio").value),
        )

    def _read_temporal_config(self) -> TemporalFilterConfig:
        return TemporalFilterConfig(
            confirm_frames=int(self.get_parameter("confirm_frames").value),
            max_missed_frames=int(self.get_parameter("max_missed_frames").value),
            match_distance_px=float(self.get_parameter("match_distance_px").value),
            smoothing_alpha=float(self.get_parameter("smoothing_alpha").value),
        )

    def _on_camera_info(self, msg: CameraInfo) -> None:
        if msg.k[0] > 0.0:
            self.detector.config.focal_px = float(msg.k[0])

    def _on_image(self, msg: Image) -> None:
        frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        estimates, _masks = self.detector.detect(frame)
        estimates = self.temporal_filter.update(estimates)

        output = CubeDetectionArray()
        output.stamp = msg.header.stamp
        for estimate in estimates:
            detection = CubeDetection()
            detection.stamp = msg.header.stamp
            detection.color = CubeDetection.ORANGE if estimate.color is CubeColor.ORANGE else CubeDetection.PURPLE
            detection.confidence = estimate.confidence
            detection.lateral_m = estimate.lateral_m
            detection.distance_m = estimate.distance_m
            detection.yaw_error_rad = estimate.yaw_error_rad
            detection.pixel_x, detection.pixel_y = estimate.pixel_x, estimate.pixel_y
            output.detections.append(detection)
        self.publisher.publish(output)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = CubePerceptionNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
