import unittest
import math
from robogame_core.line_follow import (
    LineSensorState,
    LineSegment,
    LineSensorReading,
    SENSOR_POSITIONS,
    compute_deviation,
    compute_deviation_weighted,
    update_sensor_state,
    compute_correction,
)


class TestComputeDeviation(unittest.TestCase):
    def test_centered(self):
        dev = compute_deviation([0, 0, 0, 1, 1, 0, 0, 0])
        self.assertAlmostEqual(dev, 0.0, places=5)

    def test_left_single(self):
        dev = compute_deviation([1, 0, 0, 0, 0, 0, 0, 0])
        self.assertAlmostEqual(dev, -1.0, places=5)

    def test_right_single(self):
        dev = compute_deviation([0, 0, 0, 0, 0, 0, 0, 1])
        self.assertAlmostEqual(dev, 1.0, places=5)

    def test_left_double(self):
        dev = compute_deviation([1, 1, 0, 0, 0, 0, 0, 0])
        self.assertTrue(dev < 0)

    def test_right_double(self):
        dev = compute_deviation([0, 0, 0, 0, 0, 0, 1, 1])
        self.assertTrue(dev > 0)

    def test_no_line(self):
        dev = compute_deviation([0, 0, 0, 0, 0, 0, 0, 0])
        self.assertTrue(math.isnan(dev))

    def test_all_active(self):
        dev = compute_deviation([1, 1, 1, 1, 1, 1, 1, 1])
        self.assertTrue(math.isnan(dev))

    def test_invalid_count(self):
        with self.assertRaises(ValueError):
            compute_deviation([1, 0, 0])

    def test_symmetry(self):
        """左右对称模式偏差符号相反"""
        left = compute_deviation([1, 1, 0, 0, 0, 0, 0, 0])
        right = compute_deviation([0, 0, 0, 0, 0, 0, 1, 1])
        self.assertAlmostEqual(left, -right, places=5)


class TestComputeDeviationWeighted(unittest.TestCase):
    def test_centered_weighted(self):
        raw = [0.1, 0.1, 0.1, 0.9, 0.9, 0.1, 0.1, 0.1]
        dev = compute_deviation_weighted(raw, threshold=0.5)
        self.assertAlmostEqual(dev, 0.0, places=5)

    def test_left_weighted(self):
        raw = [0.9, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
        dev = compute_deviation_weighted(raw, threshold=0.5)
        self.assertTrue(dev < 0)

    def test_right_weighted(self):
        raw = [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.9]
        dev = compute_deviation_weighted(raw, threshold=0.5)
        self.assertTrue(dev > 0)

    def test_no_signal_weighted(self):
        raw = [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
        dev = compute_deviation_weighted(raw, threshold=0.5)
        self.assertTrue(math.isnan(dev))


class TestUpdateSensorState(unittest.TestCase):
    def test_on_line(self):
        state, count = update_sensor_state(
            [0, 0, 0, 1, 1, 0, 0, 0], LineSensorState.ON_LINE, 0
        )
        self.assertEqual(state, LineSensorState.ON_LINE)
        self.assertEqual(count, 0)

    def test_left_edge(self):
        state, count = update_sensor_state(
            [1, 1, 0, 0, 0, 0, 0, 0], LineSensorState.ON_LINE, 0
        )
        self.assertEqual(state, LineSensorState.LEFT_EDGE)
        self.assertEqual(count, 0)

    def test_right_edge(self):
        state, count = update_sensor_state(
            [0, 0, 0, 0, 0, 0, 1, 1], LineSensorState.ON_LINE, 0
        )
        self.assertEqual(state, LineSensorState.RIGHT_EDGE)
        self.assertEqual(count, 0)

    def test_lost_after_threshold(self):
        state = LineSensorState.ON_LINE
        count = 0
        for _ in range(5):
            state, count = update_sensor_state(
                [0, 0, 0, 0, 0, 0, 0, 0], state, count, lost_threshold=5
            )
        self.assertEqual(state, LineSensorState.LOST)
        self.assertEqual(count, 5)

    def test_not_lost_before_threshold(self):
        state, count = update_sensor_state(
            [0, 0, 0, 0, 0, 0, 0, 0], LineSensorState.ON_LINE, 0, lost_threshold=5
        )
        self.assertEqual(state, LineSensorState.ON_LINE)
        self.assertEqual(count, 1)

    def test_recover_from_lost(self):
        state, count = update_sensor_state(
            [0, 0, 0, 1, 1, 0, 0, 0], LineSensorState.LOST, 5
        )
        self.assertEqual(state, LineSensorState.ON_LINE)
        self.assertEqual(count, 0)

    def test_intersection(self):
        state, count = update_sensor_state(
            [1, 1, 1, 1, 1, 1, 0, 0], LineSensorState.ON_LINE, 0
        )
        self.assertEqual(state, LineSensorState.INTERSECTION)

    def test_all_black(self):
        state, count = update_sensor_state(
            [1, 1, 1, 1, 1, 1, 1, 1], LineSensorState.ON_LINE, 0
        )
        self.assertEqual(state, LineSensorState.ALL_BLACK)


class TestComputeCorrection(unittest.TestCase):
    def test_zero_deviation(self):
        vx, wz = compute_correction(0.0, 1.0, 0.5, 0.0, 0.01)
        self.assertAlmostEqual(vx, 0.2)
        self.assertAlmostEqual(wz, 0.0)

    def test_positive_deviation_turns_right(self):
        vx, wz = compute_correction(0.5, 1.0, 0.0, 0.0, 0.01)
        self.assertTrue(wz < 0)

    def test_negative_deviation_turns_left(self):
        vx, wz = compute_correction(-0.5, 1.0, 0.0, 0.0, 0.01)
        self.assertTrue(wz > 0)

    def test_nan_deviation_stops(self):
        vx, wz = compute_correction(float('nan'), 1.0, 0.5, 0.0, 0.01)
        self.assertEqual(vx, 0.0)
        self.assertEqual(wz, 0.0)

    def test_pd_proportional(self):
        vx, wz = compute_correction(0.5, 2.0, 0.0, 0.0, 0.01)
        self.assertAlmostEqual(wz, -1.0)

    def test_pd_derivative(self):
        vx, wz = compute_correction(0.5, 0.0, 1.0, 0.0, 0.01)
        self.assertAlmostEqual(wz, -50.0)


class TestLineSensorReading(unittest.TestCase):
    def test_active_count(self):
        reading = LineSensorReading(channels=[0, 1, 1, 0, 0, 0, 0, 0])
        self.assertEqual(reading.active_count, 2)

    def test_all_active(self):
        reading = LineSensorReading(channels=[1, 1, 1, 1, 1, 1, 1, 1])
        self.assertTrue(reading.is_all_active)

    def test_none_active(self):
        reading = LineSensorReading(channels=[0, 0, 0, 0, 0, 0, 0, 0])
        self.assertTrue(reading.is_none_active)

    def test_invalid_channels(self):
        with self.assertRaises(ValueError):
            LineSensorReading(channels=[1, 0, 0])


if __name__ == '__main__':
    unittest.main()

