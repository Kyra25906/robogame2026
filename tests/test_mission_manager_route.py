"""B2 接线测试：任务层路线模式、唯一底盘授权、整栈启动配置。

本机（Windows）没有 rclpy，节点行为无法直接跑（rclpy 行为测试默认 skip），
所以这里用仓库既有做法——**AST 结构断言 + 配置文件绑定断言**，把「危险接线」
钉死；纯逻辑部分（分发决策、状态解析、路径解析）另有行为测试。

要钉住的四件事：

1. **授权唯一**：任务层是唯一授权广播者；只有被授权的来源才发非零速度，
   未被授权时保持沉默（持续发零速会和真正在驱动的节点抢 /cmd_vel）。
2. **转向/平移先给目标再授权**：否则位姿控制器会在没有目标时被授权而空转。
3. **失败要响亮**：路线加载/自检失败必须判失败并停车，绝不退回演示流程。
4. **比赛配置真的打开了这些开关**：robot.yaml / robot_field.yaml /
   hardware.launch.py 三处必须同时到位，少一处车就不会按路线跑。
"""

from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "ros2_ws" / "src"
CONFIG_DIR = SRC_ROOT / "robogame_bringup" / "config"
LAUNCH_DIR = SRC_ROOT / "robogame_bringup" / "launch"
MISSION_NODE = SRC_ROOT / "mission_manager" / "mission_manager" / "node.py"
MOTION_NODE = SRC_ROOT / "motion_control" / "motion_control" / "node.py"
LINE_NODE = SRC_ROOT / "motion_control" / "motion_control" / "line_follow_node.py"

sys_path_root = Path(__file__).resolve().parents[1]
import sys  # noqa: E402  （放在文件后部便于阅读路径常量，导入顺序由下面统一处理）

if str(sys_path_root) not in sys.path:
    sys.path.insert(0, str(sys_path_root))

# 注意：这里**不导入** `mission_manager.node`——它依赖 rclpy，开发机上没有。
# 该节点只能用 AST 检查（见 MissionManagerRouteWiringTests）；纯逻辑部分住在
# `robogame_core.route_loader`（零 ROS），可以直接行为测试。
from robogame_core.route_loader import (  # noqa: E402
    DEFAULT_LAYOUT_RELATIVE_PATH,
    load_route_plan,
    resolve_field_layout_path,
)
from robogame_core.cmd_vel_arbiter import SOURCE_LINE_FOLLOW  # noqa: E402
from tools.launch_params import effective_parameter  # noqa: E402

# 仲裁器里的另一个合法来源（用于验证「未授权来源不得驱动底盘」）
SOURCE_MISSION_OTHER = "motion_control"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def _calls(tree: ast.AST) -> list[tuple[str, tuple]]:
    """(被调用名, 位置参数字面量) 列表，用于断言话题名与调用顺序。"""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
            elif isinstance(node.func, ast.Name):
                name = node.func.id
            literals = tuple(
                arg.value for arg in node.args if isinstance(arg, ast.Constant)
            )
            found.append((name, literals))
    return found


class MissionManagerRouteWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = _tree(MISSION_NODE)

    def test_subscribes_to_the_route_mode_inputs(self):
        calls = _calls(self.tree)
        for topic in ("/line_follow/status", "/pose", "/robot/status",
                      "/motion/result", "/manipulator/result"):
            self.assertTrue(
                any(name == "create_subscription" and topic in literals for name, literals in calls),
                f"mission_manager 必须订阅 {topic}",
            )

    def test_publishes_authority_and_route_progress(self):
        calls = _calls(self.tree)
        for topic in ("/mission/active_source", "/mission/route", "/motion/goal",
                      "/manipulator/command", "/mission/state", "/mission/cargo"):
            self.assertTrue(
                any(name == "create_publisher" and topic in literals for name, literals in calls),
                f"mission_manager 必须发布 {topic}",
            )

    def test_start_gate_receives_the_same_verdict_in_both_flows(self):
        """开赛门在路线模式与演示模式里必须用**同一个判据函数**。

        演示模式保留在节点里（没有 `MissionRun`），如果那边不喂观测，
        同一个 `require_line_calibration: true` 在两条流程下就会有两种含义：
        一条按标定状态判，另一条永远判「未上报」——现场只会看到「演示模式跑不了」。
        """
        tick = ast.unparse(_function(self.tree, "_tick"))
        self.assertIn("calibration_readiness", tick, "演示模式的分支也必须喂标定三态")

        def demo_branch_source() -> str:
            """取出 `_tick` 里 `if self.run is not None: ... else: <演示模式>` 的 else 分支。

            为什么按 AST 取分支而不是按注释文字切：`ast.unparse` 会丢掉注释，
            以前那样切会直接 IndexError（这次就踩到了）。
            """
            for node in ast.walk(_function(self.tree, "_tick")):
                if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
                    continue
                test = ast.unparse(node.test)
                if "self.run" in test and "None" in test:
                    return ast.unparse(ast.Module(body=node.orelse, type_ignores=[]))
            raise AssertionError("_tick 里找不到 'if self.run is not None: ... else: ...' 分支")

        self.assertIn("line_calibration_ready", demo_branch_source())
        # 判据函数来自共享模块，不是节点里重写的
        self.assertIn(
            "from robogame_core.mission_run import",
            MISSION_NODE.read_text(encoding="utf-8"),
        )

    def test_node_tracks_the_last_line_status_for_the_gate(self):
        """演示模式也要留一份最近状态；否则判据永远拿到 None。"""
        handler = ast.unparse(_function(self.tree, "_on_line_status"))
        self.assertIn("self.line_status", handler)
        self.assertIn("self.line_status_time", handler)
        init = ast.unparse(_function(self.tree, "__init__"))
        self.assertIn("self.line_status_time = 0.0", init, "必须是 0.0（=从未收到），不能是 now")

    def test_every_command_kind_has_a_publisher(self):
        """`CommandKind` 与发布者的映射必须完整——漏一个就等于命令被静默丢弃。

        运行逻辑（顺序、重发、步骤）由 `tests/test_mission_run.py` 的**行为测试**
        覆盖；节点这一层只负责「按类型选发布者」，所以这里查映射完整性。
        """
        mapping = ast.unparse(_function(self.tree, "_publish_command"))
        for kind, publisher in (
            ("AUTHORITY", "authority_pub"),
            ("GOAL", "goal_pub"),
            ("MANIPULATOR", "manip_pub"),
            ("LINE", "line_pub"),
            ("TURN", "turn_pub"),
            ("STOP", "stop_pub"),
        ):
            self.assertIn(f"CommandKind.{kind}", mapping, kind)
            self.assertIn(publisher, mapping, kind)

    def test_node_delegates_the_run_loop(self):
        """节点不再自己算逻辑：订阅 → 调 MissionRun → 发布命令。"""
        tick = ast.unparse(_function(self.tree, "_tick"))
        self.assertIn("self.run.tick", tick)
        self.assertIn("_publish_command", tick)
        self.assertIn("self.run.payload", tick)
        for handler, call in (
            ("_on_line_status", "observe_line_status"),
            ("_on_pose", "observe_pose"),
            ("_handle_result", "handle_result"),
        ):
            body = ast.unparse(_function(self.tree, handler))
            self.assertIn(f"self.run.{call}", body, handler)

    def test_node_publishes_stop_in_terminal_states(self):
        tick = ast.unparse(_function(self.tree, "_tick"))
        self.assertIn("stop_pub.publish", tick)
        self.assertIn("SAFE_STOP", tick)

    def test_route_error_fails_loudly_instead_of_falling_back(self):
        function = _function(self.tree, "_tick")
        self.assertIn("route_error", ast.unparse(function))
        self.assertIn("fail", ast.unparse(function))
        load = _function(self.tree, "_load_route")
        text = ast.unparse(load)
        self.assertIn("route_error", text)
        # 失败路径必须 return（不许继续把 route 设成别的东西）
        returns_early = any(isinstance(node, ast.Return) for node in ast.walk(load))
        self.assertTrue(returns_early, "路线加载失败必须提前返回（不设 route）")

    def test_stale_line_status_timeout_is_configurable(self):
        """观察逻辑（断流 → 不推进段）在 MissionRun 里，由行为测试覆盖；
        节点这一层必须把它作为参数暴露出来，现场才能调。"""
        text = MISSION_NODE.read_text(encoding="utf-8")
        self.assertIn('"line_status_timeout_s"', text)
        self.assertIn("MissionRun(", text)

    def test_legacy_mode_never_publishes_authority(self):
        """演示模式（route_enabled=false）不得广播授权：否则会改变既有 mock 流程。"""
        legacy_dispatch = ast.unparse(_function(self.tree, "_dispatch_demo"))
        self.assertNotIn("authority_pub", legacy_dispatch)


