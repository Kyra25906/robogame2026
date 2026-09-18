"""文档盘点：给仓库里每一份文档贴一个「现在还算不算数」的分类，并机械校验这个分类没说谎。

## 为什么需要它（真实动机）

2026-09-18 做文档盘点时发现的问题不是「某份文档写错了某个字」，而是**没人知道该信哪一份**：

- `docs/` 下有 80+ 份 markdown，其中既有「现在照它做动作」的执行清单，也有
  8 月初的日志、已作废的计划、某个 agent 当天的工作留痕。它们**长得一样**，
  只有打开读前几行才知道算不算数。照着过期文档做，会得到**假通过**——
  比报错更危险（R13 已经踩过一次：文档让人跑的脚本没有 `__main__`，跑了没输出）。
- 还有一批文档**根本不在版本库里**（`*.docx`、`*.pdf` 被 `.gitignore` 排除，
  `tmp/`、`outputs/`、`results/` 同样）。它们和入库文档混在同一个目录树下，
  光看目录分不出来。clone 到树莓派的人**看不到**这些文件，却可能被别处的链接指向它们。

所以本模块做两件机械可判定的事：

1. **分类登记表**：每一份文档恰好属于一个类别（见 `CATEGORY_MEANING`）；
2. **校验分类没说谎**：
   - 仓库里出现的 `.md` 必须在登记表里（`doc-unregistered`）——新文档不能悄悄绕过分类；
   - 登记表里的路径必须真的存在（`registry-stale`）——改名/删除后登记表要跟着改；
   - 标为「已过期」的文档**必须有横幅**（`archived-without-banner`）——横幅是给读者看的，
     不是给工具看的，所以要求它出现在正文里；
   - 「当前执行」类文档必须被 `tools/docs_audit.py` 覆盖（`current-not-audited`）——
     否则「文档审计通过」只是「没检查」，这是最容易被误读的一种通过。

## 覆盖范围（别高估它）

- 它只回答「这份文档属于哪一类、分类是否自洽」。**不检查内容对不对**——
  内容与代码的一致性归 `tools/docs_audit.py`（路径/命令/launch/话题是否存在）
  和人工复核（参数值、流程安全）。
- 分类是**人做的判断**（写死在 `REGISTRY` 里），工具只负责让这个判断可被 review、
  不可悄悄漂移。所以改分类要改这个文件，并且会在 `git diff` 里显出来。

用法：

```bash
python3 tools/docs_inventory.py            # 只检查分类是否自洽（退出码 0 = 通过）
python3 tools/docs_inventory.py --write    # 顺带把 docs/DOC_INVENTORY.md 重新生成
python3 tools/docs_inventory.py --list     # 打印全部分类（人工 review 用）
```
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# 分类定义
# --------------------------------------------------------------------------

#: 类别 → 含义。判定标准只有一个：**读者现在该拿它做什么**。
CATEGORY_MEANING: dict[str, str] = {
    "current": "现在照它做动作（命令、步骤、清单）。",
    "reference": "现在照它做判断（协议字节、参数含义、接口责任、规则对照）。长期有效，按需查。",
    "history": "某一天的事实与决策，内容冻结。只用于回看与交接，不用于执行。",
    "archived": "已确认过期：**不要照做**。正文必须有「> ⚠️ 已过期」横幅并写明替代文档。",
    "teaching": "教学材料，面向学习，不参与现场执行。",
}

#: 报告与生成文档里的固定顺序（从「最该看」到「最不该看」）
CATEGORY_ORDER: tuple[str, ...] = ("current", "reference", "history", "archived", "teaching")

#: 「已过期」文档必须写进正文的横幅前缀（单独一行，放在开头才看得见）
ARCHIVED_BANNER = "> ⚠️ 已过期"

#: 检查横幅时只看开头这么多行——横幅写在文档末尾等于没写
BANNER_SEARCH_LINES = 40

# --------------------------------------------------------------------------
# 分类登记表
# --------------------------------------------------------------------------
#
# 规则（改这个表前先读）：
#   - 一份文档只能出现在一个类别里（工具会把重复登记报出来）；
#   - 「当前执行」是**承诺**：写进 current 就意味着它必须被 docs_audit 覆盖、必须能照做；
#   - 拿不准时选 reference 或 history，不要为了好看塞进 current；
#   - 历史文档**不改内容**（改了就不是留痕了）。要让它不再被误用，就用 archived + 横幅，
#     或者就地更正并注明更正日期（本项目既有做法）。
REGISTRY: dict[str, tuple[str, ...]] = {
    # ---- 现在照它做动作 ----
    "current": (
        "AGENTS.md",
        "README.md",
        "docs/README.md",
        "docs/AGENT.md",
        "docs/ENGINEERING_DISCIPLINE.md",
        "docs/TODO_AND_ISSUES.md",
        "docs/field/首次上车执行清单.md",
        "docs/field/上电自主完赛流程.md",
        "docs/field/现场待测清单_B2B3_待填值.md",
        "docs/field/真车对接设计稿_2026-08-19.md",
        "docs/field/FIELD_SESSION_CHECKLIST.md",
        "docs/field/FAULT_INJECTION_TEST_CARD.md",
        "docs/field/INTEGRATION_CHECKLIST.md",
        "docs/guides/RASPBERRY_PI_SSH_AND_WEB_GUIDE.md",
        "docs/guides/FIELD_DASHBOARD.md",
        "docs/guides/FIELD_CONSOLE.md",
        "docs/guides/GETTING_STARTED.md",
        "docs/guides/GRASP_ALIGNMENT_WEB.md",
    ),
    # ---- 现在照它做判断（事实来源） ----
    "reference": (
        "docs/DOC_INVENTORY.md",
        "docs/RULE_COVERAGE.md",
        "docs/术语表.md",
        "docs/树莓派_STM32机械机构通信协议_v1.0.md",
        "docs/calibration/四自由度眼在手上_操作说明.md",
        "docs/field/STM32_SERIAL_PROTOCOL_V1.md",
        "docs/field/算法给电控与机械的接口说明.md",
        "docs/field/GENERAL_FIELD_MAP_2026.md",
        "docs/field/给机械组现场问答表_2026-08-19.md",
        "docs/field/给机械组待确认清单.md",
        "docs/field/给电控组待确认清单.md",
        "docs/field/给硬件组的交接手册_2026-08-12.md",
        "docs/field/MECHANICAL_PARAMETERS_CONFIRMATION.md",
        "docs/field/MANIPULATOR_ACTION_EXTENSION.md",
        "docs/field/MECHANISM_0x20_INTERFACE_ALIGNMENT_2026-08-18.md",
        "docs/field/LINE_TELEMETRY_0x14_INTERFACE_ALIGNMENT_2026-08-19.md",
        "docs/field/evidence/localization/README.md",
        "docs/guides/Windows本地Python环境说明.md",
        "docs/guides/Windows到Ubuntu一键同步.md",
        "docs/line_follow/README.md",
        "docs/line_follow/CALIBRATION_AND_HARDWARE.md",
        "docs/line_follow/LINE_TELEMETRY_0x14_STM32_TEMPLATE.md",
        "docs/team/两人算法最终分工.md",
        "docs/team/GitHub两人代码协作说明.md",
        "docs/vision/VISION_MODULE.md",
        "docs/vision/连续帧确认使用说明.md",
        "tools/工具路径记录.md",
        "ros2_ws/src/cube_perception/README.md",
        "ros2_ws/src/localization/README.md",
        "ros2_ws/src/manipulator_client/README.md",
        "ros2_ws/src/mission_manager/README.md",
        "ros2_ws/src/motion_control/README.md",
        "ros2_ws/src/robot_bridge/README.md",
        "ros2_ws/src/robogame_interfaces/README.md",
        "ros2_ws/src/robogame_core/README.md",
        "ros2_ws/src/robogame_bringup/README.md",
    ),
    # ---- 历史留痕（内容冻结，只回看） ----
    "history": (
        "docs/history/B3_WORK_LOG_2026-09-17.md",
        "docs/history/DEV_LOG_2026-08-04.md",
        "docs/history/DEVELOPMENT_PLAN_2026-08-06_TO_14.md",
        "docs/history/INTEGRATION_HANDOFF_2026-08-12.md",
        "docs/history/INTEGRATION_HANDOFF_2026-08-13.md",
        "docs/history/PAUSE_HANDOFF_2026-08-06.md",
        "docs/history/RoboGame2026从远程开发到现场推进_大白话总结.md",
        "docs/history/RoboGame2026分支整理建议_2026-08-08.md",
        "docs/history/SESSION_2026-08-08_SUMMARY.md",
        "docs/history/SESSION_2026-08-19_SUMMARY.md",
        "docs/RoboGame2026_本对话树莓派软件推进总结_2026-08-15.md",
        "docs/RoboGame2026_第三轮复审修复方案_2026-08-17.md",
        "docs/field/ARM_GPIO_FIND_PIN_TEST_2026-08-19.md",
        "docs/field/CHASSIS_ENABLE_CHAIN_2026-08-18.md",
        "docs/field/ODOM_DROP_ROOT_CAUSE_2026-08-19.md",
        "docs/field/FIELD_MEASUREMENT_PLAN_2026-08-18.md",
        "docs/field/RASPBERRY_PI_DEPLOYMENT_LOG_2026-08-10.md",
        "docs/field/RASPBERRY_PI_DEPLOYMENT_LOG_2026-08-18.md",
        "docs/field/RASPBERRY_PI_REAL_CAR_AGENT_HANDOFF_2026-08-12.md",
        "docs/field/REAL_CAR_HARDWARE_PRELIMINARY_INVENTORY_2026-08-09.md",
        "docs/field/单相机模拟双相机性能测试_2026-08-15.md",
        "docs/team/Claude建议_相机方案参考_2026-08-18.md",
        "docs/team/给第二位算法同学的任务清单.md",
        "docs/team/给电控机械的最小请求单_2026-08-18.md",
        "docs/team/第二位算法同学Git提交说明.md",
        "docs/team/算法一执行队列_2026-08-18.md",
        "docs/vision/CURRENT_STATUS_HANDOFF_REAL_CAMERA_2026-08-11.md",
        "docs/vision/GF100_CAMERA_CALIBRATION_CANDIDATE_REPORT_2026-08-12.md",
        "docs/vision/GF100_DATA_COLLECTION_GUIDE_2026-08-11.md",
        "docs/vision/P1_ANGLE_AND_OCCLUSION_ANALYSIS_2026-08-12.md",
        "docs/vision/VISION_AGENT_HANDOFF_2026-08-12.md",
        "docs/vision/VISION_FIELD_TASKS_2026-08-12.md",
    ),
    # ---- 已过期（横幅是硬要求） ----
    "archived": (
        "docs/PLAN_2026-08-13_TO_18.md",
        "docs/field/FIELD_DAY1_EXECUTION_ORDER.md",
        "docs/field/_archived/FREEZE_TABLE.md",
        "docs/team/SPRINT_BOARD.md",
        # 任务书，但任务已交付：正文写「仓库里没有任何巡线代码」，照它开工就是白做一遍
        "docs/team/给第二位算法同学的任务_巡线模块.md",
    ),
    # ---- 教学 ----
    "teaching": (
        "docs/LEARNING_LOG.md",
        "docs/guides/BEGINNER_PROJECT_LEARNING_GUIDE.md",
        "docs/vision/TEACHING_INCREMENTAL_DEVELOPMENT_AGREEMENT.md",
        "lessons/AGENT_BRIEFING_2026-08-13.md",
        "lessons/HARDWARE_COMMS_LEARNING_ROADMAP.md",
        "lessons/LOCAL_LESSONS_README.md",
        "lessons/01_interfaces_core/README.md",
        "lessons/02_cube_perception/README.md",
    ),
}

#: 扫描 `.md` 时要跳过的目录前缀（工作目录 / 生成目录 / 厂商源码）。
#: 它们不是「文档」，登记表管不着；但要在盘点报告里单独列出来，否则「盘点」是假的。
EXCLUDED_PREFIXES: tuple[str, ...] = (
    ".git/",
    ".claude/",
    "tmp/",
    "outputs/",
    "results/",
    "Four_Motor_PID_Test_1/",
    "_inspect_four_motor/",
    "_dialogue_doc_work/",
    "_dialogue_doc_render/",
    "_merge_remote_original/",
    "ros2_ws/build/",
    "ros2_ws/install/",
    "ros2_ws/log/",
    "lessons/01_interfaces_core/output/",
    "lessons/02_cube_perception/output/",
)
#: 路径中任意位置出现这些片段也跳过（缓存、厂商源码、第三方目录）
EXCLUDED_CONTAINS: tuple[str, ...] = ("/__pycache__/", "/Drivers/", "/Middlewares/", "/node_modules/")

# --------------------------------------------------------------------------
# 不入库的东西：为什么不在版本库里
# --------------------------------------------------------------------------
#
# 每一条都必须对应 `.gitignore` 里真实存在的一行（tests/test_docs_inventory.py 会核对），
# 否则时间一长，这里就会变成「对 .gitignore 的猜测」。


@dataclass(frozen=True)
class IgnoredRule:
    """一条「不入库」规则：位置 + 匹配方式 + .gitignore 里对应的那一行。"""

    location: str  # 仓库相对路径（文件或目录）
    kind: str  # 文件扩展 / 目录
    ignores_line: str  # .gitignore 里的原文（必须真实存在）
    reason: str


IGNORED_RULES: tuple[IgnoredRule, ...] = (
    IgnoredRule("docs", "*.docx", "*.docx", "由 tools/build_*.py 生成的可打印版，字节大且逐次重生成"),
    IgnoredRule(
        "docs/calibration/checkerboard_A4_20mm_9x6.pdf",
        "文件",
        "*.pdf",
        "由同目录 svg 生成，需要时重生成",
    ),
    IgnoredRule("tmp", "目录", "tmp/", "临时脚本与中间产物"),
    IgnoredRule("outputs", "目录", "outputs/", "生成的可打印文档"),
    IgnoredRule("results", "目录", "results/", "视觉原始视频/图片与报告（体积大，必须另行备份）"),
    IgnoredRule("_dialogue_doc_work", "目录", "_dialogue_doc_work/", "对话归档工程的中间文件"),
    IgnoredRule("_inspect_four_motor", "目录", "_inspect_four_motor/", "只读解包的厂商固件工程"),
    IgnoredRule(
        "Four_Motor_PID_Test_1", "目录", "Four_Motor_PID_Test_1/", "厂商 Keil 工程（含 CMSIS/HAL 与编译产物）"
    ),
)

#: 根目录下的「外部素材」：不是本项目产物，但盘点上必须知道它们只在本地
ROOT_EXTERNAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("*.pdf", "组委会发布的规则手册/评分细则（外部素材，不随仓库分发）"),
    ("*.docx", "外部对话记录/第三方生成的文档（*.docx 被 .gitignore 排除）"),
    ("*.zip", "打包快照"),
    ("*.pack", "Keil 厂商器件支持包，单个近 300MB"),
)

# --------------------------------------------------------------------------
# 检查
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """一条分类问题。`code` 机械可判定，`detail` 给人看。"""

    code: str
    detail: str
    subject: str = ""

    def render(self) -> str:
        return f"[{self.code}] {self.detail}"


@dataclass(frozen=True)
class Document:
    """一份被登记（或被扫描到）的文档。"""

    path: str  # 仓库相对路径，posix 分隔
    category: str  # 类别（未登记时为空串）
    title: str  # 正文第一个 H1，取不到就用空串
    updated: str  # 最后修改时间 YYYY-MM-DD

    @property
    def in_git_expected(self) -> bool:
        """登记在册的文档都应当在版本库里（不入库的那批走 IGNORED_RULES）。"""
        return bool(self.category)


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


def _is_excluded(relative: str) -> bool:
    if relative.startswith(EXCLUDED_PREFIXES):
        return True
    return any(part in relative for part in EXCLUDED_CONTAINS)


def discover_markdown(root: Path) -> list[str]:
    """扫出所有该被分类的 `.md`（工作目录与生成目录不算文档）。"""
    found: list[str] = []
    for path in sorted(root.rglob("*.md")):
        relative = _posix(path.relative_to(root))
        if _is_excluded(relative):
            continue
        found.append(relative)
    return found


_TITLE_RE = re.compile(r"^#[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def read_title(path: Path) -> str:
    """取第一个 H1 当标题（摘要用；取不到就返回空串，不要编）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - 权限问题不该让盘点整体失败
        return ""
    match = _TITLE_RE.search(text)
    return match.group(1).strip() if match else ""


