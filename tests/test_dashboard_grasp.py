import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.field_dashboard import DashboardController, RosFacade

WEB_ROOT = Path(__file__).resolve().parents[1] / "tools" / "field_dashboard_web"


def controller() -> DashboardController:
    return DashboardController(Mock(snapshot=lambda: []), Mock(), Mock(), Mock())


def facade_with(controller_: DashboardController, detections) -> RosFacade:
    facade = RosFacade.__new__(RosFacade)
    facade.controller = controller_
    with patch("tools.field_dashboard.time.monotonic", return_value=10):
        facade._cubes(SimpleNamespace(detections=detections))
    return facade


def detection(distance_m, lateral_m, confidence=0.9, color=0):
    return SimpleNamespace(distance_m=distance_m, lateral_m=lateral_m,
                           confidence=confidence, color=color)


class GraspSnapshotTests(unittest.TestCase):
    def test_snapshot_reports_no_detection_before_any_cube_message(self):
        snapshot = controller().snapshot()

        self.assertEqual(snapshot["grasp"]["source"], "no_detection")
        self.assertIn("message", snapshot["grasp"])

    def test_cube_message_publishes_offset_target_as_turn_phase(self):
        dashboard = controller()
        facade_with(dashboard, [detection(0.30, 0.10)])

        grasp = dashboard.snapshot()["grasp"]

        self.assertEqual(grasp["source"], "cubes")
        self.assertEqual(grasp["phase"], "TURN")
        self.assertEqual(grasp["linear_x"], 0.0)
        self.assertLess(grasp["angular_z"], 0.0)
        self.assertEqual(grasp["linear_y"], 0.0)
        self.assertAlmostEqual(grasp["cross_m"], -0.10)
        self.assertAlmostEqual(grasp["along_m"], 0.30)

    def test_on_axis_cube_message_reports_ready(self):
        dashboard = controller()
        facade_with(dashboard, [detection(0.25, 0.004)])

        grasp = dashboard.snapshot()["grasp"]

        self.assertTrue(grasp["ready_to_grab"])
        self.assertEqual(grasp["phase"], "READY")

    def test_highest_confidence_detection_is_used(self):
        dashboard = controller()
        facade_with(dashboard, [detection(0.30, 0.10, confidence=0.5),
                                detection(0.25, 0.001, confidence=0.95, color=1)])

        grasp = dashboard.snapshot()["grasp"]

        self.assertEqual(grasp["detections"], 2)
        self.assertEqual(grasp["color"], "purple")
        self.assertTrue(grasp["ready_to_grab"])

    def test_empty_detection_array_is_not_reported_as_missing_topic(self):
        dashboard = controller()
        facade_with(dashboard, [])

        grasp = dashboard.snapshot()["grasp"]

        self.assertEqual(grasp["source"], "cubes")
        self.assertNotIn("phase", grasp)
        self.assertEqual(grasp["detections"], 0)


class GraspSimulateRouteTests(unittest.TestCase):
    def test_route_returns_trace_without_ros(self):
        dashboard = controller()

        result = asyncio.run(dashboard.action("/api/grasp/simulate", {"target_y_m": -0.15}))

        self.assertTrue(result["converged"])
        self.assertEqual(result["safety_violations"], 0)
        self.assertGreater(len(result["trace"]), 0)
        self.assertEqual(dashboard.ros, None)

    def test_route_rejects_unknown_parameter(self):
        dashboard = controller()

        with self.assertRaises(ValueError):
            asyncio.run(dashboard.action("/api/grasp/simulate", {"not_a_parameter": 1}))

    def test_route_rejects_unsafe_configuration(self):
        dashboard = controller()

        with self.assertRaises(ValueError):
            asyncio.run(dashboard.action("/api/grasp/simulate", {"max_turn_rate": 0.0}))


class GraspWebAssetsTests(unittest.TestCase):
    def test_page_declares_grasp_card_and_script(self):
        page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="graspLive"', page)
        self.assertIn('id="graspCanvas"', page)
        self.assertIn('id="graspSim"', page)
        self.assertIn('src="/grasp_panel.js"', page)
        self.assertLess(page.index('src="/grasp_panel.js"'), page.index('src="/app.js"'))

    def test_app_js_renders_grasp_panel_on_every_refresh(self):
        script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn("renderGrasp(s)", script)

    def test_panel_script_posts_to_simulate_route(self):
        script = (WEB_ROOT / "grasp_panel.js").read_text(encoding="utf-8")

        self.assertIn("/api/grasp/simulate", script)
        self.assertIn("function renderGrasp", script)
        self.assertNotIn("approach_yaw_offset_rad", script)


if __name__ == "__main__":
    unittest.main()
