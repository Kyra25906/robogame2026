import math


def choose_yaw_rate(
    wheel_wz: float,
    imu_wz: float | None,
    imu_age_s: float | None,
    stale_after_s: float,
    *,
    status_valid: bool = True,
) -> tuple[float, bool]:
    """返回(选中的角速度，是否使用了IMU)。

    IMU新鲜且数据有效时使用IMU角速度，否则回退到轮式里程计角速度。
    """
    imu_valid = (
        status_valid
        and imu_wz is not None
        and imu_age_s is not None
        and imu_age_s <= stale_after_s
        and math.isfinite(imu_wz)
    )
    if imu_valid:
        return (imu_wz, True)
    return (wheel_wz, False)


def odometry_is_finite(values: list[float]) -> bool:
    """所有值为有限数时返回True。"""
    return all(math.isfinite(v) for v in values)


def detect_yaw_divergence(wheel_wz: float, imu_wz: float | None, threshold: float = 0.5) -> tuple[float, bool]:
    """比较轮式里程计角速度和IMU角速度，检测是否分歧过大。

    分歧过大通常意味着轮子打滑、编码器异常或IMU零偏漂移。

    Args:
        wheel_wz: 轮式里程计角速度 (rad/s)
        imu_wz: IMU角速度 (rad/s)，如果IMU不可用传None
        threshold: 分歧阈值 (rad/s)，默认0.5（约28.6°/s）

    Returns:
        tuple: (divergence: float, is_divergent: bool)
            - divergence: 绝对差值，IMU不可用时返回0.0
            - is_divergent: 是否超过阈值
    """
    if imu_wz is None or not math.isfinite(wheel_wz) or not math.isfinite(imu_wz):
        return 0.0, False
    diff = abs(wheel_wz - imu_wz)
    return diff, diff > threshold


def detect_pose_jump(prev_x: float, prev_y: float, curr_x: float, curr_y: float, dt: float, max_speed: float = 3.0) -> tuple[float, float, bool]:
    """检测连续两帧位姿之间的跳变是否超出物理极限。

    如果两帧之间的位移换算成速度超过max_speed，说明位姿可能跳变。

    Args:
        prev_x, prev_y: 上一帧位置 (m)
        curr_x, curr_y: 当前帧位置 (m)
        dt: 两帧时间间隔 (s)
        max_speed: 物理最大速度 (m/s)，默认3.0

    Returns:
        tuple: (distance: float, speed: float, is_jump: bool)
            - distance: 两帧间距 (m)
            - speed: 换算速度 (m/s)，dt<=0时返回inf
            - is_jump: 是否超出物理极限
    """
    if dt <= 0 or not all(math.isfinite(v) for v in (prev_x, prev_y, curr_x, curr_y)):
        return 0.0, float("inf"), False
    distance = math.hypot(curr_x - prev_x, curr_y - prev_y)
    speed = distance / dt
    return distance, speed, speed > max_speed


def classify_pose_quality(
    divergence: float,
    is_divergent: bool,
    jump_speed: float,
    is_jump: bool,
    imu_stale: bool,
    is_finite: bool,
) -> str:
    """根据多项检测结果综合分类位姿质量。

    Returns:
        str: "OK" | "WARN" | "REJECT"
            - OK: 数据正常，可发布
            - WARN: 有异常但不严重，发布但记录警告
            - REJECT: 严重异常，不应发布 /pose
    """
    if not is_finite or is_jump:
        return "REJECT"
    if imu_stale or is_divergent:
        return "WARN"
    return "OK"