class GatingWiringTests(unittest.TestCase):
    """两个运动节点都必须遵守 /mission/active_source 授权。"""

    @classmethod
    def setUpClass(cls):
        cls.motion = _tree(MOTION_NODE)
        cls.line = _tree(LINE_NODE)
        cls.motion_text = MOTION_NODE.read_text(encoding="utf-8")
        cls.line_text = LINE_NODE.read_text(encoding="utf-8")

    def test_both_nodes_subscribe_to_authorization(self):
        for tree, label in ((self.motion, "motion_control"), (self.line, "line_follow")):
            self.assertTrue(
                any(
                    name == "create_subscription" and "/mission/active_source" in literals
                    for name, literals in _calls(tree)
                ),
                f"{label} 必须订阅 /mission/active_source",
            )

    def test_motion_control_checks_authorization_before_any_command(self):
        function = _function(self.motion, "_tick")
        text = ast.unparse(function)
        self.assertIn("_authorized", text)
        # 授权检查必须在「发布速度」之前（AST 顺序 == 执行顺序）
        self.assertLess(text.index("_authorized"), text.index("cmd_pub.publish"))

    def test_motion_control_stops_once_then_stays_silent(self):
        function = _function(self.motion, "_tick")
        text = ast.unparse(function)
        self.assertIn("was_driving", text)
        self.assertIn("_stop()", text)
        # 未授权分支必须 return，不许继续往下走控制律
        self.assertIn("return", text)

    def test_authorization_rules_live_in_the_shared_module(self):
        """三个运动节点必须共用同一份授权规则（分叉 = 某个节点可能不受约束）。

        规则实现已下沉到 `robogame_core.authorization`（见集成审计发现的
        authority_not_enforced：manipulator_client 原来看都不看授权）。
        """
        for tree, label, source in (
            (self.motion, "motion_control", "SOURCE_NAVIGATE"),
            (self.line, "line_follow", None),
        ):
            text = ast.unparse(tree)
            self.assertIn("AuthorizationState", text, label)
            self.assertIn("require_authorization", text, label)
            self.assertIn("authorization_stale_s", text, label)
            if source is not None:
                self.assertIn(source, ast.unparse(_function(tree, "_authorized")), label)

    def test_line_follow_stays_silent_when_unauthorized(self):
        function = _function(self.line, "_publish")
        text = ast.unparse(function)
        self.assertIn("allowed_sources", text)
        self.assertIn("was_driving", text)
        # 未授权且本来没在驱动 → 直接返回（不发零速抢话题）
        self.assertIn("return", text)

    def test_defaults_keep_standalone_debugging_working(self):
        """代码默认 require_authorization=False：独立联调（无任务层）行为不变。"""
        for tree, label in ((self.motion, "motion_control"), (self.line, "line_follow")):
            defaults = ast.unparse(tree)
            self.assertRegex(
                defaults,
                r"['\"]require_authorization['\"]:\s*False",
                f"{label} 的代码默认值必须是不要求授权（独立联调不受影响）",
            )


