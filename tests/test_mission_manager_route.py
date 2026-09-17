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

    def test_goal_is_published_before_authority(self):
        """先给目标再授权：反之位姿控制器会在没有目标时被授权（空转或按旧目标动）。"""
        function = _function(self.tree, "_dispatch_route")
        goal_line = authority_line = None
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            owner = getattr(node.func.value, "attr", "")
            if node.func.attr != "publish":
                continue
            if owner == "goal_pub" and goal_line is None:
                goal_line = node.lineno
            elif owner == "authority_pub" and authority_line is None:
                authority_line = node.lineno
        self.assertIsNotNone(goal_line, "路线分发里必须发 /motion/goal")
        self.assertIsNotNone(authority_line, "路线分发里必须发授权")
        self.assertLess(goal_line, authority_line, "目标必须早于授权发布（按源码行号判定）")

    def test_work_command_is_sent_once_per_work_index(self):
        source = MISSION_NODE.read_text(encoding="utf-8")
        self.assertIn("published_work_index", source)
        function = _function(self.tree, "_dispatch_route")
        guarded = any(
            isinstance(node, ast.Compare)
            and any(isinstance(op, ast.NotEq) for op in node.ops)
            for node in ast.walk(function)
        )
        self.assertTrue(guarded, "作业命令必须有「本次序号是否已发过」的判断")

    def test_terminal_states_release_authority(self):
        function = _function(self.tree, "_tick")
        calls_release = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_release_authority"
            for node in ast.walk(function)
        )
        self.assertTrue(calls_release, "终态必须释放底盘授权（谁都不许动）")
        function = _function(self.tree, "_release_authority")
        self.assertTrue(
            any(
                isinstance(node, ast.Constant) and node.value == "none"
                for node in ast.walk(function)
            )
            or "SOURCE_NONE" in ast.unparse(function),
            "_release_authority 必须发 none（SOURCE_NONE）",
        )

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

    def test_stale_line_status_blocks_segment_progress(self):
        function = _function(self.tree, "_tick")
        text = ast.unparse(function)
        self.assertIn("mark_stale(True)", text)
        self.assertIn("line_status_timeout_s", text)

    def test_legacy_mode_never_publishes_authority(self):
        """演示模式（route_enabled=false）不得广播授权：否则会改变既有 mock 流程。"""
        legacy_dispatch = ast.unparse(_function(self.tree, "_dispatch"))
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

    def test_motion_control_authorization_requires_our_own_source(self):
        function = _function(self.motion, "_authorized")
        text = ast.unparse(function)
        self.assertIn("require_authorization", text)
        self.assertIn("granted_source", text)
        self.assertIn("authorization_stale_s", text)
        self.assertIn("SOURCE_NAVIGATE", text)

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

    def test_mission_manager_publishes_and_clears_turn_command(self):
        calls = _calls(self.mission)
        self.assertTrue(
            any(name == "create_publisher" and "/mission/turn" in literals for name, literals in calls)
        )
        dispatch = ast.unparse(_function(self.mission, "_dispatch_route"))
        self.assertIn("_publish_turn_command", dispatch)
        # 终态必须清空待执行转弯，否则任务失败后残留指令会在下次授权时突然执行
        release = ast.unparse(_function(self.mission, "_release_authority"))
        self.assertIn("turn_pub", release)
        publisher = ast.unparse(_function(self.mission, "_publish_turn_command"))
        self.assertIn("turn_command_for", publisher)
        self.assertIn("if command is None", publisher, "非转弯段必须下发空串（清除）")

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

    def test_mission_manager_publishes_and_clears_line_command(self):
        calls = _calls(self.mission)
        self.assertTrue(
            any(name == "create_publisher" and "/mission/line" in literals for name, literals in calls)
        )
        dispatch = ast.unparse(_function(self.mission, "_dispatch_route"))
        self.assertIn("_publish_line_command", dispatch)
        publisher = ast.unparse(_function(self.mission, "_publish_line_command"))
        self.assertIn("line_command_for", publisher)
        self.assertIn("if command is None", publisher, "非巡线段必须下发空串（清除）")
        release = ast.unparse(_function(self.mission, "_release_authority"))
        self.assertIn("line_pub", release, "终态必须清除巡线参数")

    def test_retry_republishes_the_segment_commands(self):
        """打滑卡住后节点保持零速：只重置计时叫不醒它，必须重发本段命令。"""
        dispatch = ast.unparse(_function(self.mission, "_dispatch_route"))
        self.assertIn("published_retries", dispatch)
        self.assertIn("self.machine.retries", dispatch)
        retry_branch = dispatch.split("elif", 1)[-1]
        self.assertIn("_publish_line_command", retry_branch)
        self.assertIn("_publish_turn_command", retry_branch)

    def test_stuck_ramp_triggers_a_segment_retry(self):
        handler = ast.unparse(_function(self.mission, "_on_line_status"))
        self.assertIn("SlipDecision.STUCK.value", handler)
        self.assertIn("ramp_stuck_reports", handler)
        self.assertIn("action_failed=True", handler)
        self.assertIn("MECHANISM_ERROR", handler)

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

    def test_route_payload_exposes_ramp_state(self):
        payload = ast.unparse(_function(self.mission, "_publish_route_status"))
        for key in ("ramp_decision", "line_limit_mps", "turn_phase"):
            self.assertIn(key, payload, f"/mission/route 必须带 {key}（网页显示坡道/转弯状态）")


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
