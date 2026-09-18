"""文档盘点工具的测试（纯逻辑 + 真仓库回归）。

这个工具存在的理由（2026-09-18 盘点时的事实）：
`docs/` 下 80+ 份 markdown **长得一样**，但有的要照做、有的只是当天留痕、有的已经作废；
照着过期文档做会得到**假通过**（R13：文档让人跑的脚本没有 `__main__`，跑了没输出）。
更麻烦的是还有一批文档**根本不在版本库里**（`*.docx`/`*.pdf`/`tmp/`/`outputs/`），
clone 到树莓派上的人看不到它们，却可能被别处的文字指向。

所以这里测三件事：
1. **分类检查真的能红**：用假仓库/假登记表构造五种问题，逐条验证能被抓到
   （一个永远报 0 的检查器比没有检查器更糟：它给人虚假的安全感）；
2. **分类与 .gitignore 不许各说各话**：报告里说「不入库」的依据必须是 `.gitignore` 里真实存在的行；
3. **真仓库自洽**：每份 `.md` 都被分类、登记表里的文件都存在、标为已过期的都有横幅。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from docs_inventory import (  # noqa: E402
    ARCHIVED_BANNER,
    CATEGORY_MEANING,
    CATEGORY_ORDER,
    IGNORED_RULES,
    REGISTRY,
    check,
    collect,
    discover_markdown,
    has_archived_banner,
    registry_entries,
    render_inventory,
    scan_ignored_areas,
)


def _fake_repo(tmp: Path) -> Path:
    """最小假仓库：一份已登记文档 + 一份没登记文档。"""
    (tmp / "docs").mkdir(parents=True, exist_ok=True)
    (tmp / "docs/known.md").write_text("# 已登记\n\n正文。\n", encoding="utf-8")
    (tmp / "docs/unknown.md").write_text("# 没登记\n\n正文。\n", encoding="utf-8")
    return tmp


def _registry(**overrides) -> dict[str, tuple[str, ...]]:
    """一份干净的假登记表（默认覆盖假仓库里的两份文档）。"""
    table: dict[str, tuple[str, ...]] = {category: () for category in CATEGORY_ORDER}
    table["current"] = ("docs/known.md", "docs/unknown.md")
    for category, paths in overrides.items():
        table[category] = tuple(paths)
    return table


class CategoryTableTests(unittest.TestCase):
    def test_every_category_has_a_meaning(self):
        """分类必须能解释自己——读者要凭这句话决定信不信它。"""
        for category in CATEGORY_ORDER:
            self.assertIn(category, CATEGORY_MEANING)
            self.assertTrue(CATEGORY_MEANING[category].strip())

    def test_registry_only_uses_known_categories(self):
        for category in REGISTRY:
            self.assertIn(category, CATEGORY_MEANING, f"未定义含义的类别：{category}")

    def test_每个文档只登记一次(self):
        paths = [path for _, path in registry_entries()]
        self.assertEqual(len(paths), len(set(paths)), "同一份文档被登记进两个类别")


class DetectionTests(unittest.TestCase):
    """每条检查都必须真的能红。"""

    def test_unregistered_document_is_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            (tmp / "docs/unknown.md").unlink()  # 先让它干净
            (tmp / "docs/new.md").write_text("# 新来的\n", encoding="utf-8")
            codes = [f.code for f in check(tmp, audited=("docs/known.md",), registry=_registry())]
            self.assertIn("doc-unregistered", codes)

    def test_registered_but_missing_is_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            (tmp / "docs/known.md").unlink()
            findings = check(tmp, audited=("docs/known.md", "docs/unknown.md"), registry=_registry())
            self.assertIn("registry-stale", [f.code for f in findings])

    def test_duplicate_registration_is_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            table = _registry()
            table["history"] = ("docs/known.md",)
            codes = [f.code for f in check(tmp, audited=(), registry=table)]
            self.assertIn("duplicate-registration", codes)

    def test_archived_without_banner_is_reported(self):
        """「已过期」的横幅是给读者看的：读者不会去读检查器的输出。"""
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            (tmp / "docs/old.md").write_text("# 旧文档\n\n正文。\n", encoding="utf-8")
            table = _registry(archived=("docs/old.md",))
            codes = [f.code for f in check(tmp, audited=(), registry=table)]
            self.assertIn("archived-without-banner", codes)

    def test_archived_with_banner_is_not_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            (tmp / "docs/old.md").write_text(
                f"# 旧文档\n\n{ARCHIVED_BANNER}：看 `docs/known.md`。\n", encoding="utf-8"
            )
            table = _registry(archived=("docs/old.md",))
            codes = [f.code for f in check(tmp, audited=(), registry=table)]
            self.assertNotIn("archived-without-banner", codes)

    def test_bold_banner_is_accepted(self):
        """`> ⚠️ **已过期（2026-09-17 标注）**` 是本项目已有写法，不该逼人改样式。"""
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            (tmp / "docs/old.md").write_text(
                "# 旧文档\n\n> ⚠️ **已过期（2026-09-17 标注）**：不要照做。\n", encoding="utf-8"
            )
            table = _registry(archived=("docs/old.md",))
            codes = [f.code for f in check(tmp, audited=(), registry=table)]
            self.assertNotIn("archived-without-banner", codes)

    def test_current_document_outside_audit_scope_is_reported(self):
        """自称当前执行却不被 docs_audit 覆盖 = 「审计通过」其实是「没检查」。"""
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            findings = check(tmp, audited=("docs/known.md",), registry=_registry())
            codes = [f.code for f in findings]
            self.assertIn("current-not-audited", codes)
            self.assertIn("docs/unknown.md", [f.subject for f in findings])

    def test_audited_document_without_category_is_reported(self):
        """两处登记必须一致：审计了却没说它算哪一类，等于分类不完整。"""
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            findings = check(
                tmp,
                audited=("docs/known.md", "docs/ghost.md"),
                registry={"current": ("docs/known.md",)},
            )
            codes = [f.code for f in findings]
            self.assertIn("audited-not-registered", codes)

    def test_missing_audit_list_is_not_a_silent_pass(self):
        """取不到审计登记表时要说「无法判定」，不能安静地报通过。"""
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            codes = [f.code for f in check(tmp, audited=(), registry=_registry())]
            self.assertIn("audit-list-unavailable", codes)

    def test_clean_repository_has_no_findings(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = _fake_repo(Path(raw))
            findings = check(
                tmp,
                audited=("docs/known.md", "docs/unknown.md"),
                registry=_registry(),
            )
            self.assertEqual([f.render() for f in findings], [])


class DiscoveryTests(unittest.TestCase):
    def test_working_directories_are_not_documents(self):
        """`tmp/`、`outputs/`、厂商固件里的 markdown 不是「文档」，不该要求分类。"""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / "docs").mkdir(parents=True)
            (tmp / "docs/a.md").write_text("# a\n", encoding="utf-8")
            (tmp / "tmp").mkdir()
            (tmp / "tmp/b.md").write_text("# b\n", encoding="utf-8")
            (tmp / "Four_Motor_PID_Test_1").mkdir()
            (tmp / "Four_Motor_PID_Test_1/LICENSE.md").write_text("# l\n", encoding="utf-8")
            self.assertEqual(discover_markdown(tmp), ["docs/a.md"])


class IgnoredAreaTests(unittest.TestCase):
    def test_every_ignored_rule_matches_the_real_gitignore(self):
        """报告里写「不入库」的依据必须是 `.gitignore` 里真实存在的行。

        不这么绑住的话，这份盘点报告会慢慢变成「对 .gitignore 的猜测」——
        而它偏偏是给现场同事判断「这个文件在树莓派上有没有」用的。
        """
        # `.gitignore` 里混有编码坏掉的旧中文注释（GBK 字节），所以按字节容错读：
        # 本测试只关心那几行 ASCII 模式是否存在，不关心注释能不能解码。
        gitignore = (ROOT / ".gitignore").read_bytes().decode("utf-8", errors="replace")
        for rule in IGNORED_RULES:
            self.assertIn(
                rule.ignores_line,
                gitignore,
                f"{rule.location} 的 .gitignore 依据 `{rule.ignores_line}` 在 .gitignore 里找不到",
            )

    def test_scan_reports_real_file_counts(self):
        """文件数必须来自现场扫描，不能是估的。"""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / "tmp").mkdir()
            (tmp / "tmp/x.py").write_text("x = 1\n", encoding="utf-8")
            (tmp / "tmp/y.py").write_text("y = 1\n", encoding="utf-8")
            areas = {area.location: area for area in scan_ignored_areas(tmp)}
            self.assertEqual(areas["tmp"].files, 2)


class RealRepositoryTests(unittest.TestCase):
    def test_every_markdown_is_classified(self):
        """仓库里每一份 `.md` 都必须有分类——新文档不能悄悄绕过。"""
        registered = {path for _, path in registry_entries()}
        unclassified = [path for path in discover_markdown(ROOT) if path not in registered]
        self.assertEqual(unclassified, [], "这些文档没有分类；往 tools/docs_inventory.py 加一行")

    def test_registry_paths_exist(self):
        """登记表里的文件必须真的存在（改名/删除后要同步）。"""
        missing = [path for _, path in registry_entries() if not (ROOT / path).is_file()]
        self.assertEqual(missing, [])

    def test_repository_classification_is_self_consistent(self):
        """真仓库必须 0 findings：分类完整、已过期的都有横幅、当前执行都被审计。"""
        self.assertEqual([f.render() for f in check(ROOT)], [])

    def test_current_documents_are_audited_by_docs_audit(self):
        """「当前执行」必须被 docs_audit 覆盖（单一事实来源：分类登记表）。"""
        from docs_audit import CURRENT_DOCUMENTS

        for path in REGISTRY["current"]:
            self.assertIn(path, CURRENT_DOCUMENTS)

    def test_field_checklist_is_current(self):
        """首次上车清单是人们现在要照做的文档，必须在 current 里。"""
        self.assertIn("docs/field/首次上车执行清单.md", REGISTRY["current"])

    def test_archived_documents_carry_a_banner(self):
        for path in REGISTRY["archived"]:
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertTrue(has_archived_banner(text), f"{path} 缺少已过期横幅")


class RenderTests(unittest.TestCase):
    def test_inventory_lists_every_category_and_every_document(self):
        documents = collect(ROOT)
        text = render_inventory(ROOT, documents, scan_ignored_areas(ROOT))
        for category in CATEGORY_ORDER:
            self.assertIn(category, text)
        for doc in documents:
            self.assertIn(f"`{doc.path}`", text)

    def test_inventory_says_where_it_comes_from(self):
        """生成的报告必须写明「不要手改」，否则下一个人会手改然后被覆盖。"""
        text = render_inventory(ROOT, collect(ROOT), scan_ignored_areas(ROOT))
        self.assertIn("tools/docs_inventory.py --write", text)

    def test_inventory_mentions_gitignored_areas(self):
        text = render_inventory(ROOT, collect(ROOT), scan_ignored_areas(ROOT))
        self.assertIn("不在版本库里的文档与目录", text)
        self.assertIn(".gitignore", text)


if __name__ == "__main__":
    unittest.main()
