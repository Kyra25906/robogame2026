"""A0.4 mock/real 契约测试（杀 P0-4）。

背景（执行队列 2026-08-18 A0.4）：真实固件与 mock 支持的机构命令集合曾
不一致（real 认 {GRAB, RELEASE, HOME}，mock 认 {GRAB, RELEASE, STOP,
RETREAT}），差异没有任何测试断言。P0-4 的修复已在 A4 完成（field 模式
PLACE 不再调机构式 RETREAT），本测试把「两侧命令集合相等，或差异在代码中
显式声明并附理由」变成机器可断言的契约。

实现：从 `robot_bridge/node.py::_mechanism` 的字面量提取两侧命令集合
（real = operations dict 键 + STOP 特判；mock = allowed set），断言对称差
恰好等于模块级常量 `REAL_MOCK_MECHANISM_COMMAND_DIFFERENCES` 声明的差异
（HOME real-only / RETREAT mock-only，附理由）。任一侧新增命令而不同步
声明，测试即红。
"""
import ast
import unittest
from pathlib import Path

NODE_PATH = (
    Path(__file__).parents[1]
    / "ros2_ws"
    / "src"
    / "robot_bridge"
    / "robot_bridge"
    / "node.py"
)

DECLARATION_NAME = "REAL_MOCK_MECHANISM_COMMAND_DIFFERENCES"


def _method_source(tree, source, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return ast.get_source_segment(source, node)
    raise AssertionError(f"method {name} not found")


def _extract_real_commands(method_source: str) -> set[str]:
    """real 侧：_mechanism 里 operations dict 的字符串键 + STOP 特判。"""
    tree = ast.parse(method_source)
    real: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {
                ast.literal_eval(k)
                for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if "GRAB" in keys:  # 就是 operations 映射表
                real |= keys
    if 'command == "STOP" and not self.mock_mode' in method_source:
        real.add("STOP")
    return real


def _extract_mock_commands(method_source: str) -> set[str]:
    """mock 侧：_mechanism 里 allowed set 的字面量。"""
    tree = ast.parse(method_source)
    mock: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Set):
            elements = {
                ast.literal_eval(e)
                for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
            if "GRAB" in elements:  # 就是 allowed 集合
                mock |= elements
    return mock


def _extract_declaration(source: str, tree):
    """模块级常量 REAL_MOCK_MECHANISM_COMMAND_DIFFERENCES（dict[str, str]）。

    兼容带类型注解的 AnnAssign 与普通 Assign 两种写法。
    """
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if (
            len(targets) == 1
            and isinstance(targets[0], ast.Name)
            and targets[0].id == DECLARATION_NAME
            and node.value is not None
        ):
            return ast.literal_eval(node.value)
    return None


def contract_issues(
    real: set[str], mock: set[str], declared: dict[str, str] | None
) -> list[str]:
    """契约检查（纯函数）：两侧命令集合对称差必须等于显式声明。

    返回问题列表；空列表 = 契约成立。
    """
    if declared is None:
        return [f"{DECLARATION_NAME} not found in bridge source"]
    issues: list[str] = []
    symmetric = real ^ mock
    declared_set = set(declared)
    if symmetric != declared_set:
        issues.append(
            f"command-set symmetric difference {sorted(symmetric)} != "
            f"declared {sorted(declared_set)}"
        )
    for command in declared_set:
        if (command in real) == (command in mock):
            issues.append(
                f"declared {command!r} must be on exactly one side, "
                f"real={command in real} mock={command in mock}"
            )
    for command, reason in declared.items():
        if not isinstance(reason, str) or not reason.strip():
            issues.append(f"declared {command!r} lacks a non-empty reason")
    return issues


class MockRealContractTests(unittest.TestCase):
    """真实代码：两侧命令集合契约成立。"""

    @classmethod
    def setUpClass(cls):
        cls.source = NODE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.mechanism = _method_source(cls.tree, cls.source, "_mechanism")
        cls.real = _extract_real_commands(cls.mechanism)
        cls.mock = _extract_mock_commands(cls.mechanism)
        cls.declared = _extract_declaration(cls.source, cls.tree)

    def test_real_and_mock_command_sets(self):
        # 明确钉住两侧当前集合，任何一侧增删命令都在这里可见。
        self.assertEqual(self.real, {"GRAB", "RELEASE", "HOME", "STOP"})
        self.assertEqual(self.mock, {"GRAB", "RELEASE", "STOP", "RETREAT"})

    def test_contract_holds(self):
        self.assertEqual(contract_issues(self.real, self.mock, self.declared), [])

    def test_only_declared_differences_are_home_and_retreat(self):
        # P0-4 的既定设计：HOME 仅 real、RETREAT 仅 mock，且声明里没有别的。
        self.assertEqual(set(self.declared or {}), {"HOME", "RETREAT"})
        self.assertIn("HOME", self.real)
        self.assertNotIn("HOME", self.mock)
        self.assertNotIn("RETREAT", self.real)
        self.assertIn("RETREAT", self.mock)


class ContractCheckerNegativeTests(unittest.TestCase):
    """反向证明：命令集合漂移会在测试里红。"""

    def test_undeclared_new_real_command_is_detected(self):
        issues = contract_issues(
            {"GRAB", "RELEASE", "HOME", "STOP", "PLACE"},
            {"GRAB", "RELEASE", "STOP", "RETREAT"},
            {"HOME": "real-only", "RETREAT": "mock-only"},
        )
        self.assertTrue(any("symmetric difference" in i for i in issues))

    def test_declared_command_on_both_sides_is_detected(self):
        issues = contract_issues(
            {"GRAB", "RELEASE", "HOME", "STOP"},
            {"GRAB", "RELEASE", "STOP", "RETREAT", "HOME"},
            {"HOME": "real-only", "RETREAT": "mock-only"},
        )
        self.assertTrue(any("exactly one side" in i for i in issues))

    def test_missing_declaration_is_detected(self):
        issues = contract_issues(
            {"GRAB", "RELEASE", "HOME", "STOP"},
            {"GRAB", "RELEASE", "STOP", "RETREAT"},
            None,
        )
        self.assertTrue(any("not found" in i for i in issues))

    def test_empty_reason_is_detected(self):
        issues = contract_issues(
            {"GRAB", "RELEASE", "HOME", "STOP"},
            {"GRAB", "RELEASE", "STOP", "RETREAT"},
            {"HOME": "  ", "RETREAT": "mock-only"},
        )
        self.assertTrue(any("empty reason" in i for i in issues))


class ContractParserTests(unittest.TestCase):
    """解析器本身的正确性（防提取逻辑悄悄漏集合/漏声明）。"""

    @classmethod
    def setUpClass(cls):
        cls.source = NODE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_extraction_finds_both_sides(self):
        mechanism = _method_source(self.tree, self.source, "_mechanism")
        self.assertEqual(_extract_real_commands(mechanism),
                         {"GRAB", "RELEASE", "HOME", "STOP"})
        self.assertEqual(_extract_mock_commands(mechanism),
                         {"GRAB", "RELEASE", "STOP", "RETREAT"})

    def test_declaration_is_module_level_dict(self):
        declared = _extract_declaration(self.source, self.tree)
        self.assertIsInstance(declared, dict)
        self.assertTrue(all(isinstance(v, str) for v in declared.values()))


if __name__ == "__main__":
    unittest.main()