def _updated(path: Path) -> str:
    try:
        stamp = datetime.datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:  # pragma: no cover
        return ""
    return stamp.strftime("%Y-%m-%d")


def _has_archived_banner(line: str) -> bool:
    """这一行是不是「已过期」横幅？

    允许 Markdown 的加粗写法（`> ⚠️ **已过期**`）——本项目已有文档是这么写的，
    检查器不该逼人为了一行样式去改留痕。判定前先去掉 `*` 和空白。
    """
    return line.replace("*", "").strip().startswith(ARCHIVED_BANNER)


def has_archived_banner(text: str) -> bool:
    """文档正文前 `BANNER_SEARCH_LINES` 行里有没有「已过期」横幅。

    只看开头：横幅写在文档末尾等于没写（读者看不到就已经照做了）。
    """
    for line in text.splitlines()[:BANNER_SEARCH_LINES]:
        if _has_archived_banner(line):
            return True
    return False


def registry_entries(registry: dict[str, tuple[str, ...]] | None = None) -> list[tuple[str, str]]:
    """展开登记表成 (类别, 路径) 列表；顺序按 CATEGORY_ORDER + 登记顺序。

    `registry` 可注入：测试用假登记表，这样「分类检查器」本身也能被验证
    （不然就只能对着真仓库看它是不是永远报 0——一个永远报 0 的检查器比没有检查器更糟）。
    """
    table = REGISTRY if registry is None else registry
    entries: list[tuple[str, str]] = []
    for category in CATEGORY_ORDER:
        for path in table.get(category, ()):
            entries.append((category, path))
    return entries


