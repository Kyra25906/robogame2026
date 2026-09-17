"""机械臂联调面板的接线测试（协议/接口/网页资产）。

本机（Windows，无 ROS 2）不能 import `tools.field_dashboard`，所以对节点这部分
沿用项目既有做法：从源码做结构断言。**判定逻辑**由 `tests/test_arm_selftest.py`
用注入的假 ROS 覆盖，本文件只查“线有没有接上、失败时会不会优雅降级”。

证据边界：结构断言只证明源码里存在这些路由与资产，不证明页面在浏览器里可用、
不证明 ROS 图跑得起来。
"""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "tools" / "field_dashboard.py"
WEB = ROOT / "tools" / "field_dashboard_web"
ARM_PANEL = WEB / "arm_panel.js"
INDEX = WEB / "index.html"
INTERFACE = ROOT / "ros2_ws" / "src" / "robogame_interfaces" / "srv" / "SetArmJoint.srv"


class ArmPanelAssetTests(unittest.TestCase):
    def test_panel_script_exists_and_is_loaded_by_the_page(self):
        self.assertTrue(ARM_PANEL.is_file(), "arm_panel.js 缺失")
        self.assertIn("/arm_panel.js", INDEX.read_text(encoding="utf-8"))

    def test_panel_calls_the_two_arm_endpoints(self):
        source = ARM_PANEL.read_text(encoding="utf-8")
        self.assertIn("/api/arm/selftest", source)
        self.assertIn("/api/arm/set_joint", source)

    def test_panel_states_the_open_loop_limit(self):
        # 纯开环：页面必须自己说清 PASS 不等于舵机到位，否则现场会误读证据。
        source = ARM_PANEL.read_text(encoding="utf-8")
        self.assertIn("纯开环", source)
        self.assertIn("不代表舵机真的到位", source)


class ArmEndpointWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DASHBOARD.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_arm_routes_are_dispatched_to_the_arm_handler(self):
        self.assertIn('{"/api/arm/set_joint", "/api/arm/selftest"}', self.source)
        self.assertIn("await self._arm_action(path, body)", self.source)

    def test_selftest_gets_a_longer_http_budget(self):
        # 抓→放→复位要等好几个机构动作，默认 12 s 不够。
        self.assertIn('timeout=90 if path == "/api/arm/selftest" else 12', self.source)

    def test_set_arm_joint_import_is_optional(self):
        # 树莓派可能还没重新 colcon build：缺接口不能拖垮整个网页。
        self.assertIn("from robogame_interfaces.srv import SetArmJoint", self.source)
        self.assertIn("SetArmJoint = None", self.source)
        self.assertIn("self.arm_interface_missing = SetArmJoint is None", self.source)

    def test_arm_handler_refuses_while_the_chassis_is_moving(self):
        handler = self._method_source("_arm_action")
        self.assertIn("self.state.mode != \"OBSERVE\"", handler)
        self.assertIn("C-4", handler)

    def test_arm_handler_guards_busy_and_safety_blockers(self):
        handler = self._method_source("_arm_action")
        self.assertIn("self.mechanism_busy", handler)
        self.assertIn("action_blockers(", handler)

    def test_arm_handler_validates_joint_and_angle(self):
        handler = self._method_source("_arm_action")
        self.assertIn("0 <= joint <= 4", handler)
        self.assertIn("math.isfinite(angle_deg)", handler)
        self.assertIn("math.isfinite(timeout_s)", handler)

    def test_selftest_never_raises_when_ros_is_missing(self):
        # ros 为 None 时返回“ROS 客户端未就绪”的失败表，而不是 HTTP 500。
        handler = self._method_source("_selftest_runner")
        self.assertIn("ros = self.ros", handler)
        self.assertNotIn("self._require_ros()", handler)

    def test_results_are_archived_for_the_evidence_log(self):
        handler = self._method_source("_arm_action")
        self.assertIn('"type": "arm_selftest"', handler)
        self.assertIn('"type": "arm"', handler)

    def _method_source(self, name):
        for node in ast.walk(self.tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                return ast.get_source_segment(self.source, node)
        self.fail(f"method {name} not found")


class ArmInterfaceTests(unittest.TestCase):
    def test_service_file_is_the_frozen_shape(self):
        request, response = INTERFACE.read_text(encoding="utf-8").split("---", 1)
        request_names = [
            line.split()[1] for line in request.splitlines() if line.strip()
        ]
        response_names = [
            line.split()[1] for line in response.splitlines() if line.strip()
        ]
        self.assertEqual(request_names, ["joint", "angle_deg", "timeout_s"])
        self.assertEqual(
            response_names, ["success", "error_code", "duration_s", "detail"]
        )


if __name__ == "__main__":
    unittest.main()
