"""现场文档一致性审计：文档里写的命令、路径、话题，**真的存在吗**。

## 为什么需要这个工具（真实动机）

R13 收尾时按 `docs/field/上电自主完赛流程.md` 原样敲命令，发现两个问题：

1. `python3 tools/mission_sim.py` → 什么都不打印（那个文件当时没有 `__main__`）。
   照着文档做的人会以为「预演通过了」——**假通过比报错更危险**。
2. `python3 tools/mission_budget.py` → 在 GBK 控制台直接 `UnicodeEncodeError` 崩掉
   （表里一个 `⚠️`），算好的结论全被一个符号带崩。

两个都不是「代码有 bug」，而是**文档和事实不一致**。这类问题靠人眼复查必然复发，
所以做成可复现的检查：跑一条命令，看有没有 findings。

## 覆盖范围（重要，别高估它）

- 只审计 `default_documents()` 里登记的**当前执行文档**，不是全仓库文档。
  历史文档（带「已过期」标注的复盘、日志）故意不纳入：它们记录的是当时的真实情况，
  拿今天的代码去判它们「错」没有意义。
- 每一条检查都只回答「文件/命令/话题名是否存在」这种**可机械判定**的问题。
  它**不能**检查：参数值是否合理、流程步骤是否安全、真车行为是否符合预期。

## 检查项

| 代码 | 含义 |
|---|---|
| `path-missing` | 文档里反引号引用的仓库路径不存在 |
| `command-missing-main` | 文档让人跑的 `python3 tools/x.py` 里没有 `__main__`（跑了没输出） |
| `launch-missing` | `ros2 launch <包> <文件>.launch.py` 指向的 launch 文件不在仓库里 |
| `topic-unknown` | 文档提到的本项目话题在源码里找不到任何出现处（可能是话题改名了） |

## 豁免（文档本来就是在报告缺失时）

有时文档**刻意**提到一个不存在的东西（例如「历史文档引用了 `docs/field/MCU_PROTOCOL.md`，
该文件不存在」）。检查器分不清「文档声称它存在」和「文档说它不存在」，所以在文档里写：

```html
<!-- docs-audit: allow path-missing docs/field/MCU_PROTOCOL.md; topic-unknown /cubes/front -->
```

规则：只豁免 **code + subject 精确匹配**的那一条；报告里会把这些豁免**打印出来**
（静默豁免等于把检查悄悄关掉）。

用法：

```bash
python3 tools/docs_audit.py          # 退出码 0 = 没有 findings
python3 tools/docs_audit.py --all    # 连历史文档一起看（会有已知噪声）
```
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# 待审计文档登记表
# --------------------------------------------------------------------------

#: 分类登记表（`tools/docs_inventory.py` 的 `REGISTRY`）里这些类别要被审计：
#: `current`（现在照它做动作）+ `reference`（现在照它做判断）。
#: 为什么不审计 `history` / `archived` / `teaching`：它们记录的是**当时**的事实，
#: 拿今天的代码去判它们「错」没有意义，还会用假问题训练人忽略 findings。
_AUDITED_CATEGORIES = ("current", "reference")

#: 除上表之外仍需审计的历史文档：里面有可以直接复制的命令，
#: 命令过期了照样会让人得到假通过（R13 的原始动机就是这个）。
EXTRA_DOCUMENTS: tuple[str, ...] = ("docs/history/B3_WORK_LOG_2026-09-17.md",)

#: 取不到分类登记表时的兜底（宁可少检查，也不要假装检查过）。
_FALLBACK_DOCUMENTS: tuple[str, ...] = (
    "docs/field/上电自主完赛流程.md",
    "docs/field/首次上车执行清单.md",
    "docs/field/现场待测清单_B2B3_待填值.md",
) + EXTRA_DOCUMENTS


def _derive_current_documents() -> tuple[str, ...]:
    """从分类登记表推导审计范围（**单一事实来源**，防止两张表悄悄漂移）。

    2026-09-18 盘点时发现的问题：分类表在 `tools/docs_inventory.py`、审计范围在本文件，
    两张表各写一份，必然出现「docs/README.md 让人去读的文档其实没被审计」这种裂缝。
    现在审计范围由分类推导：一份文档只要被标成「当前执行」，就自动进入审计范围，
    不需要人去第二处登记。
    """
    try:
        from docs_inventory import REGISTRY  # 同目录导入；tools/ 已在 sys.path 上

        documents: list[str] = []
        for category in _AUDITED_CATEGORIES:
            documents.extend(REGISTRY.get(category, ()))
        documents.extend(EXTRA_DOCUMENTS)
        # 去重但保序（有人把同一份文档登记两次时不要重复审计）
        return tuple(dict.fromkeys(documents))
    except Exception:  # pragma: no cover - 只有 tools/ 不在 sys.path 时才会发生
        return _FALLBACK_DOCUMENTS


#: 当前执行文档（B2/B3/B4 相关）。新增这类文档时要加到这里——
#: 否则「审计通过」只是「没检查」，这是最容易被误读的一种通过。
CURRENT_DOCUMENTS: tuple[str, ...] = _derive_current_documents()

#: 反引号里的仓库路径（只认这几个顶层目录，避免把 URL、包名当成路径）
_PATH_RE = re.compile(
    r"`((?:docs|tools|tests|ros2_ws)/[^\s`（）()，。；：、]*)`"
)
#: 路径里的 shell 花括号展开：`tools/web/{a.js,b.js}` 要当成两个路径分别检查
_BRACE_RE = re.compile(r"\{([^{}]*)\}")
#: 行号引用：`docs/x.md:104` / `:16-29` / `:49/65` / `:182,189`
_LINE_REF_RE = re.compile(r":\d+(?:[-/,]\d+)*$")
#: 生成目录与通配：不检查（它们本来就不在仓库里 / 不是具体路径）
_GENERATED_PREFIXES = ("ros2_ws/build", "ros2_ws/install", "ros2_ws/log")
#: 文档内豁免声明：`<!-- docs-audit: allow path-missing docs/x.md; ... -->`
#: 必须是**单独一行**：否则「在正文里讲解这个语法」的句子会被当成真声明
#: （R14 真实踩过：工作留痕里引用这个语法做说明，于是被豁免了两遍）。
_SUPPRESSION_RE = re.compile(
    r"^[ \t]*<!--\s*docs-audit:\s*allow\s+(.*?)-->[ \t]*$", re.S | re.MULTILINE
)
#: 让人执行的 python 命令
_PY_CMD_RE = re.compile(r"python3?\s+((?:tools|tests)/[A-Za-z0-9_./]+\.py)")
#: ros2 launch <包> <文件>.launch.py
_LAUNCH_RE = re.compile(r"ros2\s+launch\s+([a-z_]+)\s+([A-Za-z0-9_./]+\.launch\.py)")
#: 本项目话题：限定根名，避免把 /tmp/xxx 之类当话题
_TOPIC_RE = re.compile(
    r"(?<![\w/])/"
    r"(?:mission|line_follow|line_sensor|robot|cmd_vel|wheel_odom|imu|pose|"
    r"cubes|manipulator|motion|perception|arm)[A-Za-z0-9_/]*"
)


@dataclass(frozen=True)
class Finding:
    """一条不一致。`code` 是机械可判定的类别，`detail` 是给人看的位置与原因。

    `subject` 是被检查的那个具体对象（路径 / 脚本 / 话题），用于：
    ① 让人一眼看出「是谁的问题」；② 与文档里的豁免声明精确匹配——
    豁免只豁免**这一条**，不会顺手把同类问题全放过去。
    """

    code: str
    document: str
    detail: str
    subject: str = ""

    def render(self) -> str:
        return f"[{self.code}] {self.document}: {self.detail}"


@dataclass(frozen=True)
class Suppression:
    """文档里显式声明的豁免（`<!-- docs-audit: allow <code> <subject> -->`）。

    为什么需要：有些文档**本来就是在报告一个缺失的东西**（如「历史文档引用了
    `docs/field/MCU_PROTOCOL.md`，该文件不存在」）。检查器无法区分
    「文档声称它存在」和「文档说它不存在」。所以给一个显式、可被 review 的出口，
    并且在报告里**打印出来**——豁免必须是可见的，不能静默。
    """

    code: str
    subject: str
    document: str

    def render(self) -> str:
        return f"{self.code} {self.subject}（{self.document}）"


@dataclass(frozen=True)
class AuditResult:
    findings: list[Finding]
    suppressions: list[Suppression]

    @property
    def ok(self) -> bool:
        return not self.findings


# --------------------------------------------------------------------------
# 源码索引：话题名是否存在
# --------------------------------------------------------------------------


def _source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    src = root / "ros2_ws" / "src"
    if src.is_dir():
        files.extend(sorted(src.rglob("*.py")))
    tools = root / "tools"
    if tools.is_dir():
        files.extend(sorted(tools.rglob("*.py")))
        files.extend(sorted(tools.rglob("*.js")))
    return files


def build_source_index(root: Path) -> str:
    """把所有源码拼成一个大字符串（体积小，够做「这个名字出现过吗」的判断）。

    为什么用「出现过」而不是「被 publish/subscribe」：真话题名可能来自常量
    （`SOURCE_NAVIGATE`、`CRITICAL_TOPICS`）或 f-string，静态解析会漏；
    这里的目标很窄——**话题改名后文档还留着旧名字**必须被发现。
    """
    chunks: list[str] = []
    for path in _source_files(root):
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:  # pragma: no cover - 权限问题不该让审计整体失败
            continue
    return "\n".join(chunks)


#: 节点名出现在 `super().__init__("名字")` 里
_NODE_NAME_RE = re.compile(r"super\(\)\.__init__\(\s*[\"']([A-Za-z0-9_]+)[\"']")


def build_node_names(root: Path) -> set[str]:
    """从源码抽出所有 ROS 节点名。

    为什么需要：文档里 `ros2 param get /mission_manager ...` 里的 `/mission_manager` 是
    **节点名**，形态和话题一模一样。不排除它就会报「话题不存在」这种假问题——
    **审计报假问题比不报更糟**，它会训练人忽略 findings。
    节点名从源码抽（而不是写死一张表），是为了节点改名后这张表跟着变。
    """
    names: set[str] = set()
    for path in _source_files(root):
        if path.suffix != ".py":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover
            continue
        names.update(_NODE_NAME_RE.findall(text))
    return names


# --------------------------------------------------------------------------
# 单项检查
# --------------------------------------------------------------------------


def expand_braces(text: str) -> list[str]:
    """展开文档里常见的 shell 花括号写法：`webs/{a.js,b.js}` → 两个路径。

    为什么必须支持：文档里 `tools/field_dashboard_web/{index.html,app.js}` 是很自然的
    写法，不做展开就会把整串当成「路径不存在」——**审计报假问题比不报更糟**，
    因为它会训练人忽略 findings。
    """
    match = _BRACE_RE.search(text)
    if match is None:
        return [text]
    prefix = text[: match.start()]
    suffix = text[match.end() :]
    parts = [part.strip() for part in match.group(1).split(",") if part.strip()]
    if not parts:
        return [text]
    expanded: list[str] = []
    for part in parts:
        expanded.extend(expand_braces(prefix + part + suffix))
    return expanded


def _check_paths(root: Path, document: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for raw in sorted(set(_PATH_RE.findall(text))):
        for match in expand_braces(raw):
            # 去掉行号后缀：文档引用 `docs/x.md:104` 时该检查的是 `docs/x.md`
            candidate = _LINE_REF_RE.sub("", match).rstrip("/")
            if not candidate or "*" in candidate:
                continue
            if candidate.startswith(_GENERATED_PREFIXES):
                continue
            if (root / candidate).exists():
                continue
            findings.append(
                Finding(
                    "path-missing", document, f"路径不存在：{candidate}", subject=candidate
                )
            )
    return findings


def _check_python_commands(root: Path, document: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for target in sorted(set(_PY_CMD_RE.findall(text))):
        path = root / target
        if not path.is_file():
            findings.append(
                Finding(
                    "command-missing-main",
                    document,
                    f"命令引用的脚本不存在：{target}",
                    subject=target,
                )
            )
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        if "__main__" not in body:
            # 这一条是 R13 真实踩过的：命令跑得「成功」但没有任何输出
            findings.append(
                Finding(
                    "command-missing-main",
                    document,
                    f"文档让人跑 {target}，但它没有 __main__（跑了不会有任何输出）",
                    subject=target,
                )
            )
    return findings


def _launch_files(root: Path) -> dict[str, set[str]]:
    """包名 → 该包 launch 目录下的文件名集合。"""
    index: dict[str, set[str]] = {}
    src = root / "ros2_ws" / "src"
    if not src.is_dir():
        return index
    for package_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        launch_dir = package_dir / "launch"
        if launch_dir.is_dir():
            index[package_dir.name] = {p.name for p in launch_dir.glob("*.launch.py")}
    return index


def _check_launch_commands(root: Path, document: str, text: str) -> list[Finding]:
    index = _launch_files(root)
    findings: list[Finding] = []
    for package, filename in sorted(set(_LAUNCH_RE.findall(text))):
        subject = f"{package}/{filename}"
        known = index.get(package)
        if known is None:
            findings.append(
                Finding(
                    "launch-missing", document, f"包 {package} 没有 launch 目录",
                    subject=subject,
                )
            )
        elif filename not in known:
            findings.append(
                Finding(
                    "launch-missing",
                    document,
                    f"{package} 下没有 {filename}（现有：{', '.join(sorted(known))}）",
                    subject=subject,
                )
            )
    return findings


def _check_topics(
    document: str, text: str, source_index: str, node_names: set[str]
) -> list[Finding]:
    findings: list[Finding] = []
    for topic in sorted(set(_TOPIC_RE.findall(text))):
        if topic.lstrip("/") in node_names:
            continue  # 这是节点名（如 `ros2 param get /mission_manager`），不是话题
        if topic not in source_index:
            findings.append(
                Finding(
                    "topic-unknown",
                    document,
                    f"话题 {topic} 在源码里找不到（改名了？还是文档写错了？）",
                    subject=topic,
                )
            )
    return findings


# --------------------------------------------------------------------------
# 豁免声明
# --------------------------------------------------------------------------


def collect_suppressions(document: str, text: str) -> list[Suppression]:
    """解析文档里的 `<!-- docs-audit: allow <code> <subject>; ... -->` 声明。

    用途只有一个：**文档本来就是在报告一个缺失的东西**时（例如本仓库的历史文档
    提到了一个已归档的文件），检查器不该把「文档在说它缺失」判成「文档写错了」。
    豁免必须写进文档本身（**单独一行**）、且只针对 (code, subject) 精确匹配，
    报告里还会被打印出来。
    """
    suppressions: list[Suppression] = []
    seen: set[tuple[str, str]] = set()
    for block in _SUPPRESSION_RE.findall(text):
        for item in block.split(";"):
            parts = item.split()
            if len(parts) < 2:
                continue
            code = parts[0].strip()
            subject = " ".join(parts[1:]).strip()
            if not code or not subject or (code, subject) in seen:
                continue
            seen.add((code, subject))
            suppressions.append(Suppression(code, subject, document))
    return suppressions


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def audit_document(
    root: Path,
    relative: str,
    source_index: str | None = None,
    node_names: set[str] | None = None,
) -> list[Finding]:
    """审计一份文档，返回 findings（空列表 = 没发现问题）。

    已按文档内的豁免声明过滤；被豁免的条目由 `audit_details()` 单独报出来。
    """
    return audit_details(root, (relative,), source_index, node_names).findings


def audit_details(
    root: Path,
    documents: tuple[str, ...] = CURRENT_DOCUMENTS,
    source_index: str | None = None,
    node_names: set[str] | None = None,
) -> AuditResult:
    """审计并同时给出「被豁免的条目」，供命令行报告使用。"""
    index = build_source_index(root) if source_index is None else source_index
    names = build_node_names(root) if node_names is None else node_names
    findings: list[Finding] = []
    suppressions: list[Suppression] = []
    for document in documents:
        path = root / document
        if not path.is_file():
            findings.append(Finding("path-missing", document, "登记的文档本身不存在"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        declared = collect_suppressions(document, text)
        suppressions.extend(declared)
        allowed = {(item.code, item.subject) for item in declared}
        raw: list[Finding] = []
        raw.extend(_check_paths(root, document, text))
        raw.extend(_check_python_commands(root, document, text))
        raw.extend(_check_launch_commands(root, document, text))
        raw.extend(_check_topics(document, text, index, names))
        findings.extend(f for f in raw if (f.code, f.subject) not in allowed)
    return AuditResult(findings=findings, suppressions=suppressions)


def audit(root: Path, documents: tuple[str, ...] = CURRENT_DOCUMENTS) -> list[Finding]:
    """审计登记表里的全部文档（只要 findings，不含豁免明细）。"""
    return audit_details(root, documents).findings


def format_report(
    findings: list[Finding],
    documents: tuple[str, ...] = CURRENT_DOCUMENTS,
    suppressions: list[Suppression] | None = None,
) -> str:
    lines = [
        f"文档一致性审计：{len(documents)} 份当前执行文档",
        "范围：路径 / python 命令 / launch 文件 / 话题名 是否存在。"
        "不含参数值与流程安全（那些要靠测试与真车）。",
    ]
    # 豁免必须可见：静默豁免等于把检查悄悄关掉
    for suppression in suppressions or []:
        lines.append(f"（已声明豁免）{suppression.render()}")
    if not findings:
        lines.append("未发现问题。")
        return "\n".join(lines)
    lines.append(f"发现 {len(findings)} 条不一致：")
    lines.extend(finding.render() for finding in findings)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - 现场手工运行
    parser = argparse.ArgumentParser(description="现场文档一致性审计")
    parser.add_argument("--root", default="", help="仓库根目录（默认取本文件的上一级）")
    parser.add_argument(
        "--all", action="store_true",
        help="连同历史文档一起审计（会有已知噪声，仅用于人工排查）",
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    documents = CURRENT_DOCUMENTS
    if args.all:
        documents = tuple(
            str(p.relative_to(root)).replace("\\", "/")
            for p in sorted((root / "docs").rglob("*.md"))
        )
    result = audit_details(root, documents)
    stream = getattr(sys, "stdout", None)
    if callable(getattr(stream, "reconfigure", None)):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
    print(format_report(result.findings, documents, result.suppressions))
    return 1 if result.findings else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
