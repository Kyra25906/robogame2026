"""集成就绪审计的测试（静态跨文件一致性）。

两层：

1. **真实仓库必须干净**：跑一遍 `audit()`，blocker 必须为 0；risk/info 必须等于
   登记集合（当前为空）——新增风险必须显式登记，不能被无声吞掉。
2. **每条检查必须真的会响**：在临时目录里造一个「最小仓库」，然后逐项破坏，确认
   对应 finding 出现。没有这一层，审计就可能是个永远通过的摆设。

被审计的对象包括：授权来源是否真的被节点执行、段退出判据的输入是否被订阅、
网页读的字段是否真的在载荷里、现场图是否包含会被授权的节点、现场配置是否写清参数。
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from tools.integration_audit import (
    Finding,
    audit,
    format_report,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "ros2_ws" / "src"
WEB_ROOT = PROJECT_ROOT / "tools" / "field_dashboard_web"

#: 已登记的 risk/info 发现（空 = 当前全部对齐）。新增必须显式写在这里并说明原因。
REGISTERED_RISKS = set()

MISSION_NODE = '''
class MissionManagerNode:
    def __init__(self):
        self.create_subscription(RobotStatus, "/robot/status", self._on_status, 10)
        self.create_subscription(String, "/motion/result", self._on_motion, 10)
        self.create_subscription(String, "/manipulator/result", self._on_manip, 10)
        self.create_subscription(String, "/line_follow/status", self._on_line, 10)
        self.create_subscription(Odometry, "/pose", self._on_pose, 20)
        self.route_pub = self.create_publisher(String, "/mission/route", 10)
        self.authority_pub = self.create_publisher(String, "/mission/active_source", 10)

    def _publish_route_status(self):
        payload = {"segment_id": self.segment_id, "rounds": 1, "work_step": ""}
        return payload

    def route_progress(self):
        return {"match_remaining_s": 1.0, "degradations": 0}
'''

AUTHORIZED_NODE = '''
class SomeNode:
    def __init__(self):
        self.create_subscription(String, "/mission/active_source", self._on_auth, 10)
'''

UNAUTHORIZED_NODE = '''
class SomeNode:
    def __init__(self):
        self.create_publisher(Twist, "/cmd_vel", 20)
'''

FIELD_LAUNCH = """
def generate_launch_description():
    return LaunchDescription([
        Node(package="motion_control", executable="motion_controller"),
        Node(package="motion_control", executable="line_follow_controller"),
        Node(package="manipulator_client", executable="manipulator_client"),
    ])
"""

FIELD_CONFIG = """
mission_manager:
  ros__parameters:
    route_enabled: true
    field_layout_path: ""
    degrade_on_failure: true
    match_time_limit_s: 360.0
line_follow_controller:
  ros__parameters:
    calibration_file: ~/cal.json
