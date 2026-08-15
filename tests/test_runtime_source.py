import unittest
import ast
from pathlib import Path

from robogame_core.runtime_source import (
    CRITICAL_TOPICS,
    PublisherIdentity,
    RuntimeSource,
    classify_status_detail,
    validate_runtime_sources,
)


def _publishers():
    return {
        topic: [PublisherIdentity("robot_bridge", "/")]
        for topic in CRITICAL_TOPICS
    }


class RuntimeSourceTests(unittest.TestCase):
    def test_classifies_mock_and_all_field_status_markers(self):
        self.assertIs(classify_status_detail("mock hardware"), RuntimeSource.MOCK)
        for detail in (
            "decoded MCU V1 STATUS",
            "MCU transport active; decoded RobotStatus unavailable",
            "MCU transport inactive; decoded RobotStatus unavailable",
        ):
            self.assertIs(classify_status_detail(detail), RuntimeSource.FIELD)

    def test_clean_mock_and_field_sources_pass(self):
        self.assertTrue(validate_runtime_sources(
            "mock", _publishers(), ["mock hardware"]
        ).passed)
        self.assertTrue(validate_runtime_sources(
            "field", _publishers(),
            ["MCU transport inactive; decoded RobotStatus unavailable"],
        ).passed)

    def test_mixed_sources_fail_even_with_expected_sample_present(self):
        result = validate_runtime_sources(
            "field", _publishers(),
            ["decoded MCU V1 STATUS", "mock hardware"],
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("mixed mock and field" in issue for issue in result.issues))

    def test_duplicate_missing_and_wrong_publishers_fail(self):
        duplicate = _publishers()
        duplicate["/robot/status"].append(PublisherIdentity("robot_bridge", "/"))
        self.assertFalse(validate_runtime_sources(
            "mock", duplicate, ["mock hardware"]
        ).passed)
        missing = _publishers()
        missing["/imu/data"] = []
        self.assertFalse(validate_runtime_sources(
            "mock", missing, ["mock hardware"]
        ).passed)
        wrong = _publishers()
        wrong["/wheel_odom"] = [PublisherIdentity("fake_odom", "/")]
        self.assertFalse(validate_runtime_sources(
            "mock", wrong, ["mock hardware"]
        ).passed)

    def test_unknown_detail_no_samples_and_unknown_mode_fail_closed(self):
        self.assertFalse(validate_runtime_sources(
            "field", _publishers(), ["mystery source"]
        ).passed)
        self.assertFalse(validate_runtime_sources(
            "field", _publishers(), []
        ).passed)
        with self.assertRaisesRegex(ValueError, "expected_mode"):
            validate_runtime_sources("auto", _publishers(), ["mock hardware"])


class RuntimeSourceLaunchTests(unittest.TestCase):
    def test_all_main_launches_install_a_blocking_source_guard(self):
        launch_dir = (
            Path(__file__).resolve().parents[1]
            / "ros2_ws" / "src" / "robogame_bringup" / "launch"
        )
        expected_modes = {
            "mock_demo.launch.py": "mock",
            "single_cube.launch.py": "mock",
            "hardware.launch.py": "field",
        }
        for filename, expected_mode in expected_modes.items():
            with self.subTest(filename=filename):
                source = (launch_dir / filename).read_text(encoding="utf-8")
                ast.parse(source)
                self.assertIn('executable="runtime_source_guard"', source)
                self.assertIn(f'"expected_mode": "{expected_mode}"', source)
                self.assertIn('"stay_alive_on_pass": True', source)
                self.assertIn("OnProcessExit", source)
                self.assertIn("Shutdown", source)


if __name__ == "__main__":
    unittest.main()