class CompetitionConfigBindingTests(unittest.TestCase):
    """比赛配置三处必须同时到位，否则车不会按路线跑。

    这里断言的是**最终生效值**（层顺序 + 内联覆盖），不是「文件里出现过某个
    字符串」——文本级断言查不出「共用层打开了门控 → 独立联调图一动不动」这类
    跨文件语义冲突（2026-09-17 真实踩过）。
    """

    CONFIG_ROOT = SRC_ROOT / "robogame_bringup" / "config"

    def _effective(self, launch: str, package: str, executable: str, param: str):
        return effective_parameter(
            LAUNCH_DIR / launch, package, executable, param
        )

    def test_competition_stack_enforces_single_authority(self):
        """hardware.launch.py 里两个运动节点都必须开着授权门控。"""
        for package, executable in (
            ("motion_control", "motion_controller"),
            ("motion_control", "line_follow_controller"),
        ):
            self.assertIs(
                self._effective("hardware.launch.py", package, executable, "require_authorization"),
                True,
                f"{executable} 在比赛图里必须要求授权",
            )

    def test_standalone_line_graphs_do_not_require_authorization(self):
        """没有任务层的图必须关掉门控（或干脆不配置，用节点默认 false）。

        `None` = 没有任何一层写过这个参数 → 用节点代码默认值 `False` → 同样安全。
        """
        for launch in ("line_follow_hardware.launch.py", "line_follow_mock.launch.py"):
            effective = self._effective(
                launch, "motion_control", "line_follow_controller", "require_authorization"
            )
            self.assertIn(
                effective, (False, None),
                f"{launch} 没有任务层：巡线节点不能要求授权（实际解析为 {effective!r}）",
            )

    def test_gate_is_an_environment_property_not_a_shared_default(self):
        """授权门控属于**环境**，只能写在环境层（robot_field.yaml）。

        为什么这是一条硬规矩（2026-09-17 踩过两次，第二次是网页按钮）：
        共用层 `robot.yaml` 也被**手动联调**加载——网页「启动底盘链路 / 启动巡线」
        就是用 `tools/field_console.json` 里的命令启动节点，而那条命令**只带
        robot.yaml**。此时没有任何任务层在广播授权，若门控在共用层打开，
        节点会**永远不动、并且不打任何日志**（`motion_controller` 原先就是静默返回）。
        """
        common = (self.CONFIG_ROOT / "robot.yaml").read_text(encoding="utf-8")
        self.assertNotIn(
            "require_authorization: true", common,
            "共用层不能打开授权门控：手动联调的节点会永远不动",
        )
        field = (self.CONFIG_ROOT / "robot_field.yaml").read_text(encoding="utf-8")
        for node in ("motion_controller", "line_follow_controller"):
            section = field.split(f"{node}:", 1)
            self.assertEqual(len(section), 2, f"robot_field.yaml 缺少 {node} 段落")
            self.assertIn(
                "require_authorization: true", section[1].split("\n\n")[0] + section[1][:400],
                f"{node} 必须在环境层打开授权门控（整栈里任务层是唯一授权来源）",
            )

    def test_web_console_processes_only_load_the_common_layer(self):
        """网页按钮启动的节点只能拿到共用层——这正是上面那条规矩的理由。

        如果哪天有人给网页按钮加了 field 层，门控就会在手动联调时生效，
        这个测试必须红，逼人把这个决定想清楚。
        """
        import json

        console = json.loads(
            (PROJECT_ROOT / "tools/field_console.json").read_text(encoding="utf-8")
        )
        watched = {"motion": "motion_controller", "line": "line_follow_controller"}
        for process in console["processes"]:
            if process["name"] not in watched:
                continue
            self.assertNotIn(
                "robot_field.yaml", process["command"],
                f"网页「{process['name']}」按钮不应加载比赛层（会让手动联调启动的节点等授权）",
            )

    def test_field_config_enables_the_route(self):
        text = (self.CONFIG_ROOT / "robot_field.yaml").read_text(encoding="utf-8")
        self.assertIn("mission_manager:", text)
        self.assertIn("route_enabled: true", text)

    def test_field_launch_wires_route_inputs_and_line_controller(self):
        text = (LAUNCH_DIR / "hardware.launch.py").read_text(encoding="utf-8")
        self.assertIn('executable="line_follow_controller"', text)
        self.assertIn("field_layout_path", text)
        # mission_manager 必须带 field 层（robot_field.yaml 里有它的段落）
        mission_block = text.split('executable="mission_manager"', 1)[1].split("),", 1)[0]
        self.assertIn("common", mission_block)
        self.assertIn("field", mission_block)
        # 巡线节点也必须带 field 层（授权门控在 field 层打开）
        line_block = text.split('executable="line_follow_controller"', 1)[1].split("),", 1)[0]
        self.assertIn("field", line_block)

    def test_demo_configs_stay_in_legacy_mode(self):
        for name in ("robot_mock.yaml", "single_cube.yaml"):
            text = (self.CONFIG_ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("route_enabled: true", text, name)

    def test_field_config_enforces_the_line_calibration_gate(self):
        """B4：正式场地必须在**最终生效值**上要求巡线标定可用。

        断言生效值而不是文本：比赛图的内联参数能覆盖 yaml，
        只查字符串会出现「yaml 写了 true 但图里被覆盖成 false」的假通过。
        """
        self.assertIs(
            self._effective(
                "hardware.launch.py", "mission_manager", "mission_manager",
                "require_line_calibration",
            ),
            True,
            "正式场地配置必须开着开赛门（上电自主时没人会在赛前检查标定）",
        )

    def test_demo_configs_do_not_require_line_calibration(self):
        """mock 演示不接真线：不能因为缺标定就让演示流程起不来。"""
        for launch in ("mock_demo.launch.py", "single_cube.launch.py"):
            effective = self._effective(
                launch, "mission_manager", "mission_manager", "require_line_calibration"
            )
            self.assertIn(
                effective, (False, None),
                f"{launch} 不该要求标定（实际解析为 {effective!r}）",
            )


class RouteLoadingTests(unittest.TestCase):
    """路径解析与路线加载（纯逻辑，可在开发机验证）。"""

    def test_explicit_path_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "layout.yaml"
            path.write_text("survey: {}\n", encoding="utf-8")
            self.assertEqual(resolve_field_layout_path(str(path)), str(path))

    def test_share_root_candidate_is_used_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            (share / "config").mkdir()
            (share / "config" / "field_layout.yaml").write_text("survey: {}\n", encoding="utf-8")
            self.assertEqual(
                resolve_field_layout_path("", share_root=str(share)),
                str(share / "config" / "field_layout.yaml"),
            )

    def test_missing_share_falls_back_to_the_repo_path(self):
        """没装 robogame_bringup 的开发机：回落到仓库相对路径（并确实存在）。"""
        with tempfile.TemporaryDirectory() as tmp:
            result = resolve_field_layout_path("", share_root=tmp)
            self.assertEqual(result, DEFAULT_LAYOUT_RELATIVE_PATH)
            self.assertTrue(Path(result).is_file())

    def test_nothing_found_returns_empty_string(self):
        """一个候选都不存在时必须返回空串——调用方据此判「路线不可用」。"""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                resolve_field_layout_path("", share_root=tmp, fallback_paths=()), ""
            )

    def test_empty_path_is_a_hard_error(self):
        with self.assertRaises(FileNotFoundError):
            load_route_plan("")

    def test_real_layout_loads_thirteen_segments(self):
        path = resolve_field_layout_path(
            "", share_root=str(PROJECT_ROOT / "ros2_ws/src/robogame_bringup")
        )
        plan = load_route_plan(path)
        self.assertEqual(len(plan.segments), 13)
        self.assertEqual(plan.segment_ids[0], "S01_LINE_START")
        self.assertTrue(plan.version)

    def test_broken_layout_is_rejected_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.yaml"
            path.write_text("survey:\n  line_nodes:\n    N01: {type: start, x: 0.7, y: 0.7}\n", encoding="utf-8")
            with self.assertRaises(Exception):
                load_route_plan(str(path))

    def test_node_reads_the_layout_path_parameter(self):
        text = MISSION_NODE.read_text(encoding="utf-8")
        self.assertIn('"field_layout_path"', text)
        self.assertIn("resolve_field_layout_path", text)
        self.assertIn("load_route_plan", text)
        # 参数默认值必须是空串（空串 = 自动查找），不许写死某台机器的路径
        self.assertRegex(text, r'"field_layout_path":\s*""')

    def test_node_delegates_loading_to_the_core_loader(self):
        """节点不许自己实现 yaml 解析：三处（节点/网页/测试）必须共用一份实现。"""
        text = MISSION_NODE.read_text(encoding="utf-8")
        self.assertIn("from robogame_core.route_loader import", text)
        self.assertNotIn("safe_load", text)
        self.assertNotIn("survey_from_layout_text", text)


