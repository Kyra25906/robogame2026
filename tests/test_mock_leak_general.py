"""A0.5 通用结构性断言：真实模式下不得读取任何 mock 状态字段（杀 P0-5 的通用版）。

背景（执行队列 2026-08-18 A0.5）：P0-5 的具体修复与单字段 AST 断言
（`test_real_retreat_complete_does_not_read_mock_state`）已在 A4 完成；
本测试把同一原则推广到**全部** mock 状态字段（字段清单从
`mock_mechanism.py::MockMechanismState` 的 dataclass 字段提取，不硬编码）
与**全部**读取点：`robot_bridge/node.py` 里任何对
`self.mock_mechanism_state.<field>` 的读取，必须位于 mock-only 分支。

识别的 mock 门控（两种既定写法）：
1. 显式：`X if self.mock_mode else Y` / `if self.mock_mode:` 块
   （及 `if not self.mock_mode:` 的 else 分支）——读取在 mock 分支；
2. 隐式：`real_status is not None` 的 else 分支，其中 real_status 由
   `self._latest_status if not self.mock_mode else None` 派生（mock 下为
   None，故 else 分支只在 mock 可达）。

任何在真实分支或无条件位置的 mock 状态读取，测试即红。
"""
import ast
import unittest
from pathlib import Path

BRIDGE_PATH = (
    Path(__file__).parents[1]
    / "ros2_ws"
    / "src"
    / "robot_bridge"
    / "robot_bridge"
    / "node.py"
)
MOCK_MECHANISM_PATH = (
    Path(__file__).parents[1]
    / "ros2_ws"
    / "src"
    / "robogame_core"
    / "robogame_core"
    / "mock_mechanism.py"
)


def mock_state_fields(mock_module_source: str) -> set[str]:
    """从 MockMechanismState dataclass 提取字段名（通用，不硬编码字段清单）。"""
    tree = ast.parse(mock_module_source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MockMechanismState":
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
            }
    return set()


def _mock_branch_polarity(test: ast.AST) -> bool | None:
    """test 的 mock 分支极性：True=if 分支是 mock；False=else 分支是 mock；None=不认识。"""
    if (
        isinstance(test, ast.Attribute)
        and isinstance(test.value, ast.Name)
        and test.value.id == "self"
        and test.attr == "mock_mode"
    ):
        return True
    if (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and isinstance(test.operand, ast.Attribute)
        and isinstance(test.operand.value, ast.Name)
        and test.operand.value.id == "self"
        and test.operand.attr == "mock_mode"
    ):
        return False
    return None


def _not_none_test_name(test: ast.AST) -> str | None:
    """test 为 `name is not None` 时返回 name；否则 None。"""
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.IsNot)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
        and isinstance(test.left, ast.Name)
    ):
        return test.left.id
    return None


def _name_is_mock_derived(name: str, scope: ast.AST) -> bool:
    """该作用域内存在 `name = <...> if not self.mock_mode else None` 派生。"""
    for node in ast.walk(scope):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
            and isinstance(node.value, ast.IfExp)
            and _mock_branch_polarity(node.value.test) is False
            and isinstance(node.value.orelse, ast.Constant)
            and node.value.orelse.value is None
        ):
            return True
    return False


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def unguarded_mock_reads(source: str, fields: set[str]) -> list[tuple[int, str]]:
    """返回 node.py 中未被 mock 门控的 mock 状态读取 [(行号, 字段)]。

    规则：任何 `self.mock_mechanism_state.<field>` 读取必须被下述祖先门控之一
    覆盖（读取位于其 mock 分支）：
    - `self.mock_mode` / `not self.mock_mode` 条件表达式或 if 语句；
    - `name is not None` 条件表达式，且 name 由 `... if not self.mock_mode
      else None` 派生（其 else 分支只在 mock 可达）。
    """
    tree = ast.parse(source)
    parents = _parent_map(tree)
    violations: list[tuple[int, str]] = []

    def enclosing_function(node: ast.AST) -> ast.FunctionDef | None:
        cur = node
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, ast.FunctionDef):
                return cur
        return None

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Attribute)
            and node.attr in fields
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == "mock_mechanism_state"
        ):
            continue
        guarded = False
        cur = node
        while cur in parents:
            parent = parents[cur]
            if isinstance(parent, ast.IfExp):
                polarity = _mock_branch_polarity(parent.test)
                if polarity is True and cur is parent.body:
                    guarded = True
                elif polarity is False and cur is parent.orelse:
                    guarded = True
                else:
                    name = _not_none_test_name(parent.test)
                    scope = enclosing_function(node) or tree
                    if (
                        name is not None
                        and _name_is_mock_derived(name, scope)
                        and cur is parent.orelse
                    ):
                        guarded = True
            elif isinstance(parent, ast.If):
                polarity = _mock_branch_polarity(parent.test)
                if polarity is True and cur in parent.body:
                    guarded = True
                elif polarity is False and cur in parent.orelse:
                    guarded = True
            if guarded:
                break
            cur = parent
        if not guarded:
            violations.append((node.lineno, node.attr))
    return violations


