import unittest

from cube_perception.temporal_filter import TemporalDetectionFilter, TemporalFilterConfig
from robogame_core.models import CubeColor
from robogame_core.perception import DetectionEstimate


def detection(color=CubeColor.ORANGE, x=320.0, y=240.0, distance=0.5):
    return DetectionEstimate(color, 0.9, 0.0, distance, 0.0, x, y)


class TemporalFilterTests(unittest.TestCase):
    def test_requires_consecutive_frames(self):
        tracker = TemporalDetectionFilter(TemporalFilterConfig(confirm_frames=3))
        self.assertEqual(tracker.update([detection()]), [])
        self.assertEqual(tracker.update([detection(x=322.0)]), [])
        result = tracker.update([detection(x=324.0)])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].color, CubeColor.ORANGE)

    def test_large_jump_starts_confirmation_again(self):
        tracker = TemporalDetectionFilter(TemporalFilterConfig(confirm_frames=2, match_distance_px=30.0))
        tracker.update([detection(x=100.0)])
        self.assertEqual(len(tracker.update([detection(x=105.0)])), 1)
        self.assertEqual(tracker.update([detection(x=300.0)]), [])
        self.assertEqual(len(tracker.update([detection(x=302.0)])), 1)

    def test_missing_frame_breaks_consecutive_streak(self):
        tracker = TemporalDetectionFilter(TemporalFilterConfig(confirm_frames=2, max_missed_frames=3))
        tracker.update([detection()])
        self.assertEqual(tracker.update([]), [])
        self.assertEqual(tracker.update([detection()]), [])
        self.assertEqual(len(tracker.update([detection()])), 1)

    def test_smoothing_reduces_position_jump(self):
        tracker = TemporalDetectionFilter(TemporalFilterConfig(confirm_frames=2, smoothing_alpha=0.25))
        tracker.update([detection(x=100.0)])
        result = tracker.update([detection(x=120.0)])
        self.assertAlmostEqual(result[0].pixel_x, 105.0)

    def test_colors_are_confirmed_independently(self):
        tracker = TemporalDetectionFilter(TemporalFilterConfig(confirm_frames=2))
        tracker.update([detection(CubeColor.ORANGE)])
        result = tracker.update([detection(CubeColor.ORANGE), detection(CubeColor.PURPLE)])
        self.assertEqual([item.color for item in result], [CubeColor.ORANGE])


if __name__ == "__main__":
    unittest.main()