class TurnWiringTests(unittest.TestCase):
    """B3：转弯命令的端到端接线（任务层下发 → 巡线节点执行 → 网页可见）。

    节点行为在本机跑不了（无 rclpy），所以用 AST 结构断言把**危险的那几条**钉死：
    失败后不许回退 PD、未授权不许推进转弯、终态必须清除待执行转弯。
    """

    @classmethod
    def setUpClass(cls):
        cls.mission = _tree(MISSION_NODE)
        cls.line = _tree(LINE_NODE)
        cls.line_text = LINE_NODE.read_text(encoding="utf-8")

    def test_mission_manager_publishes_turn_commands(self):
        """转弯命令的内容与「重发/清除」时机在 MissionRun 里（行为测试覆盖）；
        节点这一层必须能把 TURN 命令发出去，并且有对应发布者。"""
        calls = _calls(self.mission)
        self.assertTrue(
            any(name == "create_publisher" and "/mission/turn" in literals for name, literals in calls)
        )
        mapping = ast.unparse(_function(self.mission, "_publish_command"))
        self.assertIn("CommandKind.TURN", mapping)
        self.assertIn("turn_pub", mapping)

    def test_line_node_subscribes_and_parses_turn_command(self):
        calls = _calls(self.line)
        self.assertTrue(
            any(name == "create_subscription" and "/mission/turn" in literals for name, literals in calls)
        )
        handler = ast.unparse(_function(self.line, "_on_turn_command"))
        self.assertIn("parse_turn_command", handler)
        self.assertIn("JunctionTurner", handler)
        # 空串/坏输入 = 清除待执行转弯
        self.assertIn("self.turner = None", handler)

    def test_turn_takes_over_the_output(self):
        tick = ast.unparse(_function(self.line, "_tick"))
        self.assertIn("_turn_output", tick)
        self.assertIn("turn if turn is not None else out", tick)

    def test_turn_only_progresses_when_we_are_authorized(self):
        turn_output = ast.unparse(_function(self.line, "_turn_output"))
        self.assertIn("_authorized_source", turn_output)
        self.assertIn("SOURCE_LINE_FOLLOW", turn_output)
        self.assertIn("stale", turn_output, "读数过期必须传给转弯器（它会停车）")

    def test_failed_turn_does_not_resume_line_following(self):
        """方向没转对时继续往前开是危险的：失败后保持零速等任务层处置。"""
        turn_output = ast.unparse(_function(self.line, "_turn_output"))
        self.assertIn("command.failed", turn_output)
        failed_branch = turn_output.split("command.failed", 1)[1]
        self.assertNotIn(
            "self.turner = None",
            failed_branch.split("return", 1)[0],
            "失败分支不许清除转弯器（清掉就会回退 PD 继续巡线）",
        )
        self.assertIn("get_logger().error", failed_branch)

    def test_status_reports_turn_phase_for_the_web_panel(self):
        publish = ast.unparse(_function(self.line, "_publish"))
        for key in ("turn_phase", "turn_reason", "turn_pending", "turn_last_result"):
            self.assertIn(key, publish, f"状态串必须上报 {key}（网页显示转弯阶段）")
        self.assertIn("turn=", publish, "人类可读前缀也要带 turn=")