class GeneralMockLeakTests(unittest.TestCase):
    """真实代码：node.py 里没有任何未被门控的 mock 状态读取。"""

    def test_no_unguarded_mock_state_reads_in_bridge(self):
        fields = mock_state_fields(MOCK_MECHANISM_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            fields,
            {"cube_present", "gripper_closed", "lift_height_m", "retreat_complete"},
        )
        violations = unguarded_mock_reads(
            BRIDGE_PATH.read_text(encoding="utf-8"), fields
        )
        self.assertEqual(
            violations, [], f"unguarded mock state reads: {violations}"
        )

    def test_all_declared_mock_fields_are_checked(self):
        # 通用性：字段清单来自 dataclass，新增字段自动纳入检查。
        self.assertEqual(
            mock_state_fields(MOCK_MECHANISM_PATH.read_text(encoding="utf-8")),
            {"cube_present", "gripper_closed", "lift_height_m", "retreat_complete"},
        )


class MockLeakCheckerNegativeTests(unittest.TestCase):
    """反向证明：任何真实分支/无门控的 mock 状态读取都会被检出。"""

    def _check(self, snippet: str) -> list[tuple[int, str]]:
        return unguarded_mock_reads(
            snippet, {"retreat_complete", "cube_present", "lift_height_m"}
        )

    def test_unconditional_read_is_detected(self):
        # 旧 P0-5 原写法：无条件读 mock 状态。
        self.assertTrue(self._check(
            "status.retreat_complete = self.mock_mechanism_state.retreat_complete\n"
        ))

    def test_read_in_real_branch_of_mock_ternary_is_detected(self):
        self.assertTrue(self._check(
            "x = self.mock_mechanism_state.cube_present "
            "if not self.mock_mode else False\n"
        ))

    def test_read_in_real_branch_of_status_ternary_is_detected(self):
        # real_status is not None 的 if 分支是真实侧，读 mock 状态即泄漏。
        snippet = (
            "real_status = self._latest_status if not self.mock_mode else None\n"
            "x = self.mock_mechanism_state.lift_height_m "
            "if real_status is not None else 0.0\n"
        )
        self.assertTrue(self._check(snippet))

    def test_mock_branch_of_ternary_is_clean(self):
        self.assertEqual(self._check(
            "x = self.mock_mechanism_state.cube_present "
            "if self.mock_mode else False\n"
        ), [])

    def test_mock_if_block_is_clean(self):
        self.assertEqual(self._check(
            "if self.mock_mode:\n"
            "    x = self.mock_mechanism_state.gripper_closed\n"
        ), [])

    def test_real_status_else_branch_is_clean(self):
        snippet = (
            "real_status = self._latest_status if not self.mock_mode else None\n"
            "x = 1.0 if real_status is not None "
            "else self.mock_mechanism_state.lift_height_m\n"
        )
        self.assertEqual(self._check(snippet), [])

    def test_undecorated_not_none_guard_is_not_accepted(self):
        # real_status 不是由 mock 派生时，`is not None` 的 else 分支不视为 mock 门控。
        snippet = (
            "real_status = self._latest_status\n"
            "x = 1.0 if real_status is not None "
            "else self.mock_mechanism_state.lift_height_m\n"
        )
        self.assertTrue(self._check(snippet))


if __name__ == "__main__":
    unittest.main()
