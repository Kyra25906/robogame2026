import math
import unittest

from localization.quality import (
    choose_yaw_rate,
    odometry_is_finite,
    detect_yaw_divergence,
    detect_pose_jump,
    classify_pose_quality,
)


class TestChooseYawRate(unittest.TestCase):
    """choose_yaw_rate 测试：IMU新鲜/过期/异常回退。"""

    def test_fresh_imu_used(self):
        """IMU新鲜且有效时使用IMU角速度。"""
        wz, used_imu = choose_yaw_rate(0.1, 0.15, 0.05, 0.2)
        self.assertAlmostEqual(wz, 0.15)
        self.assertTrue(used_imu)

    def test_stale_imu_fallback(self):
        """IMU过期时回退到轮式里程计。"""
        wz, used_imu = choose_yaw_rate(0.1, 0.15, 0.3, 0.2)
        self.assertAlmostEqual(wz, 0.1)
        self.assertFalse(used_imu)

    def test_imu_age_exactly_at_threshold(self):
        """IMU年龄刚好等于阈值时使用IMU。"""
        wz, used_imu = choose_yaw_rate(0.1, 0.15, 0.2, 0.2)
        self.assertAlmostEqual(wz, 0.15)
        self.assertTrue(used_imu)

    def test_no_imu(self):
        """没有IMU数据时回退到轮式里程计。"""
        wz, used_imu = choose_yaw_rate(0.1, None, None, 0.2)
        self.assertAlmostEqual(wz, 0.1)
        self.assertFalse(used_imu)

    def test_imu_nan_rejected(self):
        """IMU角速度为NaN时回退。"""
        wz, used_imu = choose_yaw_rate(0.1, float("nan"), 0.05, 0.2)
        self.assertAlmostEqual(wz, 0.1)
        self.assertFalse(used_imu)

    def test_imu_inf_rejected(self):
        """IMU角速度为inf时回退。"""
        wz, used_imu = choose_yaw_rate(0.1, float("inf"), 0.05, 0.2)
        self.assertAlmostEqual(wz, 0.1)
        self.assertFalse(used_imu)

    def test_imu_neg_inf_rejected(self):
        """IMU角速度为-inf时回退。"""
        wz, used_imu = choose_yaw_rate(0.1, float("-inf"), 0.05, 0.2)
        self.assertAlmostEqual(wz, 0.1)
        self.assertFalse(used_imu)


class TestOdometryIsFinite(unittest.TestCase):
    """odometry_is_finite 测试：NaN/inf/正常数据检查。"""

    def test_normal_values(self):
        """正常有限数据返回True。"""
        self.assertTrue(odometry_is_finite([1.0, 2.0, 3.0, 0.0, -1.5]))

    def test_nan_rejected(self):
        """包含NaN返回False。"""
        self.assertFalse(odometry_is_finite([1.0, float("nan"), 3.0]))

    def test_inf_rejected(self):
        """包含inf返回False。"""
        self.assertFalse(odometry_is_finite([1.0, float("inf"), 3.0]))

    def test_neg_inf_rejected(self):
        """包含-inf返回False。"""
        self.assertFalse(odometry_is_finite([1.0, float("-inf"), 3.0]))

    def test_empty_list(self):
        """空列表返回True（没有异常值）。"""
        self.assertTrue(odometry_is_finite([]))


class TestYawDivergence(unittest.TestCase):
    """A: 轮速与IMU角速度分歧检测测试。"""

    def test_normal_small_difference(self):
        """正常运动时轮速和IMU角速度接近，不报分歧。"""
        div, is_div = detect_yaw_divergence(0.1, 0.12)
        self.assertLess(div, 0.5)
        self.assertFalse(is_div)

    def test_large_divergence(self):
        """轮速和IMU差异过大，报分歧（可能打滑）。"""
        div, is_div = detect_yaw_divergence(0.1, 0.9)
        self.assertGreater(div, 0.5)
        self.assertTrue(is_div)

    def test_imu_none(self):
        """IMU不可用时不报分歧，返回0。"""
        div, is_div = detect_yaw_divergence(0.1, None)
        self.assertEqual(div, 0.0)
        self.assertFalse(is_div)

    def test_imu_nan(self):
        """IMU为NaN时不报分歧。"""
        div, is_div = detect_yaw_divergence(0.1, float("nan"))
        self.assertEqual(div, 0.0)
        self.assertFalse(is_div)

    def test_wheel_nan(self):
        """轮速为NaN时不报分歧。"""
        div, is_div = detect_yaw_divergence(float("nan"), 0.1)
        self.assertEqual(div, 0.0)
        self.assertFalse(is_div)

    def test_both_zero(self):
        """两者都为零时不报分歧。"""
        div, is_div = detect_yaw_divergence(0.0, 0.0)
        self.assertEqual(div, 0.0)
        self.assertFalse(is_div)

    def test_negative_values(self):
        """负角速度（顺时针旋转）也能正确检测。"""
        div, is_div = detect_yaw_divergence(-0.1, -0.15)
        self.assertLess(div, 0.5)
        self.assertFalse(is_div)

    def test_opposite_signs(self):
        """方向相反时差异最大。"""
        div, is_div = detect_yaw_divergence(0.5, -0.5)
        self.assertAlmostEqual(div, 1.0)
        self.assertTrue(is_div)

    def test_custom_threshold(self):
        """自定义阈值。"""
        div, is_div = detect_yaw_divergence(0.1, 0.3, threshold=0.15)
        self.assertTrue(is_div)

    def test_exact_threshold(self):
        """刚好等于阈值不算分歧。"""
        div, is_div = detect_yaw_divergence(0.0, 0.5, threshold=0.5)
        self.assertFalse(is_div)


