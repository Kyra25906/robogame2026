"""按 launch 图算出「某节点最终生效的参数值」（静态解析，零 ROS）。

## 为什么需要它

`tools/launch_graph.py` 已经能答「这个节点有没有拿到 common/field 层」，
但答不了**语义问题**：`require_authorization` 这类参数「到底生效成什么值」——
它可能来自共用层、环境层，或被内联参数覆盖。

现实教训（2026-09-17）：巡线节点的授权门控先是写在共用层（`robot.yaml`），
于是「独立巡线联调图」也把它打开了——那张图里没有任务层，节点永远等不到授权，
**一动不动**。文本级断言（「文件里出现过/没出现过某个字符串」）查不出这类问题；
必须按「层 + 内联覆盖」的解析顺序算出最终值。

解析规则（与 ROS2 参数层语义一致，后写的覆盖先写的）：

    common(robot.yaml) → 环境层(mock/field/single) → 内联 dict

只做静态解析，不启动任何东西；解析不出来返回 `None`（表示「没写这个参数」），
调用方据此判断是自己写死默认值还是有显式配置。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LAYER_ASSIGNMENT = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*os\.path\.join\(\s*share\s*,\s*[\"']([^\"']+)[\"']"
    r"(?:\s*,\s*[\"']([^\"']+)[\"'])?",
    re.MULTILINE,
)


@dataclass
class NodeParameterSpec:
    """launch 里一个 Node(...) 的参数来源。"""

    package: str
    executable: str
    layers: list[str] = field(default_factory=list)
    inline: dict[str, Any] = field(default_factory=dict)


def layer_files(launch_path: Path) -> dict[str, Path]:
    """launch 源码里 `x = os.path.join(share, "config", "y.yaml")` → {x: 路径}。"""
    text = launch_path.read_text(encoding="utf-8")
    files: dict[str, Path] = {}
    for match in _LAYER_ASSIGNMENT.finditer(text):
        name, first, second = match.group(1), match.group(2), match.group(3)
        parts = [first] + ([second] if second else [])
        files[name] = Path(*parts)
    return files


def node_parameter_specs(launch_path: Path) -> list[NodeParameterSpec]:
    """解析 launch 里所有顶层 Node(...) 的层引用与内联参数（含字面量值）。"""
    tree = ast.parse(launch_path.read_text(encoding="utf-8"))
    specs: list[NodeParameterSpec] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Node"
        ):
            continue
        package = executable = None
        layers: list[str] = []
        inline: dict[str, Any] = {}
        for keyword in node.keywords:
            if keyword.arg == "package" and isinstance(keyword.value, ast.Constant):
                package = keyword.value.value
            elif keyword.arg == "executable" and isinstance(keyword.value, ast.Constant):
                executable = keyword.value.value
            elif keyword.arg == "parameters" and isinstance(keyword.value, ast.List):
                for item in keyword.value.elts:
                    if isinstance(item, ast.Name):
                        layers.append(item.id)
                    elif isinstance(item, ast.Dict):
                        for key, value in zip(item.keys, item.values):
                            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                                inline[key.value] = _literal(value)
        if isinstance(package, str) and isinstance(executable, str):
            specs.append(
                NodeParameterSpec(package, executable, layers, inline)
            )
    return specs


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _yaml_value(yaml_path: Path, node_name: str, param_name: str) -> Any | None:
    """从 `node_name: ros__parameters:` 段里取一个参数值（手写解析，容忍无 PyYAML）。"""
    if not yaml_path.is_file():
        return None
    text = yaml_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    inside_node = inside_params = False
    for line in lines:
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip())
        body = stripped.strip()
        if indent == 0 and body.endswith(":"):
            inside_node = body[:-1].strip() == node_name
            inside_params = False
            continue
        if not inside_node:
            continue
        if indent == 2 and body.endswith(":"):
            inside_params = body[:-1].strip() == "ros__parameters"
            continue
        if inside_params and indent >= 4:
            key, _, raw = body.partition(":")
            if key.strip() != param_name:
                continue
            value = raw.strip()
            if value.lower() in ("true", "false"):
                return value.lower() == "true"
            try:
                return int(value)
            except ValueError:
                pass
            try:
                return float(value)
            except ValueError:
                pass
            if value.startswith("[") and value.endswith("]"):
                return [
                    item.strip().strip("'\"")
                    for item in value[1:-1].split(",")
                    if item.strip()
                ]
            return value.strip("'\"")
    return None


def effective_parameter(
    launch_path: Path,
    package: str,
    executable: str,
    param_name: str,
    *,
    share_root: Path | None = None,
) -> Any | None:
    """算出该图里这个节点最终生效的参数值（层顺序 + 内联覆盖，后者赢）。

    `share_root` 默认取 launch 文件的上级目录的上级（`.../robogame_bringup`），
    与 `get_package_share_directory("robogame_bringup")` 在安装后的语义一致。
    返回 `None` 表示**没有任何一层写过这个参数**（即会用节点代码里的默认值）。
    """
    specs = [
        spec
        for spec in node_parameter_specs(launch_path)
        if spec.package == package and spec.executable == executable
    ]
    if not specs:
        return None
    spec = specs[0]
    files = layer_files(launch_path)
    root = share_root if share_root is not None else launch_path.resolve().parent.parent
    value: Any | None = None
    for layer in spec.layers:  # 后声明的层覆盖先声明的层
        reference = files.get(layer)
        if reference is None:
            continue
        path = reference if reference.is_absolute() else root / reference
        found = _yaml_value(path, executable, param_name)
        if found is not None:
            value = found
    if param_name in spec.inline:
        value = spec.inline[param_name]
    return value
