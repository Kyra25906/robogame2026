import unittest

from robogame_core.models import CubeColor
from robogame_core.perception import Candidate, estimate_from_candidate, select_target


class PerceptionTests(unittest.TestCase):
    def test_pinhole_distance_and_lateral_error(self):
        candidate = Candidate(CubeColor.ORANGE, 370.0, 200.0, 100.0, 100.0, 10000.0, 9200.0)
        estimate = estimate_from_candidate(candidate, image_width=640, focal_px=700.0)
        self.assertIsNotNone(estimate)
        self.assertAlmostEqual(estimate.distance_m, 0.7)
        self.assertAlmostEqual(estimate.lateral_m, 0.05)

    def test_small_candidate_is_rejected(self):
        candidate = Candidate(CubeColor.PURPLE, 100.0, 100.0, 10.0, 10.0, 100.0, 50.0)
        self.assertIsNone(estimate_from_candidate(candidate, 640, 700.0))

    def test_target_selection_obeys_color(self):
        orange = estimate_from_candidate(
            Candidate(CubeColor.ORANGE, 320.0, 200.0, 100.0, 100.0, 10000.0, 9000.0), 640, 700.0
        )
        purple = estimate_from_candidate(
            Candidate(CubeColor.PURPLE, 320.0, 200.0, 100.0, 100.0, 10000.0, 9000.0), 640, 700.0
        )
        self.assertEqual(select_target([orange, purple], CubeColor.PURPLE).color, CubeColor.PURPLE)


if __name__ == "__main__":
    unittest.main()

