import unittest
import math
from localization.quality import choose_yaw_rate, odometry_is_finite

class TestChooseYawRate(unittest.TestCase):
    def test_fresh_imu_used(self):
        wz, used_imu = choose_yaw_rate(0.1, 0.5, 0.05, 0.2)
        self.assertAlmostEqual(wz, 0.5)
        self.assertTrue(used_imu)

    def test_stale_imu_fallback(self):
        wz, used_imu = choose_yaw_rate(0.1, 0.5, 0.3, 0.2)
        self.assertAlmostEqual(wz, 0.1)
        self.assertFalse(used_imu)

    def test_no_imu(self):
        wz, used_imu = choose_yaw_rate(0.1, None, None, 0.2)
        self.assertAlmostEqual(wz, 0.1)
        self.assertFalse(used_imu)

    def test_imu_age_exactly_at_threshold(self):
        wz, used_imu = choose_yaw_rate(0.1, 0.5, 0.2, 0.2)
        self.assertAlmostEqual(wz, 0.5)
        self.assertTrue(used_imu)

    def test_imu_nan_rejected(self):
        wz, used_imu = choose_yaw_rate(0.1, float('nan'), 0.05, 0.2)
        self.assertAlmostEqual(wz, 0.1)
        self.assertFalse(used_imu)

    def test_imu_inf_rejected(self):
        wz, used_imu = choose_yaw_rate(0.1, float('inf'), 0.05, 0.2)
        self.assertAlmostEqual(wz, 0.1)
        self.assertFalse(used_imu)

    def test_imu_neg_inf_rejected(self):
        wz, used_imu = choose_yaw_rate(0.1, float('-inf'), 0.05, 0.2)
        self.assertAlmostEqual(wz, 0.1)
        self.assertFalse(used_imu)

class TestOdometryIsFinite(unittest.TestCase):
    def test_normal_values(self):
        self.assertTrue(odometry_is_finite([1.0, 2.0, 3.0, 0.0, -1.5]))

    def test_nan_rejected(self):
        self.assertFalse(odometry_is_finite([1.0, float('nan'), 3.0]))

    def test_inf_rejected(self):
        self.assertFalse(odometry_is_finite([1.0, float('inf'), 3.0]))

    def test_neg_inf_rejected(self):
        self.assertFalse(odometry_is_finite([1.0, float('-inf'), 3.0]))

    def test_empty_list(self):
        self.assertTrue(odometry_is_finite([]))

if __name__ == '__main__':
    unittest.main()