def audited_documents() -> tuple[str, ...]:
    """取 docs_audit 的登记表（拿不到就返回空：检查会降级成「无法判定」，不假装通过）。"""
    try:
        from docs_audit import CURRENT_DOCUMENTS  # type: ignore import-not-found
    except Exception:  # pragma: no cover - 只有在 tools/ 不在 sys.path 时才会发生
        return ()
    return tuple(CURRENT_DOCUMENTS)


def check(
    root: Path,
    audited: tuple[str, ...] | None = None,
    registry: dict[str, tuple[str, ...]] | None = None,
) -> list[Finding]:
    """检查分类是否完整、是否自洽。`audited` / `registry` 可注入（测试用）。"""
    table = REGISTRY if registry is None else registry
    findings: list[Finding] = []
    entries = registry_entries(table)
    registered = {path: category for category, path in entries}

    # 重复登记（同一份文档出现在两个类别）：分类必须是唯一的，否则「它算不算数」没有答案
    seen: dict[str, str] = {}
    for category, path in entries:
        if path in seen:
            findings.append(
                Finding("duplicate-registration", f"{path} 同时登记为 {seen[path]} 和 {category}", path)
            )
        else:
            seen[path] = category

    # 登记了但文件不存在：分类变成谎话（改名/删除后没同步）
    for category, path in entries:
        if not (root / path).is_file():
            findings.append(
                Finding("registry-stale", f"登记为「{category}」但文件不存在：{path}", path)
            )

    # 扫到但没登记：新文档不能悄悄绕过分类
    for relative in discover_markdown(root):
        if relative not in registered:
            findings.append(
                Finding(
                    "doc-unregistered",
                    f"{relative} 没有被分类（往 tools/docs_inventory.py 的 REGISTRY 里加一行）",
                    relative,
                )
            )

    # 已过期必须有横幅：写在正文里给人看，不是给工具看
    for path in table.get("archived", ()):
        target = root / path
        if not target.is_file():
            continue
        head = target.read_text(encoding="utf-8", errors="replace")
        if not has_archived_banner(head):
            findings.append(
                Finding(
                    "archived-without-banner",
                    f"{path} 标为已过期，但开头 {BANNER_SEARCH_LINES} 行里没有「{ARCHIVED_BANNER}」横幅",
                    path,
                )
            )

    # 「当前执行」必须被 docs_audit 覆盖
    if audited is None:
        audited = audited_documents()
    if not audited:
        findings.append(
            Finding(
                "audit-list-unavailable",
                "取不到 tools/docs_audit.py 的 CURRENT_DOCUMENTS（tools/ 不在 sys.path？）——"
                "这项检查降级为「无法判定」，不要当成通过",
            )
        )
    else:
        audited_set = set(audited)
        for path in table.get("current", ()):
            if path not in audited_set:
                findings.append(
                    Finding(
                        "current-not-audited",
                        f"{path} 自称当前执行文档，但不在 docs_audit 的登记表里"
                        "（「审计通过」会变成「没检查」）",
                        path,
                    )
                )
        for path in sorted(audited_set - set(registered)):
            findings.append(
                Finding(
                    "audited-not-registered",
                    f"{path} 在 docs_audit 登记表里，却没在本工具的分类里（两处登记必须一致）",
                    path,
                )
            )
    return findings


