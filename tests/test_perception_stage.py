import unittest

from cube_perception.stage import (
    PerceptionStage,
    parse_perception_stage,
    stage_profile,
)


class PerceptionStageTests(unittest.TestCase):
    def test_accepts_all_contract_values(self):
        self.assertIs(parse_perception_stage("SEARCH"), PerceptionStage.SEARCH)
        self.assertIs(parse_perception_stage("ACQUIRE"), PerceptionStage.ACQUIRE)
        self.assertIs(parse_perception_stage("VERIFY"), PerceptionStage.VERIFY)

    def test_normalizes_whitespace_and_case(self):
        self.assertIs(parse_perception_stage("  acquire  "), PerceptionStage.ACQUIRE)

    def test_rejects_unknown_or_empty_stage(self):
        for value in ("", "TRACK", "GRAB"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "expected one of"):
                    parse_perception_stage(value)

    def test_search_profile_uses_agreed_distance(self):
        self.assertEqual(
            stage_profile(PerceptionStage.SEARCH).max_working_distance_m,
            1.2,
        )

    def test_acquire_and_verify_profiles_use_close_range(self):
        self.assertEqual(
            stage_profile(PerceptionStage.ACQUIRE).max_working_distance_m,
            0.8,
        )
        self.assertEqual(
            stage_profile(PerceptionStage.VERIFY).max_working_distance_m,
            0.8,
        )


if __name__ == "__main__":
    unittest.main()