"""


class IntegrationAuditTests(unittest.TestCase):
    def test_real_repository_has_no_blockers(self):
        findings = audit(SRC_ROOT, web_root=WEB_ROOT)
        blockers = [finding for finding in findings if finding.severity == "blocker"]
        self.assertEqual(
            blockers, [],
            "集成就绪审计发现 blocker：\n" + format_report(blockers),
        )

    def test_registered_risks_match_exactly(self):
        findings = audit(SRC_ROOT, web_root=WEB_ROOT)
        risks = {finding.code for finding in findings if finding.severity in ("risk", "info")}
        self.assertEqual(
            risks, REGISTERED_RISKS,
            f"risk/info 发现与登记不一致：{sorted(risks)} != {sorted(REGISTERED_RISKS)}",
        )

    def test_report_is_readable_when_clean(self):
        self.assertIn("未发现问题", format_report([]))
        self.assertIn("blocker", format_report([Finding("x", "blocker", "d")]))


class SyntheticTreeMixin:
    """造一个最小仓库；每个测试只破坏一个方面。"""

    def build_tree(self, *, authorized_nodes=("line_follow", "motion_control", "manipulator_client"),
                   mission_subscriptions=None, launch_nodes=None, config_params=None,
                   panel_keys=("segment_id",)) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "ros2_ws" / "src"
        (root / "mission_manager" / "mission_manager").mkdir(parents=True)
        (root / "motion_control" / "motion_control").mkdir(parents=True)
        (root / "manipulator_client" / "manipulator_client").mkdir(parents=True)
        (root / "robogame_bringup" / "launch").mkdir(parents=True)
        (root / "robogame_bringup" / "config").mkdir(parents=True)
        web = Path(self._tmp.name) / "tools" / "field_dashboard_web"
        web.mkdir(parents=True)

        mission_source = MISSION_NODE
        if mission_subscriptions is not None:
            for topic in mission_subscriptions:
                # 主题名替换（与消息类型无关，避免只匹配 String 订阅）
                mission_source = mission_source.replace(f'"{topic}"', '"/REMOVED"')
        (root / "mission_manager" / "mission_manager" / "node.py").write_text(
            mission_source, encoding="utf-8"
        )

        for name, relative in (
            ("line_follow", "motion_control/motion_control/line_follow_node.py"),
            ("motion_control", "motion_control/motion_control/node.py"),
            ("manipulator_client", "manipulator_client/manipulator_client/node.py"),
        ):
            source = AUTHORIZED_NODE if name in authorized_nodes else UNAUTHORIZED_NODE
            (root / relative).write_text(source, encoding="utf-8")

        launch = FIELD_LAUNCH
        if launch_nodes is not None:
            for executable in launch_nodes:
                launch = launch.replace(f'executable="{executable}"', 'executable="REMOVED"')
        (root / "robogame_bringup" / "launch" / "hardware.launch.py").write_text(
            launch, encoding="utf-8"
        )

        config = FIELD_CONFIG
        if config_params is not None:
            for param in config_params:
                config = config.replace(f"    {param}:", f"    # {param}:")
        (root / "robogame_bringup" / "config" / "robot_field.yaml").write_text(
            config, encoding="utf-8"
        )

        keys = ", ".join(f"data.{key}" for key in panel_keys)
        (web / "route_panel.js").write_text(
            f"function view(data) {{ return [{keys}]; }}\n", encoding="utf-8"
        )
        self._web = web
        return root

    def tearDown(self):
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def run_audit(self, root: Path) -> list[Finding]:
        return audit(root, web_root=self._web)


class SyntheticTreeBaselineTests(SyntheticTreeMixin, unittest.TestCase):
    def test_clean_synthetic_tree_has_no_findings(self):
        """先证明「最小仓库」本身是干净的，后面每个负例才有意义。"""
        self.assertEqual(self.run_audit(self.build_tree()), [])


class DetectorTests(SyntheticTreeMixin, unittest.TestCase):
    def test_missing_authorization_subscription_is_a_blocker(self):
        root = self.build_tree(authorized_nodes=("line_follow", "motion_control"))
        codes = [finding.code for finding in self.run_audit(root)]
        self.assertIn("authority_not_enforced", codes)
        finding = next(f for f in self.run_audit(root) if f.code == "authority_not_enforced")
        self.assertEqual(finding.severity, "blocker")
        self.assertIn("manipulator_client", finding.detail)

    def test_missing_exit_kind_input_is_a_blocker(self):
        root = self.build_tree(mission_subscriptions=("/pose",))
        codes = [finding.code for finding in self.run_audit(root)]
        self.assertIn("exit_kind_without_input", codes)

    def test_missing_step_result_input_is_a_blocker(self):
        root = self.build_tree(mission_subscriptions=("/manipulator/result",))
        codes = [finding.code for finding in self.run_audit(root)]
        self.assertIn("step_source_without_input", codes)

    def test_panel_reading_a_missing_field_is_a_risk(self):
        root = self.build_tree(panel_keys=("segment_id", "work_step_typo"))
        findings = self.run_audit(root)
        self.assertIn("panel_key_missing", [finding.code for finding in findings])
        finding = next(f for f in findings if f.code == "panel_key_missing")
        self.assertEqual(finding.severity, "risk")
        self.assertIn("work_step_typo", finding.detail)

    def test_panel_keys_covered_by_route_progress_are_not_reported(self):
        """`**progress` 带出来的键也算存在（否则会误报）。"""
        root = self.build_tree(panel_keys=("degradations", "match_remaining_s"))
        self.assertNotIn(
            "panel_key_missing", [finding.code for finding in self.run_audit(root)]
        )

    def test_authorized_node_missing_from_field_launch_is_a_blocker(self):
        root = self.build_tree(launch_nodes=("line_follow_controller",))
        codes = [finding.code for finding in self.run_audit(root)]
        self.assertIn("authority_node_not_launched", codes)

    def test_field_config_without_a_key_param_is_reported(self):
        """配置里缺关键参数（或被注释掉）要报出来——注意**注释不算写了**。"""
        root = self.build_tree(config_params=("match_time_limit_s",))
        codes = [finding.code for finding in self.run_audit(root)]
        self.assertIn("field_param_missing", codes)
        finding = next(f for f in self.run_audit(root) if f.code == "field_param_missing")
        self.assertIn("match_time_limit_s", finding.detail)

    def test_launch_provided_params_are_not_reported_as_missing(self):
        """参数由 launch 内联提供也算写清了（真实仓库的 field_layout_path 就是这种）。"""
        real = audit(SRC_ROOT, web_root=WEB_ROOT)
        self.assertNotIn("field_param_missing", [finding.code for finding in real])

    def test_missing_mission_node_is_reported(self):
        root = self.build_tree()
        (root / "mission_manager" / "mission_manager" / "node.py").unlink()
        codes = [finding.code for finding in self.run_audit(root)]
        self.assertIn("mission_node_missing", codes)

    def test_missing_field_launch_is_reported(self):
        root = self.build_tree()
        (root / "robogame_bringup" / "launch" / "hardware.launch.py").unlink()
        codes = [finding.code for finding in self.run_audit(root)]
        self.assertIn("field_launch_missing", codes)


class ManipulatorGateWiringTests(unittest.TestCase):
    """审计发现的 blocker `authority_not_enforced` 的修复必须被钉住。

    路线在作业段把底盘授权给 `manipulator_client`（视觉对准要动底盘），
    而那个节点原先**完全不看授权**——也就是「唯一授权」这条设计有一个洞。
    """

    @classmethod
    def setUpClass(cls):
        cls.path = (
            PROJECT_ROOT / "ros2_ws/src/manipulator_client/manipulator_client/node.py"
        )
        cls.text = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.text)

    def _function(self, name: str) -> ast.FunctionDef:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"function {name} not found")

    def test_subscribes_to_authorization(self):
        self.assertIn("/mission/active_source", self.text)
        self.assertIn("_on_authorization", self.text)

    def test_uses_the_shared_authorization_rule(self):
        self.assertIn("from robogame_core.authorization import AuthorizationState", self.text)
        self.assertIn("SOURCE_ALIGN", self.text)
        self.assertIn("AuthorizationState", ast.unparse(self._function("__init__")))

    def test_default_keeps_standalone_smoke_working(self):
        """默认 false：机构 smoke 图里没有任务层，不能要求授权。"""
        self.assertIn('"require_authorization": False', self.text)

    def test_alignment_output_is_gated(self):
        """对准命令必须先过授权；未授权时停车而不是继续对准。"""
        # 对准结果发布 /cmd_vel 的那段代码里必须出现授权检查
        source = self.text
        publish_index = source.rindex("self.cmd_pub.publish(msg)")
        window = source[max(0, publish_index - 900):publish_index]
        self.assertIn("_authorized", window, "发布对准速度之前必须先看授权")

    def test_release_publishes_one_zero(self):
        stop = ast.unparse(self._function("_publish_stop"))
        self.assertIn("cmd_pub.publish", stop)
        self.assertIn("was_driving", ast.unparse(self._function("__init__")))


if __name__ == "__main__":
    unittest.main()