class LineAndRampWiringTests(unittest.TestCase):
    """B3：每段巡线限速 + 坡道（C3）接线。"""

    @classmethod
    def setUpClass(cls):
        cls.mission = _tree(MISSION_NODE)
        cls.line = _tree(LINE_NODE)
        cls.line_text = LINE_NODE.read_text(encoding="utf-8")
        cls.mission_text = MISSION_NODE.read_text(encoding="utf-8")

    def test_mission_manager_publishes_line_commands(self):
        """巡线参数的内容/重发时机在 MissionRun 里（行为测试覆盖）；这里查发布者与映射。"""
        calls = _calls(self.mission)
        self.assertTrue(
            any(name == "create_publisher" and "/mission/line" in literals for name, literals in calls)
        )
        mapping = ast.unparse(_function(self.mission, "_publish_command"))
        self.assertIn("CommandKind.LINE", mapping)
        self.assertIn("line_pub", mapping)

    def test_line_node_consumes_line_and_wheel_speed(self):
        calls = _calls(self.line)
        for topic in ("/mission/line", "/wheel_odom"):
            self.assertTrue(
                any(name == "create_subscription" and topic in literals for name, literals in calls),
                f"巡线节点必须订阅 {topic}",
            )
        handler = ast.unparse(_function(self.line, "_on_line_command"))
        self.assertIn("parse_line_command", handler)
        self.assertIn("effective_line_speed", handler)
        self.assertIn("RampController", handler)

    def test_line_limits_are_applied_before_publishing(self):
        tick = ast.unparse(_function(self.line, "_tick"))
        self.assertIn("_apply_line_limits", tick)
        limits = ast.unparse(_function(self.line, "_apply_line_limits"))
        self.assertIn("effective_vx_limit", limits)
        self.assertIn("self.ramp.step", limits)

    def test_stale_wheel_speed_disables_slip_detection(self):
        """轮速过期时不做打滑判定——宁可不判，也不要误判停车。"""
        limits = ast.unparse(_function(self.line, "_apply_line_limits"))
        self.assertIn("measured_speed_stale_s", limits)
        self.assertIn("measured = None", limits)

    def test_status_reports_ramp_and_limit_for_the_web_panel(self):
        publish = ast.unparse(_function(self.line, "_publish"))
        for key in ("line_limit_mps", "ramp_kind", "ramp_decision", "measured_speed_mps"):
            self.assertIn(key, publish, f"状态串必须上报 {key}")

    def test_route_payload_comes_from_the_run_loop(self):
        """载荷键（含坡道/转弯状态）由 `MissionRun.payload` 产出，行为测试与集成审计覆盖。"""
        tick = ast.unparse(_function(self.mission, "_tick"))
        self.assertIn("self.run.payload", tick)
        run_source = (
            PROJECT_ROOT / "ros2_ws/src/robogame_core/robogame_core/mission_run.py"
        ).read_text(encoding="utf-8")
        for key in ("ramp_decision", "line_limit_mps", "turn_phase"):
            self.assertIn(f'"{key}"', run_source, key)


