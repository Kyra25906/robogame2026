"""B1 网页面板（全流程路线只读表）的接线与数据测试。

分两层：

1. **数据层**（`tools/field_dashboard_core.build_route_payload`）：真实读
   `field_layout.yaml` 并跑路线自检；自检不过或缺文件时必须
   `available: False` + 原因，**不能**退化成一份看起来正常的空表。
2. **接线层**：`index.html` 要在 `app.js` 之前加载 `route_panel.js`，`app.js`
   要在 `refresh()` 里调用 `renderRoute`。纯逻辑另有 node 测试
   （`tests/test_dashboard_route.js`，用 `node --test` 跑）。

注：本机 Windows 没有 PyYAML，所以这里同时覆盖「缺库时用兜底读取器」这条路径；
如果装了 PyYAML 则自动走 PyYAML 分支（另一条测试会比对两者结果一致）。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from robogame_core.route_loader import survey_from_layout_text
from tools.field_dashboard_core import (
    FIELD_LAYOUT_PATH,
    build_route_payload,
    load_survey,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "tools/field_dashboard_web"
ROBOT_YAML = PROJECT_ROOT / "ros2_ws/src/robogame_bringup/config/robot.yaml"


class RoutePayloadTests(unittest.TestCase):
    def test_payload_is_available_and_matches_the_plan(self):
        payload = build_route_payload()
        self.assertTrue(payload["available"], payload.get("reason"))
        self.assertEqual(len(payload["segments"]), 13)
        self.assertEqual(payload["segments"][0]["id"], "S01_LINE_START")
        self.assertEqual(payload["segments"][-1]["id"], "S13_RETREAT")
        self.assertTrue(payload["version"])
        self.assertTrue(payload["source_note"])
        self.assertEqual(
            [(turn["ref"], turn["direction"]) for turn in payload["turns"]],
            [("N02", "RIGHT"), ("N06", "LEFT")],
        )
        self.assertEqual(
            [item["ref"] for item in payload["passthrough"]], ["N03", "N09", "N09", "N06"]
        )
        self.assertEqual(
            (payload["cargo"]["orange"], payload["cargo"]["purple"], payload["cargo"]["layers"]),
            (3, 0, 2),
        )

    def test_payload_speed_limits_match_robot_yaml(self):
        payload = build_route_payload()
        text = ROBOT_YAML.read_text(encoding="utf-8")
        section = text.split("motion_controller:", 1)[1].split("\ncube_perception:", 1)[0]
        for key, limit_key in (("max_vx", "max_vx"), ("max_vy", "max_vy"), ("max_wz", "max_wz")):
            value = float(next(
                line.split(":", 1)[1] for line in section.splitlines() if line.strip().startswith(key + ":")
            ))
            self.assertEqual(payload["speed_limits"][limit_key], value)

    def test_payload_marks_pose_dependent_segments_as_estimated(self):
        """网页上必须能一眼看出哪些段依赖未标定的里程计。"""
        payload = build_route_payload()
        evidence = {row["id"]: row["evidence"] for row in payload["segments"]}
        self.assertEqual(evidence["S01_LINE_START"], "measured")
        for segment_id in ("S03_RAMP_APPROACH", "S04_RAMP_UP", "S09_RAMP_DOWN", "S11_SHIFT_TO_BUILD"):
            self.assertEqual(evidence[segment_id], "estimated", segment_id)
        self.assertGreaterEqual(sum(1 for value in evidence.values() if value == "estimated"), 5)

    def test_missing_layout_file_degrades_honestly(self):
        payload = build_route_payload(PROJECT_ROOT / "tmp" / "does_not_exist_layout.yaml")
        self.assertFalse(payload["available"])
        self.assertIn("路线数据不可用", payload["reason"])

    def test_broken_survey_degrades_honestly(self):
        """结构被改坏时不许「猜」出一份表——必须整体不可用并说明原因。"""
        broken = PROJECT_ROOT / "tmp" / "_route_broken_layout.yaml"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text(
            "survey:\n"
            "  line_nodes:\n"
            "    N01: {type: start, x: 0.7, y: 0.7}\n"
            "  line_edges: {}\n"
            "  stops: {}\n",
            encoding="utf-8",
        )
        try:
            payload = build_route_payload(broken)
        finally:
            broken.unlink()
        self.assertFalse(payload["available"])
        self.assertIn("路线数据不可用", payload["reason"])

    def test_fallback_reader_matches_pyyaml_when_available(self):
        """兜底读取器与 PyYAML 必须给出同一份 survey（否则网页会显示另一份场地）。"""
        text = FIELD_LAYOUT_PATH.read_text(encoding="utf-8")
        fallback = survey_from_layout_text(text)
        self.assertEqual(len(fallback["line_nodes"]), 11)
        self.assertEqual(len(fallback["line_edges"]), 9)
        self.assertEqual(len(fallback["stops"]), 8)
        try:
            import yaml  # type: ignore
        except ImportError:
            self.skipTest("本机没有 PyYAML，只验证了兜底读取器（Ubuntu/树莓派上会跑对比）")
        loaded = yaml.safe_load(text)["survey"]
        for section in ("line_nodes", "line_edges", "stops"):
            self.assertEqual(set(fallback[section]), set(loaded[section]), section)
            for entry_id, entry in loaded[section].items():
                parsed = fallback[section][entry_id]
                for key, value in entry.items():
                    if isinstance(value, (int, float)):
                        self.assertAlmostEqual(parsed[key], float(value), places=6, msg=f"{entry_id}.{key}")
                    else:
                        self.assertEqual(parsed[key], value, f"{entry_id}.{key}")

    def test_load_survey_uses_the_real_layout_path_by_default(self):
        survey = load_survey()
        self.assertIn("N02", survey["line_nodes"])
        self.assertEqual(FIELD_LAYOUT_PATH.name, "field_layout.yaml")


class RoutePanelWiringTests(unittest.TestCase):
    def test_index_html_loads_panel_before_app(self):
        page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="/route_panel.js"', page)
        self.assertLess(
            page.index('src="/route_panel.js"'),
            page.index('src="/app.js"'),
            "route_panel.js 必须在 app.js 之前加载，否则 renderRoute 未定义",
        )
        for element in ("routePanel", "routeSummary", "routeTurns", "routeLive"):
            self.assertIn(f'id="{element}"', page)

    def test_dashboard_subscribes_to_live_route_progress(self):
        """B2：实时段进度来自 /mission/route（mission_manager 在路线模式下上报）。"""
        text = (PROJECT_ROOT / "tools/field_dashboard.py").read_text(encoding="utf-8")
        self.assertIn('"/mission/route"', text)
        self.assertIn('"mission_route"', text)

    def test_app_js_calls_render_route(self):
        script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("renderRoute(s)", script)
        self.assertLess(
            script.index("renderCalibration(s)"),
            script.index("renderRoute(s)"),
            "renderRoute 应挂在同一处 refresh 渲染里",
        )

    def test_panel_js_declares_pure_view_and_export(self):
        script = (WEB_ROOT / "route_panel.js").read_text(encoding="utf-8")
        self.assertIn("function routePanelView(", script)
        self.assertIn("function routeLiveView(", script)
        self.assertIn("module.exports", script)
        # 面板只读：不许出现任何 POST/控制类调用
        for forbidden in ("fetch(", "api(", "XMLHttpRequest"):
            self.assertNotIn(forbidden, script, f"只读面板不应出现 {forbidden}")

    def test_server_actually_serves_the_panel_file(self):
        """真网页路径：页面里引用的 route_panel.js 必须真的能从 HTTP 服务取到。"""
        import asyncio
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.request import urlopen

        from tools.field_dashboard import DashboardHandler

        class _App:
            def snapshot(self):
                return {"mode": "OBSERVE"}

            def record(self, event, **_kwargs):
                pass

        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()
        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        server.app = _App()
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/route_panel.js", timeout=3) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
            self.assertIn("routePanelView", body)
        finally:
            server.shutdown()
            server.server_close()
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