# --------------------------------------------------------------------------
# 盘点报告（生成 docs/DOC_INVENTORY.md）
# --------------------------------------------------------------------------


def collect(root: Path) -> list[Document]:
    """收集全部已登记文档的现状（标题 / 最后更新）。"""
    documents: list[Document] = []
    for category, path in registry_entries():
        target = root / path
        documents.append(
            Document(
                path=path,
                category=category,
                title=read_title(target) if target.is_file() else "",
                updated=_updated(target) if target.is_file() else "",
            )
        )
    return documents


@dataclass(frozen=True)
class IgnoredArea:
    """一块「不在版本库里」的东西。"""

    location: str
    kind: str
    files: int
    examples: tuple[str, ...]
    ignores_line: str
    reason: str


def _count_files(target: Path) -> tuple[int, list[str]]:
    if target.is_file():
        return 1, [target.name]
    if not target.is_dir():
        return 0, []
    total = 0
    examples: list[str] = []
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        total += 1
        if len(examples) < 3:
            examples.append(_posix(path.relative_to(target)))
    return total, examples


def scan_ignored_areas(root: Path) -> list[IgnoredArea]:
    """列出「不入库」的区域，并给出真实文件数（不是猜的）。"""
    areas: list[IgnoredArea] = []
    for rule in IGNORED_RULES:
        target = root / rule.location
        if not target.exists():
            continue
        if rule.kind.startswith("*"):
            suffix = rule.kind.lstrip("*")
            files = [p for p in target.glob(rule.kind) if p.is_file()]
            areas.append(
                IgnoredArea(
                    location=f"{rule.location}/{rule.kind}",
                    kind=rule.kind,
                    files=len(files),
                    examples=tuple(p.name for p in files[:3]),
                    ignores_line=rule.ignores_line,
                    reason=rule.reason,
                )
            )
            continue
        total, examples = _count_files(target)
        areas.append(
            IgnoredArea(
                location=rule.location,
                kind=rule.kind,
                files=total,
                examples=tuple(examples),
                ignores_line=rule.ignores_line,
                reason=rule.reason,
            )
        )
    for pattern, reason in ROOT_EXTERNAL_PATTERNS:
        files = [p for p in root.glob(pattern) if p.is_file()]
        if not files:
            continue
        areas.append(
            IgnoredArea(
                location=f"（仓库根）{pattern}",
                kind="外部素材",
                files=len(files),
                examples=tuple(p.name for p in files[:3]),
                ignores_line=pattern if pattern in ("*.pdf", "*.docx") else pattern,
                reason=reason,
            )
        )
    return areas


