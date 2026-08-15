import unittest

from robogame_core.mcu_time import validate_mcu_tick


class McuTickValidationTests(unittest.TestCase):
    def test_initial_and_normal_forward_ticks_pass(self):
        self.assertTrue(validate_mcu_tick(None, 100, max_gap_ms=250).accepted)
        result = validate_mcu_tick(100, 120, max_gap_ms=250)
        self.assertTrue(result.accepted)
        self.assertEqual(result.delta_ms, 20)

    def test_duplicate_is_rejected_without_resync(self):
        result = validate_mcu_tick(100, 100, max_gap_ms=250)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "duplicate")
        self.assertFalse(result.resync)

    def test_backward_tick_is_rejected_without_resync(self):
        result = validate_mcu_tick(120, 100, max_gap_ms=250)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "backward")
        self.assertFalse(result.resync)

    def test_large_forward_gap_is_rejected_and_requests_resync(self):
        result = validate_mcu_tick(100, 400, max_gap_ms=250)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "gap")
        self.assertTrue(result.resync)

    def test_uint32_wrap_with_small_delta_passes(self):
        result = validate_mcu_tick(0xFFFFFFF5, 9, max_gap_ms=250)
        self.assertTrue(result.accepted)
        self.assertEqual(result.delta_ms, 20)

    def test_invalid_arguments_are_rejected(self):
        for previous, current, gap in ((None, -1, 250), (None, 1 << 32, 250), (0, 1, 0)):
            with self.subTest(previous=previous, current=current, gap=gap):
                with self.assertRaises(ValueError):
                    validate_mcu_tick(previous, current, max_gap_ms=gap)


if __name__ == "__main__":
    unittest.main()
