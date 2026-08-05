import math

def choose_yaw_rate(wheel_wz: float, imu_wz: float | None, imu_age_s: float | None, stale_after_s: float) -> tuple[float, bool]:
    """返回(选中的角速度, 是否使用了IMU)。
    
    - IMU 数据新鲜且为有限数值时使用 IMU 角速度
    - IMU 过期或无效时回退到轮式里程计角速度
    """
    if imu_wz is not None and imu_age_s is not None and imu_age_s <= stale_after_s and math.isfinite(imu_wz):
        return (imu_wz, True)
    return (wheel_wz, False)

def odometry_is_finite(values: list[float]) -> bool:
    """所有关键数值均为有限数时返回 True。"""
    return all(math.isfinite(v) for v in values)