class WorkSequenceWiringTests(unittest.TestCase):
    """B3：作业段动作序列（抓 3 块 / 搭 2 层 + 槽间微移）的接线。"""

    @classmethod
    def setUpClass(cls):
        cls.mission = _tree(MISSION_NODE)
        cls.mission_text = MISSION_NODE.read_text(encoding="utf-8")

    def test_work_sequence_logic_lives_in_the_run_loop(self):
        """作业序列的展开/逐步下发/来源匹配都在 `MissionRun`（行为测试覆盖）。"""
        run_source = (
            PROJECT_ROOT / "ros2_ws/src/robogame_core/robogame_core/mission_run.py"
        ).read_text(encoding="utf-8")
        for token in ("work_steps", "WorkPlan", "shifted_pose", "work_reference_pose"):
            self.assertIn(token, run_source, token)

    def test_node_forwards_results_with_their_source(self):
        """抓取结果与车体微移结果必须分开转发：否则侧移成功会被记成抓了一块。"""
        text = self.mission_text
        self.assertIn("self._on_motion_result", text)
        self.assertIn("self._on_manipulator_result", text)
        handler = ast.unparse(_function(self.mission, "_handle_result"))
        self.assertIn("self.run.handle_result(source", handler)


class MatchClockAndRoundsWiringTests(unittest.TestCase):
    """B4：比赛时钟与多趟循环的接线。"""

    @classmethod
    def setUpClass(cls):
        cls.mission = _tree(MISSION_NODE)
        cls.mission_text = MISSION_NODE.read_text(encoding="utf-8")

    def test_rounds_parameter_reaches_the_route_builder(self):
        self.assertIn('"rounds"', self.mission_text)
        loader = ast.unparse(_function(self.mission, "_load_route"))
        self.assertIn("rounds=rounds", loader)
        self.assertIn("load_route_plan", loader)

    def test_match_time_limit_is_a_configurable_parameter(self):
        self.assertIn('"match_time_limit_s"', self.mission_text)
        init = ast.unparse(_function(self.mission, "__init__"))
        self.assertIn("match_time_limit_s", init)
        self.assertIn("MissionConfig", init)

    def test_payload_comes_from_the_run_loop(self):
        tick = ast.unparse(_function(self.mission, "_tick"))
        self.assertIn("self.run.payload", tick)
        core = PROJECT_ROOT / "ros2_ws/src/robogame_core/robogame_core"
        run_source = (core / "mission_run.py").read_text(encoding="utf-8")
        self.assertIn('"rounds"', run_source)
        # 剩余时间随 route_progress 展开进来（住在 mission.py）
        machine_source = (core / "mission.py").read_text(encoding="utf-8")
        self.assertIn('"match_remaining_s"', machine_source)