def render_inventory(root: Path, documents: list[Document], areas: list[IgnoredArea]) -> str:
    """生成 `docs/DOC_INVENTORY.md` 正文。"""
    lines: list[str] = [
        "# 文档盘点与分类（DOC_INVENTORY）",
        "",
        "> **本文件由 `tools/docs_inventory.py --write` 生成，不要手改**——",
        "> 手改会在下一次生成时被覆盖，而且会让「分类」和「登记表」不一致。",
        "> 要改分类：改 `tools/docs_inventory.py` 里的 `REGISTRY`，再重新生成。",
        "",
        "本页回答一个问题：**这份文档现在还算不算数**。判定标准只有一条——",
        "读者现在该拿它做什么。内容与代码是否一致由 `tools/docs_audit.py` 与人工复核负责，",
        "本页不重复。",
        "",
        "## 分类规则",
        "",
        "| 类别 | 含义 | 硬要求 |",
        "|---|---|---|",
    ]
    requirement = {
        "current": "必须被 `tools/docs_audit.py` 覆盖（否则「审计通过」=「没检查」）",        "reference": "无（但改了事实来源要同步引用它的文档）",
        "history": "不改内容（改了就不是留痕）；被引用时要写明日期",
        "archived": f"正文开头必须有 `{ARCHIVED_BANNER}` 横幅 + 替代文档",
        "teaching": "无",
    }
    for category in CATEGORY_ORDER:
        lines.append(
            f"| `{category}` | {CATEGORY_MEANING[category]} | {requirement[category]} |"
        )
    counts = {category: len(REGISTRY.get(category, ())) for category in CATEGORY_ORDER}
    lines += [
        "",
        "## 统计",
        "",
        "| 类别 | 份数 |",
        "|---|---|",
    ]
    for category in CATEGORY_ORDER:
        lines.append(f"| {category} | {counts[category]} |")
    lines.append(f"| **合计** | **{sum(counts.values())}** |")

    for category in CATEGORY_ORDER:
        rows = [doc for doc in documents if doc.category == category]
        lines += [
            "",
            f"## {category}（{len(rows)} 份）—— {CATEGORY_MEANING[category]}",
            "",
            "| 文档 | 标题 | 最后修改 |",
            "|---|---|---|",
        ]
        for doc in rows:
            lines.append(f"| `{doc.path}` | {doc.title or '—'} | {doc.updated or '—'} |")

    lines += [
        "",
        "## 不在版本库里的文档与目录（含 `.gitignore` 排除的）",
        "",
        "clone 到树莓派或别人的机器上时，**下面这些文件不会出现**。",
        "所以：不要在任何入库文档里把它们当成「可点开的链接」。",
        "",
        "| 位置 | 类型 | 文件数 | 示例 | `.gitignore` 依据 | 为什么不入库 |",
        "|---|---|---|---|---|---|",
    ]
    for area in areas:
        examples = "、".join(area.examples) if area.examples else "—"
        lines.append(
            f"| `{area.location}` | {area.kind} | {area.files} | {examples} | "
            f"`{area.ignores_line}` | {area.reason} |"
        )
    lines.append("")
    return "\n".join(lines)