class TestPoseJump(unittest.TestCase):
    """B: 位姿跳变检测测试。"""

    def test_normal_movement(self):
        """正常速度移动不报跳变。"""
        dist, speed, is_jump = detect_pose_jump(0.0, 0.0, 0.05, 0.0, 0.1)
        self.assertAlmostEqual(dist, 0.05, places=4)
        self.assertAlmostEqual(speed, 0.5, places=4)
        self.assertFalse(is_jump)

    def test_large_jump(self):
        """位置突变报跳变。"""
        dist, speed, is_jump = detect_pose_jump(0.0, 0.0, 1.0, 0.0, 0.1)
        self.assertTrue(is_jump)

    def test_diagonal_movement(self):
        """对角线移动按距离计算。"""
        dist, speed, is_jump = detect_pose_jump(0.0, 0.0, 0.03, 0.04, 0.1)
        self.assertAlmostEqual(dist, 0.05, places=4)
        self.assertAlmostEqual(speed, 0.5, places=4)
        self.assertFalse(is_jump)

    def test_zero_dt(self):
        """时间间隔为零时速度为inf，不报跳变。"""
        dist, speed, is_jump = detect_pose_jump(0.0, 0.0, 0.1, 0.0, 0.0)
        self.assertEqual(speed, float("inf"))
        self.assertFalse(is_jump)

    def test_negative_dt(self):
        """负时间间隔按无效处理。"""
        dist, speed, is_jump = detect_pose_jump(0.0, 0.0, 0.1, 0.0, -0.1)
        self.assertEqual(speed, float("inf"))
        self.assertFalse(is_jump)

    def test_nan_position(self):
        """位置为NaN时不报跳变。"""
        dist, speed, is_jump = detect_pose_jump(
            float("nan"), 0.0, 0.1, 0.0, 0.1
        )
        self.assertFalse(is_jump)

    def test_custom_max_speed(self):
        """自定义最大速度。"""
        dist, speed, is_jump = detect_pose_jump(
            0.0, 0.0, 0.2, 0.0, 0.1, max_speed=1.0
        )
        self.assertAlmostEqual(speed, 2.0, places=4)
        self.assertTrue(is_jump)

    def test_just_at_limit(self):
        """刚好等于最大速度不算跳变。"""
        dist, speed, is_jump = detect_pose_jump(
            0.0, 0.0, 0.3, 0.0, 0.1, max_speed=3.0
        )
        self.assertAlmostEqual(speed, 3.0, places=4)
        self.assertFalse(is_jump)


class TestClassifyPoseQuality(unittest.TestCase):
    """综合位姿质量分类测试。"""

    def test_all_normal(self):
        """全部正常返回OK。"""
        result = classify_pose_quality(0.1, False, 0.5, False, False, True)
        self.assertEqual(result, "OK")

    def test_imu_stale_warn(self):
        """IMU过期返回WARN。"""
        result = classify_pose_quality(0.1, False, 0.5, False, True, True)
        self.assertEqual(result, "WARN")

    def test_divergent_warn(self):
        """角速度分歧返回WARN。"""
        result = classify_pose_quality(0.8, True, 0.5, False, False, True)
        self.assertEqual(result, "WARN")

    def test_jump_reject(self):
        """位姿跳变返回REJECT。"""
        result = classify_pose_quality(0.1, False, 10.0, True, False, True)
        self.assertEqual(result, "REJECT")

    def test_not_finite_reject(self):
        """数据非有限返回REJECT。"""
        result = classify_pose_quality(0.1, False, 0.5, False, False, False)
        self.assertEqual(result, "REJECT")

    def test_reject_priority_over_warn(self):
        """REJECT优先级高于WARN。"""
        result = classify_pose_quality(0.8, True, 10.0, True, True, True)
        self.assertEqual(result, "REJECT")


if __name__ == "__main__":
    unittest.main()