class TurnCommandPathTests(unittest.TestCase):
    """纯逻辑组合：转弯命令 → 仲裁门控（授权语义与巡线路径同一套）。"""

    def _params(self):
        from robogame_core.junction_turn import turn_params_for_direction
        from robogame_core.mission_route import TurnDirection

        return turn_params_for_direction(TurnDirection.RIGHT, turn_rate_radps=1.0)

    def test_turn_command_reaches_cmd_vel_only_when_authorized(self):
        from robogame_core.cmd_vel_arbiter import CmdVelArbiter, ArbiterConfig
        from robogame_core.junction_turn import JunctionTurner
        from robogame_core.line_follow import LineSensorState
        from robogame_core.models import Velocity2D

        turner = JunctionTurner(self._params())
        turner.update(line_state=LineSensorState.INTERSECTION, now=0.0)
        command = turner.update(line_state=LineSensorState.INTERSECTION, now=0.02)
        self.assertNotEqual(command.wz, 0.0, "到路口后必须开始原地转")

        arbiter = CmdVelArbiter(ArbiterConfig(stale_s=0.5))
        arbiter.update(SOURCE_LINE_FOLLOW, Velocity2D(command.vx, 0.0, command.wz), now=0.05)
        authorized = arbiter.output(active_source=SOURCE_LINE_FOLLOW, now=0.06, emergency_stop=False)
        self.assertAlmostEqual(authorized.wz, command.wz)
        blocked = arbiter.output(active_source=SOURCE_MISSION_OTHER, now=0.06, emergency_stop=False)
        self.assertEqual((blocked.vx, blocked.wz), (0.0, 0.0), "未授权来源不得驱动底盘")
        stopped = arbiter.output(active_source=SOURCE_LINE_FOLLOW, now=0.06, emergency_stop=True)
        self.assertEqual((stopped.vx, stopped.wz), (0.0, 0.0), "急停优先于授权")


if __name__ == "__main__":
    unittest.main()
