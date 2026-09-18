"""文档一致性审计工具的测试（纯逻辑 + 真仓库回归）。

这个工具存在的理由：**文档和事实不一致时，照着文档做的人会得到假通过**。
R13 真实踩过两次——文档让人跑的 `tools/mission_sim.py` 当时没有 `__main__`
（跑了没输出，看起来"通过"了），预算表里的一个 `⚠️` 让整条命令在 GBK 控制台崩掉。

所以这里测两件事：
1. **它真的能发现问题**：用临时假仓库构造四种不一致，逐条验证能被抓到
   （一个永远报 0 的检查器比没有检查器更糟——它会给人虚假的安全感）；
2. **它不在真仓库上误报**：真仓库的当前执行文档必须 0 findings
   （假问题会训练人忽略 findings，所以这条是硬要求）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docs_audit import (
    CURRENT_DOCUMENTS,
    Finding,
    audit,
    audit_details,
    audit_document,
    build_node_names,
    expand_braces,
    format_report,
)

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# 纯函数
# --------------------------------------------------------------------------


class ExpandBracesTests(unittest.TestCase):
    def test_expands_a_single_brace_group(self):
        self.assertEqual(
            expand_braces("tools/web/{a.js,b.js}"),
            ["tools/web/a.js", "tools/web/b.js"],
        )

    def test_expands_nested_groups(self):
        self.assertEqual(
            expand_braces("docs/{field,team}/{a.md,b.md}"),
            ["docs/field/a.md", "docs/field/b.md", "docs/team/a.md", "docs/team/b.md"],
        )

    def test_leaves_plain_paths_alone(self):
        self.assertEqual(expand_braces("tools/field_dashboard.py"), ["tools/field_dashboard.py"])

    def test_empty_group_is_left_alone(self):
        """`{}` 不是展开语法，宁可原样返回也不要吐出空路径。"""
        self.assertEqual(expand_braces("tools/{}"), ["tools/{}"])


# --------------------------------------------------------------------------
# 假仓库：验证四种检查都能抓到问题
# --------------------------------------------------------------------------


def _fake_repo(tmp: Path) -> Path:
    """造一个最小仓库：一个包、一个 launch、一个 tools 脚本。"""
    (tmp / "ros2_ws/src/demo_pkg/launch").mkdir(parents=True)
    (tmp / "ros2_ws/src/demo_pkg/launch").mkdir(parents=True, exist_ok=True)
    (tmp / "ros2_ws/src/demo_pkg/launch/real.launch.py").write_text(
        "def generate_launch_description():\n    return None\n", encoding="utf-8"
    )
    (tmp / "ros2_ws/src/demo_pkg/demo_pkg").mkdir(parents=True, exist_ok=True)
    (tmp / "ros2_ws/src/demo_pkg/demo_pkg/node.py").write_text(
        'import rclpy\n\n\nclass DemoNode(rclpy.node.Node):\n'
        '    def __init__(self):\n        super().__init__("demo_node")\n'
        '        self.pub = self.create_publisher(object, "/demo/topic", 10)\n',
        encoding="utf-8",
    )
    (tmp / "tools").mkdir(parents=True, exist_ok=True)
    (tmp / "tools/runnable.py").write_text(
        'def main():\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n',
        encoding="utf-8",
    )
    (tmp / "tools/library_only.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8"
    )
    (tmp / "docs").mkdir(parents=True, exist_ok=True)
    return tmp


def _write_doc(tmp: Path, text: str) -> str:
    path = tmp / "docs/current.md"
    path.write_text(text, encoding="utf-8")
    return "docs/current.md"


class FindingDetectionTests(unittest.TestCase):
    """每一条检查都必须真的能红——不然「审计通过」只是「没检查」。"""

    def test_missing_path_is_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(tmp, "见 `tools/does_not_exist.py`。\n")
            codes = [f.code for f in audit_document(tmp, doc)]
            self.assertIn("path-missing", codes)

    def test_existing_path_is_not_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(tmp, "见 `tools/runnable.py`。\n")
            self.assertEqual(audit_document(tmp, doc), [])

    def test_line_number_suffix_and_braces_are_not_false_positives(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(
                tmp, "见 `tools/runnable.py:12-20` 与 `tools/{runnable.py}`。\n"
            )
            self.assertEqual(audit_document(tmp, doc), [])

    def test_command_without_main_is_reported(self):
        """文档让人跑一个没有 __main__ 的脚本 = 跑了没输出 = 假通过。"""
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(tmp, "先跑\n\n```bash\npython3 tools/library_only.py\n```\n")
            findings = audit_document(tmp, doc)
            self.assertIn("command-missing-main", [f.code for f in findings])

    def test_command_with_main_is_not_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(tmp, "先跑 `python3 tools/runnable.py`。\n")
            self.assertEqual(audit_document(tmp, doc), [])

    def test_missing_launch_file_is_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(tmp, "ros2 launch demo_pkg ghost.launch.py\n")
            findings = audit_document(tmp, doc)
            self.assertIn("launch-missing", [f.code for f in findings])

    def test_existing_launch_file_is_not_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(tmp, "ros2 launch demo_pkg real.launch.py\n")
            self.assertEqual(audit_document(tmp, doc), [])

    def test_unknown_topic_is_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(tmp, "看 /mission/ghost_topic 的输出。\n")
            self.assertIn("topic-unknown", [f.code for f in audit_document(tmp, doc)])

    def test_known_topic_is_not_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(tmp, "看 /demo/topic 的输出。\n")
            self.assertEqual(audit_document(tmp, doc), [])

    def test_node_names_are_not_mistaken_for_topics(self):
        """`ros2 param get /demo_node x` 里的是**节点名**，不是话题（真实误报）。"""
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(tmp, "ros2 param get /demo_node some_param\n")
            self.assertEqual(audit_document(tmp, doc), [])
            self.assertIn("demo_node", build_node_names(tmp))

    def test_missing_document_itself_is_reported(self):
        """登记表里写了但文件不存在 → 必须红，否则覆盖率会静默丢失。"""
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            findings = audit_document(tmp, "docs/never_written.md")
            self.assertEqual([f.code for f in findings], ["path-missing"])


class FindingRenderTests(unittest.TestCase):
    def test_render_includes_code_and_location(self):
        text = Finding("topic-unknown", "docs/a.md", "话题 /x 找不到").render()
        self.assertIn("topic-unknown", text)
        self.assertIn("docs/a.md", text)
        self.assertIn("/x", text)


# --------------------------------------------------------------------------
# 真仓库
# --------------------------------------------------------------------------


class RealRepositoryTests(unittest.TestCase):
    def test_current_documents_have_no_findings(self):
        """当前执行文档必须干净：文档里让人跑的命令/引用的文件都真的存在。

        这条是 R13 两次真实教训的回归——文档说「跑这个」，结果那个脚本没有
        `__main__`（没输出）或在控制台上直接崩掉。
        """
        findings = audit(ROOT)
        self.assertEqual([f.render() for f in findings], [])

    def test_every_registered_document_exists(self):
        for document in CURRENT_DOCUMENTS:
            self.assertTrue(
                (ROOT / document).is_file(),
                f"登记表里的 {document} 不存在（改名了就要同步登记表，否则覆盖率静默丢失）",
            )

    def test_first_time_field_checklist_is_registered(self):
        """首次上车清单是当前执行文档，必须纳入审计范围。"""
        self.assertIn("docs/field/首次上车执行清单.md", CURRENT_DOCUMENTS)

    def test_checklist_commands_are_real(self):
        """清单里那几条命令必须真的存在且可执行（否则现场会卡在第一步）。"""
        text = (ROOT / "docs/field/首次上车执行清单.md").read_text(encoding="utf-8")
        for command in (
            "python3 tools/run_tests.py",
            "python3 tools/integration_audit.py",
            "python3 tools/docs_audit.py",
            "python3 tools/mission_sim.py",
            "python3 tools/mission_budget.py",
            "python3 tools/mission_faults.py",
            "ros2 launch robogame_bringup hardware.launch.py",
            "ros2 launch robogame_bringup line_follow_hardware.launch.py",
        ):
            self.assertIn(command, text, f"清单里少了命令：{command}")


class SuppressionTests(unittest.TestCase):
    """文档「本来就是在报告缺失」时的显式豁免。

    检查器分不清「文档声称某文件存在」和「文档说某文件不存在」。没有出口的话，
    要么把真问题删掉（信息丢失），要么留一堆假问题（训练人忽略 findings）。
    所以给显式声明，并且**报告里必须打印出来**——静默豁免等于悄悄关掉检查。
    """

    def test_declared_suppression_hides_only_that_finding(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(
                tmp,
                "<!-- docs-audit: allow path-missing docs/ghost.md -->\n"
                "报告里提到 `docs/ghost.md` 不存在，另外 `tools/also_ghost.py` 也不存在。\n",
            )
            codes = [f.code for f in audit_document(tmp, doc)]
            self.assertEqual(codes, ["path-missing"], "只该剩没被豁免的那一条")
            self.assertIn("tools/also_ghost.py", audit_document(tmp, doc)[0].detail)

    def test_suppression_is_code_and_subject_specific(self):
        """豁免 `path-missing docs/ghost.md` 不能顺手放过 `/幽灵话题`。"""
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(
                tmp,
                "<!-- docs-audit: allow path-missing docs/ghost.md -->\n"
                "`docs/ghost.md` 不存在；顺带一提 /mission/ghost 也没有。\n",
            )
            codes = sorted(f.code for f in audit_document(tmp, doc))
            self.assertEqual(codes, ["topic-unknown"])

    def test_suppressions_are_reported_not_silent(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(
                tmp, "<!-- docs-audit: allow path-missing docs/ghost.md -->\n`docs/ghost.md`\n"
            )
            result = audit_details(tmp, (doc,))
            self.assertEqual(result.findings, [])
            self.assertTrue(result.ok)
            self.assertEqual([s.subject for s in result.suppressions], ["docs/ghost.md"])
            self.assertIn("已声明豁免", format_report(result.findings, (doc,), result.suppressions))

    def test_declaration_must_be_its_own_line(self):
        """正文里讲解这个语法，不能被当成真声明（R14 真实踩过）。"""
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(
                tmp,
                "用法是写 `<!-- docs-audit: allow path-missing docs/ghost.md -->` 这样一行。\n"
                "而 `docs/ghost.md` 确实不存在。\n",
            )
            self.assertEqual([f.code for f in audit_document(tmp, doc)], ["path-missing"])

    def test_duplicate_declarations_are_reported_once(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            doc = _write_doc(
                tmp,
                "<!-- docs-audit: allow path-missing docs/ghost.md -->\n"
                "<!-- docs-audit: allow path-missing docs/ghost.md -->\n"
                "`docs/ghost.md`\n",
            )
            result = audit_details(tmp, (doc,))
            self.assertEqual(len(result.suppressions), 1)

    def test_work_log_declares_its_known_missing_references(self):
        """工作留痕里登记了历史文档的缺失引用，这些豁免必须显式写下（不能靠删文字）。"""
        text = (ROOT / "docs/history/B3_WORK_LOG_2026-09-17.md").read_text(encoding="utf-8")
        for expected in (
            "path-missing docs/field/MCU_PROTOCOL.md",
            "topic-unknown /cubes/front",
        ):
            self.assertIn(expected, text, f"豁免声明里少了：{expected}")


if __name__ == "__main__":
    unittest.main()