def format_report(findings: list[Finding], documents: list[Document] | None = None) -> str:
    lines = ["文档盘点：分类登记表自洽性检查"]
    if documents is not None:
        counts = {category: 0 for category in CATEGORY_ORDER}
        for doc in documents:
            counts[doc.category] = counts.get(doc.category, 0) + 1
        lines.append(
            "登记：" + "、".join(f"{category} {counts[category]}" for category in CATEGORY_ORDER)
        )
    if not findings:
        lines.append("未发现问题。")
        return "\n".join(lines)
    lines.append(f"发现 {len(findings)} 条问题：")
    lines.extend(finding.render() for finding in findings)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - 现场手工运行
    parser = argparse.ArgumentParser(description="文档盘点与分类自洽性检查")
    parser.add_argument("--root", default="", help="仓库根目录（默认取本文件的上一级）")
    parser.add_argument("--write", action="store_true", help="重新生成 docs/DOC_INVENTORY.md")
    parser.add_argument("--list", action="store_true", help="打印全部分类（人工 review）")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]

    stream = getattr(sys, "stdout", None)
    if callable(getattr(stream, "reconfigure", None)):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

    documents = collect(root)
    if args.list:
        for doc in documents:
            print(f"{doc.category:9s} {doc.path}")
    findings = check(root)
    print(format_report(findings, documents))
    if args.write:
        areas = scan_ignored_areas(root)
        target = root / "docs" / "DOC_INVENTORY.md"
        target.write_text(render_inventory(root, documents, areas), encoding="utf-8", newline="\n")
        print(f"已写入 {target.relative_to(root)}")
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
