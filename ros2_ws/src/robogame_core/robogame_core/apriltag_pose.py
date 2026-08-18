"""C2: AprilTag 标签识别 + PnP 位姿解算（纯算法，零 ROS 依赖）。

背景（执行队列 2026-08-18 C2）：机器人靠轮式里程计积分定位，6 分钟必然
漂移。需要绝对矫正源——识别场地里的 AprilTag 标签（Tag36h11，id 1-6，
15x15cm，墙上 40cm 处），用「已知标签在场地哪个位置 + 相机看到的它」反推
「我在哪」，消除里程计漂移（乙3）。

本模块提供：
- ``detect_tags``：用 cv2.aruco 检测图像里的 AprilTag，返回 ID + 角点
- ``estimate_tag_pose``：PnP 解算相机相对标签的位姿（rvec/tvec）
- ``TagObservation``：一次观测（ID + 位姿 + 质量），供 localization 融合
- 异常观测拒绝：离群距离、远距、大角度（防止坏观测污染定位）

说明：检测器按 AprilTag 36h11 实现（执行队列确认）；仓库样例 tag_01~06.png
经检测不是标准 AprilTag（可能是数字牌），现场确认样式后若为数字牌，只需
替换 ``detect_tags`` 内部为模板识别，``estimate_tag_pose``/融合逻辑不变。

依赖：opencv-python（含 aruco 模块）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

# 场地标签约定（执行队列 C2 + 待现场确认）：
# - 字典：AprilTag Tag36h11
# - 尺寸：15x15 cm
# - 安装：墙上约 40cm 高处（几何冲突见「待拍板决策 1」）
TAG_DICT = cv2.aruco.DICT_APRILTAG_36h11
TAG_SIZE_M = 0.15

# 异常观测拒绝阈值（保守首值，现场标定后可调）
MAX_TAG_DISTANCE_M = 4.0  # 超过此距离的观测不可信（角点精度不够）
MAX_TAG_YAW_ERROR_RAD = math.radians(60.0)  # 相机相对标签法线的最大角度


@dataclass(frozen=True)
class TagObservation:
    """一次可信的标签观测。"""

    tag_id: int
    distance_m: float  # 相机到标签中心的距离
    yaw_error_rad: float  # 相机光轴与标签法线的夹角（越小越正对）
    # 相机坐标系下标签中心的平移（x 右、y 下、z 前，OpenCV 惯例）
    tvec: tuple[float, float, float]
    rvec: tuple[float, float, float]
    corners: tuple[tuple[float, float], ...]  # 4 个角点像素坐标
    valid: bool = True


def _detect_markers(gray: np.ndarray, dictionary) -> tuple[list, np.ndarray | None]:
    """跨 OpenCV 版本检测标记。

    - 新版（4.7+）：``ArucoDetector`` + ``detectMarkers``
    - 旧版（4.6-）：``DetectorParameters_create`` + ``aruco.detectMarkers``
    返回 (corners_list, ids)，与 cv2.aruco 约定一致。
    """
    if hasattr(cv2.aruco, "ArucoDetector"):
        detector = cv2.aruco.ArucoDetector(dictionary)
        return detector.detectMarkers(gray)
    parameters = cv2.aruco.DetectorParameters_create()
    return cv2.aruco.detectMarkers(gray, dictionary, parameters=parameters)


def detect_tags(
    image: np.ndarray,
    *,
    dictionary: int = TAG_DICT,
) -> list[tuple[int, np.ndarray]]:
    """检测图像里的 AprilTag，返回 [(tag_id, corners_4x2)]。

    corners 为 4x2 float 数组（像素坐标，顺序：左上、右上、右下、左下）。
    图像需为灰度或 BGR；内部转灰度。
    """
    if image is None or image.size == 0:
        return []
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image
    dictionary = cv2.aruco.getPredefinedDictionary(dictionary)
    corners_list, ids, _rejected = _detect_markers(gray, dictionary)
    results: list[tuple[int, np.ndarray]] = []
    if ids is None:
        return results
    for tag_id, corners in zip(ids.flatten(), corners_list):
        results.append((int(tag_id), np.asarray(corners[0], dtype=np.float64)))
    return results


def estimate_tag_pose(
    corners: np.ndarray,
    *,
    tag_size_m: float = TAG_SIZE_M,
    camera_matrix: np.ndarray | None = None,
    dist_coeffs: np.ndarray | None = None,
    focal_px: float = 1275.0,
) -> tuple[np.ndarray, np.ndarray]:
    """PnP 解算相机相对标签的位姿。

    返回 (rvec, tvec)：
    - rvec: 旋转向量（标签坐标系到相机坐标系）
    - tvec: 平移向量（相机坐标系下标签中心位置，单位米）
    内参缺省时用焦距 1275.0 @ 640x480（A3 统一值），主点取图像中心
    （320, 240）。现场标定后传入真实 camera_matrix。
    """
    if corners.shape != (4, 2):
        raise ValueError("corners must be 4x2")
    if tag_size_m <= 0.0:
        raise ValueError("tag_size_m must be positive")
    if camera_matrix is None:
        camera_matrix = np.array(
            [[focal_px, 0.0, 320.0], [0.0, focal_px, 240.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
    if dist_coeffs is None:
        dist_coeffs = np.zeros((4, 1), dtype=np.float64)
    half = tag_size_m / 2.0
    object_points = np.array(
        [
            [-half, -half, 0.0],
            [half, -half, 0.0],
            [half, half, 0.0],
            [-half, half, 0.0],
        ],
        dtype=np.float64,
    )
    # corners 顺序：左上、右上、右下、左下 —— 与 object_points 对应。
    success, rvec, tvec = cv2.solvePnP(
        object_points, corners, camera_matrix, dist_coeffs
    )
    if not success:
        raise ValueError("solvePnP failed")
    return rvec, tvec


def _rvec_to_axis_angle(rvec: np.ndarray) -> tuple[float, np.ndarray]:
    norm = float(np.linalg.norm(rvec))
    if norm < 1e-12:
        return 0.0, np.array([1.0, 0.0, 0.0])
    return norm, rvec.flatten() / norm


def observation_quality(
    tvec: np.ndarray, rvec: np.ndarray
) -> tuple[float, float]:
    """从 PnP 结果计算 (distance_m, yaw_error_rad)。

    yaw_error_rad：相机光轴（+z）与「相机到标签中心」方向的夹角。
    标签正对相机时约 0，斜视时增大。
    """
    t = np.asarray(tvec, dtype=np.float64).flatten()
    distance = float(np.linalg.norm(t))
    if distance < 1e-9:
        return 0.0, 0.0
    # 光轴 +z 与 t 方向的夹角
    axis = np.array([0.0, 0.0, 1.0])
    cos_angle = float(np.dot(axis, t) / distance)
    yaw_error = math.acos(max(-1.0, min(1.0, cos_angle)))
    return distance, yaw_error


def build_observation(
    tag_id: int,
    corners: np.ndarray,
    *,
    tag_size_m: float = TAG_SIZE_M,
    focal_px: float = 1275.0,
    max_distance_m: float = MAX_TAG_DISTANCE_M,
    max_yaw_error_rad: float = MAX_TAG_YAW_ERROR_RAD,
    camera_matrix: np.ndarray | None = None,
    dist_coeffs: np.ndarray | None = None,
) -> TagObservation:
    """检测结果 → 可信观测（含异常拒绝）。

    拒绝条件（返回 valid=False）：
    - 距离超过 max_distance_m（角点像素精度不足）
    - 斜视角度超过 max_yaw_error_rad（大角度观测位姿误差大）
    被拒绝的观测仍返回（valid=False），由上层决定忽略或降权，
    而不是静默丢弃——便于排障。
    """
    rvec, tvec = estimate_tag_pose(
        corners,
        tag_size_m=tag_size_m,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
        focal_px=focal_px,
    )
    distance, yaw_error = observation_quality(tvec, rvec)
    valid = distance <= max_distance_m and yaw_error <= max_yaw_error_rad
    corners_tuple = tuple(
        (float(c[0]), float(c[1])) for c in np.asarray(corners, dtype=np.float64)
    )
    return TagObservation(
        tag_id=tag_id,
        distance_m=distance,
        yaw_error_rad=yaw_error,
        tvec=tuple(float(v) for v in np.asarray(tvec, dtype=np.float64).flatten()),
        rvec=tuple(float(v) for v in np.asarray(rvec, dtype=np.float64).flatten()),
        corners=corners_tuple,
        valid=valid,
    )


def detect_and_pose(
    image: np.ndarray,
    *,
    tag_size_m: float = TAG_SIZE_M,
    focal_px: float = 1275.0,
    max_distance_m: float = MAX_TAG_DISTANCE_M,
    max_yaw_error_rad: float = MAX_TAG_YAW_ERROR_RAD,
) -> list[TagObservation]:
    """一步完成：检测 + 位姿 + 质量过滤。返回全部观测（含无效的）。"""
    observations: list[TagObservation] = []
    for tag_id, corners in detect_tags(image):
        observations.append(build_observation(
            tag_id,
            corners,
            tag_size_m=tag_size_m,
            focal_px=focal_px,
            max_distance_m=max_distance_m,
            max_yaw_error_rad=max_yaw_error_rad,
        ))
    return observations
