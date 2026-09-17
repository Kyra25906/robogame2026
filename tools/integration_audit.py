"""集成就绪审计：把「单点测试都过、接起来对不上」的风险查出来（静态，零 ROS）。

## 为什么需要它

到目前为止的接线验证都是**单文件**的：`mission_manager` 里断言它订阅了什么、
`line_follow_node` 里断言它订阅了什么、launch 图断言话题有没有发布者。
每一层都过，但**跨层的一致性**没人查。典型漏洞：

- 路线把某一段的底盘授权给了 `manipulator_client`，而那个节点**根本不看授权**；
- 某个段退出判据需要 `/pose`，而任务层某个版本没订阅它；
- 网页读 `payload.work_step_kind`，而任务层写的是 `work_step_type`（页面显示 undefined，
  没人报错）；
- 比赛配置里少了某个参数（例如 `calibration_file`），节点静默用默认值。

本模块把这些**跨文件不变量**做成可执行检查，输出 `Finding` 列表；
既能在 CI（`tests/test_integration_audit.py`）里守住，也能在树莓派上人工跑一遍
（`python3 tools/integration_audit.py`）看当前状态。

## 严重级别

- `blocker`：会让「上电自主」直接失效（授权无人执行、判据缺输入、面板字段对不上）；
- `risk`：不致命但会误导人或掩盖失败；
- `info`：登记信息（例如「某话题只在 mock 图里有发布者」）。
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

from launch_graph import node_topics, parse_launch_nodes

#: 段类型 / 作业步骤 -> 底盘授权来源（与 mission_dispatch 的表保持一致）
ROLE_ACTIVE_SOURCE = {
    "LINE": "line_follow",
    "RAMP_UP": "line_follow",
    "RAMP_DOWN": "line_follow",
    "SHIFT": "motion_control",
    "TURN": "motion_control",
    "WORK": "manipulator_client",
}
STEP_ACTIVE_SOURCE = {
    "PICK": "manipulator_client",
    "PLACE": "manipulator_client",
    "SHIFT": "motion_control",
}

#: 授权来源 -> 负责执行该授权的节点源码（相对 ros2_ws/src）
SOURCE_NODE_SOURCES = {
    "line_follow": "motion_control/motion_control/line_follow_node.py",
    "motion_control": "motion_control/motion_control/node.py",
    "manipulator_client": "manipulator_client/manipulator_client/node.py",
}

#: 段退出判据 -> 任务层必须具备的输入话题
EXIT_KIND_REQUIRED_TOPICS = {
    "JUNCTION_TURN": "/line_follow/status",
    "LINE_END": "/line_follow/status",
    "ODOM_DISTANCE": "/pose",
    "STOP_POINT": "/pose",
    "POSE_TOLERANCE": "/pose",
    "YAW_TARGET": "/pose",
    "WORK_DONE": "/manipulator/result",
}
#: 作业步骤推进来源 -> 必须订阅的结果话题
STEP_SOURCE_REQUIRED_TOPICS = {
    "manipulator": "/manipulator/result",
    "motion": "/motion/result",
}


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str  # blocker | risk | info
    detail: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.detail}"


def _node_source(src_root: Path, relative: str) -> Path:
    return src_root / relative


def _payload_keys(mission_node: Path) -> set[str]:
    """从 `_publish_route_status` 里取出 payload 的字面键名。"""
    tree = ast.parse(mission_node.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_publish_route_status":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Dict):
                    for key in inner.keys:
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            keys.add(key.value)
    # **progress 展开：把 route_progress 的键也算进来
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "route_progress":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Dict):
                    for key in inner.keys:
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            keys.add(key.value)
    return keys


def _panel_keys(web_root: Path) -> dict[str, set[str]]:
    """从面板 JS 里取出它从任务层载荷读的键（`data.<key>`）。"""
    used: dict[str, set[str]] = {}
    for path in sorted(web_root.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        keys = set(re.findall(r"\bdata\.([A-Za-z_][A-Za-z0-9_]*)", text))
        if keys:
            used[path.name] = keys
    return used


def audit(src_root: Path, *, web_root: Path | None = None) -> list[Finding]:
    """跑一遍跨文件一致性检查，返回发现列表（空 = 全部对齐）。"""
    findings: list[Finding] = []
    src_root = Path(src_root)
    web_root = Path(web_root) if web_root is not None else src_root.parents[2] / "tools" / "field_dashboard_web"
    mission_node = src_root / "mission_manager" / "mission_manager" / "node.py"
    if not mission_node.is_file():
        return [Finding("mission_node_missing", "blocker", f"找不到 {mission_node}")]

    # 1) 每一个授权来源都必须有节点真的执行它（订阅 /mission/active_source）
    for source, relative in SOURCE_NODE_SOURCES.items():
        path = _node_source(src_root, relative)
        if not path.is_file():
            findings.append(Finding(
                "authority_node_missing", "blocker",
                f"授权来源 {source} 的节点源码不存在：{relative}",
            ))
            continue
        subscribed, _published = node_topics(path)
        if "/mission/active_source" not in subscribed:
            findings.append(Finding(
                "authority_not_enforced", "blocker",
                f"路线会把底盘授权给 {source}（{relative.split('/')[-1]}），"
                "但该节点没有订阅 /mission/active_source —— 授权对它不构成约束",
            ))

    # 2) 每条段退出判据 / 每个作业步骤推进来源，任务层都必须订阅对应输入
    subscribed, published = node_topics(mission_node)
    for kind, topic in sorted(EXIT_KIND_REQUIRED_TOPICS.items()):
        if topic not in subscribed:
            findings.append(Finding(
                "exit_kind_without_input", "blocker",
                f"段退出判据 {kind} 需要 {topic}，但 mission_manager 没有订阅它",
            ))
    for source, topic in sorted(STEP_SOURCE_REQUIRED_TOPICS.items()):
        if topic not in subscribed:
            findings.append(Finding(
                "step_source_without_input", "blocker",
                f"作业步骤推进来源 {source} 需要 {topic}，但 mission_manager 没有订阅它",
            ))

    # 3) 网页读的字段必须真的在任务层载荷里
    payload = _payload_keys(mission_node)
    for name, keys in sorted(_panel_keys(web_root).items()):
        missing = sorted(key for key in keys if key not in payload and not _is_local_variable(key))
        if missing:
            findings.append(Finding(
                "panel_key_missing", "risk",
                f"{name} 读了载荷里没有的字段：{', '.join(missing)}（页面会显示 undefined）",
            ))

    # 4) 授权来源与作业步骤来源必须互相覆盖（不能出现「步骤要求一个来源，段表没它」）
    for source in sorted(set(STEP_ACTIVE_SOURCE.values())):
        if source not in set(ROLE_ACTIVE_SOURCE.values()):
            findings.append(Finding(
                "step_source_unknown", "risk",
                f"作业步骤来源 {source} 不在段类型授权表里：读代码的人会找不到对应关系",
            ))

    # 5) 现场启动图必须包含所有会被授权的节点（否则授权给了一个没起的节点）
    hardware = src_root / "robogame_bringup" / "launch" / "hardware.launch.py"
    if hardware.is_file():
        nodes = {executable for _package, executable in parse_launch_nodes(hardware)}
        expected_executables = {
            "line_follow": "line_follow_controller",
            "motion_control": "motion_controller",
            "manipulator_client": "manipulator_client",
        }
        for source, executable in expected_executables.items():
            if executable not in nodes:
                findings.append(Finding(
                    "authority_node_not_launched", "blocker",
                    f"现场图 hardware.launch.py 里没有 {executable}，"
                    f"但路线会把底盘授权给它（来源 {source}）",
                ))
    else:
        findings.append(Finding(
            "field_launch_missing", "blocker", f"找不到 {hardware}",
        ))

    # 6) 现场配置必须写清「上电自主」依赖的参数
    field_config = src_root / "robogame_bringup" / "config" / "robot_field.yaml"
    text = field_config.read_text(encoding="utf-8") if field_config.is_file() else ""
    for param in ("route_enabled", "field_layout_path", "degrade_on_failure",
                  "match_time_limit_s", "calibration_file"):
        if param not in text:
            findings.append(Finding(
                "field_param_missing", "risk",
                f"robot_field.yaml 里没写 {param}；现场只能靠代码默认值，"
                "上电行为会与预期不一致",
            ))

    return findings


#: 面板 JS 里从别处（不是任务层载荷）取的键，白名单（避免误报）
_PANEL_LOCAL_KEYS = {
    # odom_panel.js 用的是 snapshot 的其它段落
    "distance_result", "odom_calibration", "odom_trial_candidate", "task",
}


def _is_local_variable(key: str) -> bool:
    return key in _PANEL_LOCAL_KEYS


def format_report(findings: list[Finding]) -> str:
    if not findings:
        return "集成就绪审计：未发现问题（跨文件一致性全部对齐）"
    lines = [f"集成就绪审计：发现 {len(findings)} 项"]
    for severity in ("blocker", "risk", "info"):
        group = [finding for finding in findings if finding.severity == severity]
        if group:
            lines.append(f"  --- {severity}（{len(group)}）---")
            lines.extend(f"  {finding}" for finding in group)
    return "\n".join(lines)


def main() -> int:  # pragma: no cover - 现场手工运行
    import sys

    root = Path(__file__).resolve().parents[1] / "ros2_ws" / "src"
    findings = audit(root)
    print(format_report(findings))
    return 1 if any(finding.severity == "blocker" for finding in findings) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
