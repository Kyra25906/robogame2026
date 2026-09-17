"""路线加载器：把场地图 yaml 变成自检过的 `RoutePlan`（纯逻辑，零 ROS）。

## 为什么单独一个模块

「读哪份 `field_layout.yaml`」「怎么把它变成 `RoutePlan`」这两件事与 ROS 无关：
- ROS 节点（`mission_manager`）需要它；
- 网页面板（`tools/field_dashboard_core`）需要它显示路线表；
- 测试需要在开发机（无 rclpy、无 PyYAML）上验证它。

放在核心模块里，三处共用同一份实现与同一个兜底读取器——不会出现「网页显示的
场地」和「车实际跑的场地」来自两个不同解析器的情况。

## PyYAML 不是硬依赖

树莓派/Ubuntu 上应当装 `python3-yaml`（优先走 PyYAML）。开发机上常常没有，于是
提供一个**只针对本文件结构**的最小读取器 `survey_from_layout_text`：它只认
`survey:` 段下的 `line_nodes` / `line_edges` / `stops` 三种行内映射，结构一旦变化
就返回空段，由 `build_route_plan` 的自检报错——**不猜**。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping

from .mission_route import RoutePlan, build_route_plan

#: 场地图在仓库中的相对路径（开发机与未安装的部署用）
DEFAULT_LAYOUT_RELATIVE_PATH = "ros2_ws/src/robogame_bringup/config/field_layout.yaml"

SURVEY_SECTIONS = ("line_nodes", "line_edges", "stops")

_INLINE_KEY = re.compile(r"(?:^|[,\s{])([A-Za-z_]+)\s*:")


def _parse_inline_value(raw: str):
    value = raw.strip().strip("'\"")
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
    try:
        return float(value)
    except ValueError:
        return value


def _parse_inline_map(text: str) -> dict[str, Any]:
    """解析 `{type: turn, x: 0.7, note: 含，全角逗号}`。

    按「下一个已知键」切分而不是按逗号切分，这样 note 里的中文逗号不会打断解析。
    """
    inner = text.strip().lstrip("{").rstrip("}")
    matches = list(_INLINE_KEY.finditer(inner))
    result: dict[str, Any] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(inner)
        result[match.group(1)] = _parse_inline_value(inner[start:end].strip().rstrip(",").strip())
    return result


def survey_from_layout_text(text: str) -> dict[str, dict]:
    """从 `field_layout.yaml` 文本里取 survey 段（无 PyYAML 时的兜底读取器）。"""
    sections: dict[str, dict] = {name: {} for name in SURVEY_SECTIONS}
    in_survey = False
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        if indent == 0:
            in_survey = body.startswith("survey:")
            current = None
            continue
        if not in_survey:
            continue
        if indent == 2 and body.endswith(":"):
            name = body[:-1].strip()
            current = name if name in sections else None
            continue
        if indent >= 4 and current is not None and body.endswith("}"):
            entry_id, _, raw_map = body.partition(":")
            if raw_map.strip().startswith("{"):
                sections[current][entry_id.strip()] = _parse_inline_map(raw_map)
    return sections


def load_survey(layout_path: Path | str | None = None) -> Mapping[str, Any]:
    """读场地图 survey 段：优先 PyYAML，缺库时用兜底读取器。"""
    path = Path(layout_path or DEFAULT_LAYOUT_RELATIVE_PATH)
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        return survey_from_layout_text(text)
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict) or "survey" not in loaded:
        raise ValueError(f"{path} 缺少 survey 段")
    return loaded["survey"]


def resolve_field_layout_path(
    param_value: str = "",
    *,
    share_root: str | None = None,
    fallback_paths: tuple[str, ...] = (DEFAULT_LAYOUT_RELATIVE_PATH,),
) -> str:
    """决定读哪一份 `field_layout.yaml`。

    顺序：显式参数 → robogame_bringup 安装目录（`share_root` 或 ament）→ 兜底路径
    （默认仓库相对路径，供开发机）。返回空字符串表示都没找到——调用方必须按
    「路线不可用」处理，不许猜一份场地。
    """
    if param_value:
        return param_value
    candidates: list[str] = []
    if share_root:
        candidates.append(os.path.join(share_root, "config", "field_layout.yaml"))
    else:
        try:  # 只在需要时依赖 ament（开发机上不导入）
            from ament_index_python.packages import get_package_share_directory

            candidates.append(
                os.path.join(
                    get_package_share_directory("robogame_bringup"),
                    "config",
                    "field_layout.yaml",
                )
            )
        except Exception:
            pass
    candidates.extend(fallback_paths)
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def load_route_plan(layout_path: Path | str | None = None, *, rounds: int = 1) -> RoutePlan:
    """读场地图并构造自检过的路线；任何失败都抛异常（由调用方判失败）。

    `rounds > 1` 时把取存环重复多趟（见 `mission_route.build_route_plan`）。
    """
    if layout_path is None or str(layout_path) == "":
        raise FileNotFoundError("未提供 field_layout.yaml 路径（场地数据缺失）")
    return build_route_plan(load_survey(layout_path), rounds=rounds)
