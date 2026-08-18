"""A0.2 launch 图完整性静态分析（零 ROS 依赖，纯 AST）。

背景（执行队列 2026-08-18 A0 节）：P0-2「hardware.launch.py 无相机节点」
只存在于人工走读结论里，没有任何测试会红。本工具把 launch 图变成可断言的
事实：解析 launch 文件得到节点列表，经 setup.py entry_points 把 executable
映射到源码模块，再解析节点源码的 create_subscription / create_publisher，
最终判定「每个被订阅的话题是否有发布者」。

用法（测试入口）：
    python -c "from tools.launch_graph import analyze_all_launches; ..."

本模块不 import 任何 ROS 包，只读文件并用 ast 解析，Windows 上可直接跑。
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class LaunchGraphIssue:
    """一个被订阅但图中没有发布者的话题。"""

    launch: str  # launch 文件名（相对 ros2_ws/src/robogame_bringup/launch）
    topic: str  # 被订阅的话题
    subscriber: str  # 订阅它的 executable 名


# 外部 ROS 包节点的话题契约（源码不在本仓库，无法 AST 解析）。
# 键为 (package, executable)，值为 (订阅话题集, 发布话题集)。
# 只登记 B 阶段会用到的外部节点；新增时同步更新注释。
EXTERNAL_NODE_TOPICS: dict[tuple[str, str], tuple[set[str], set[str]]] = {
    # usb_cam（ros-jazzy-usb-cam）：默认发布 /image_raw 与 /camera_info，
    # 在 camera.launch.py 里 remap 到 /camera/image_raw 与 /camera/camera_info。
    # 参考：https://deepwiki.com/ros-drivers/usb_cam/4-configuration-guide
    ("usb_cam", "usb_cam_node_exe"): (set(), {"/image_raw", "/camera_info"}),
}


def _external_node_topics(
    package: str, executable: str
) -> tuple[set[str], set[str]] | None:
    return EXTERNAL_NODE_TOPICS.get((package, executable))


def _constant_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _launch_nodes_recursive(
    launch_path: Path, src_root: Path, seen: set[Path]
) -> list[tuple[str, str]]:
    """解析 launch 文件及其 include 的子 launch，返回 [(package, executable)]。

    IncludeLaunchDescription(PythonLaunchDescriptionSource(<path>)) 会被递归
    展开。``<path>`` 允许是 ``os.path.join(share, "launch", "x.launch.py")``
    形式（基于 robogame_bringup share 目录），或相对当前文件所在目录。
    """
    return [
        (package, executable)
        for package, executable, _source in _launch_nodes_with_source(
            launch_path, src_root, seen
        )
    ]


def _launch_nodes_with_source(
    launch_path: Path, src_root: Path, seen: set[Path]
) -> list[tuple[str, str, Path]]:
    """同 _launch_nodes_recursive，但每个节点附带它声明的 launch 文件路径。

    用于按节点所属 launch 文件应用 remappings（例如 usb_cam 的
    /image_raw -> /camera/image_raw 只在 camera.launch.py 里声明）。
    """
    resolved = launch_path.resolve()
    if resolved in seen:
        return []
    seen.add(resolved)
    tree = ast.parse(launch_path.read_text(encoding="utf-8"))
    nodes: list[tuple[str, str, Path]] = []
    launch_dir = launch_path.parent
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "Node":
            package = executable = None
            for keyword in node.keywords:
                if keyword.arg == "package":
                    package = _constant_string(keyword.value)
                elif keyword.arg == "executable":
                    executable = _constant_string(keyword.value)
            if package is not None and executable is not None:
                nodes.append((package, executable, launch_path))
            continue
        # IncludeLaunchDescription(PythonLaunchDescriptionSource(<path>))
        if isinstance(node.func, ast.Name) and node.func.id == "IncludeLaunchDescription":
            source_call = None
            if node.args:
                source_call = node.args[0]
            else:
                for keyword in node.keywords:
                    if keyword.arg in (None, "launch_description"):
                        source_call = keyword.value
                        break
            if source_call is None:
                continue
            if not (isinstance(source_call, ast.Call)
                    and isinstance(source_call.func, ast.Name)
                    and source_call.func.id == "PythonLaunchDescriptionSource"):
                continue
            included = None
            if source_call.args:
                included = _constant_string(source_call.args[0])
            elif source_call.keywords:
                for kw in source_call.keywords:
                    if kw.arg == "launch_file_path":
                        included = _constant_string(kw.value)
                        break
            if included is None:
                # PythonLaunchDescriptionSource(os.path.join(share, "launch",
                # "camera.launch.py"))：os.path.join 是一个嵌套 Call。
                # 提取其字符串实参（跳过变量 share），拼成相对 share 根的路径。
                path_call = source_call.args[0] if source_call.args else None
                if (
                    isinstance(path_call, ast.Call)
                    and isinstance(path_call.func, ast.Attribute)
                    and path_call.func.attr == "join"
                ):
                    parts = []
                    for arg in path_call.args:
                        part = _constant_string(arg)
                        if part is None:
                            continue
                        parts.append(part)
                    if parts:
                        included = os.path.join(*parts)
            if included is None:
                continue
            candidate = _resolve_launch_path(included, launch_dir, src_root)
            if candidate is not None and candidate.is_file():
                nodes.extend(_launch_nodes_with_source(candidate, src_root, seen))
    return nodes


def _resolve_launch_path(
    referenced: str, current_dir: Path, src_root: Path
) -> Path | None:
    """把 launch 里引用的路径解析成文件。

    支持：
    - 绝对路径 / 直接相对路径（相对当前 launch 所在目录）
    - ``os.path.join(share, "launch", "x.launch.py")`` 这类基于
      robogame_bringup share 目录的拼接（launch 源码里 share 来自
      ``get_package_share_directory("robogame_bringup")``，安装后等于
      ``<src_root>/robogame_bringup``）。
    """
    candidate = Path(referenced)
    if candidate.is_absolute():
        return candidate
    for base in (current_dir, src_root / "robogame_bringup"):
        joined = base / referenced
        if joined.is_file():
            return joined
        # os.path.join(share, "launch", "camera.launch.py") 在 AST 里是一个
        # Call 表达式而非常量；若拿到的是字面 "launch/camera.launch.py"
        # 这类相对 share 的路径，直接试 share 根。
        share_relative = (src_root / "robogame_bringup") / referenced
        if share_relative.is_file():
            return share_relative
    return None


def parse_launch_nodes(launch_path: Path) -> list[tuple[str, str]]:
    """解析一个 launch 文件（含 include 展开），返回 [(package, executable)]。

    需要 src_root 时通过 ``_launch_nodes_recursive`` 完成；为保持旧接口
    签名不变，这里以 launch 文件所在目录推导 src_root。
    """
    src_root = launch_path.parents[3]  # .../robogame_bringup/launch/x.py
    return _launch_nodes_recursive(launch_path, src_root, set())


def entrypoint_modules(src_root: Path) -> dict[tuple[str, str], str]:
    """扫描所有包的 setup.py，构建 {(package, executable): "pkg.module"} 映射。

    只解析 console_scripts 条目，格式 ``executable = pkg.module:main``。
    """
    mapping: dict[tuple[str, str], str] = {}
    if not src_root.is_dir():
        return mapping
    for setup_py in sorted(src_root.glob("*/setup.py")):
        package = setup_py.parent.name
        try:
            tree = ast.parse(setup_py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            entry_points = keywords.get("entry_points")
            if not isinstance(entry_points, ast.Dict):
                continue
            console = None
            for key, value in zip(entry_points.keys, entry_points.values):
                if _constant_string(key) == "console_scripts" and isinstance(
                    value, ast.List
                ):
                    console = value.elts
                    break
            if console is None:
                continue
            for item in console:
                text = _constant_string(item)
                if not text or "=" not in text:
                    continue
                executable, _, module_ref = text.partition("=")
                module_ref = module_ref.strip()
                if not module_ref.endswith(":main"):
                    continue
                mapping[(package, executable.strip())] = module_ref[:-5]
    return mapping


def node_topics(module_path: Path) -> tuple[set[str], set[str]]:
    """解析一个节点源码，返回 (订阅话题集合, 发布话题集合)。

    只识别 ``self.create_subscription(Type, "/topic", ...)`` 与
    ``self.create_publisher(Type, "/topic", ...)`` 中第二个位置参数为
    字符串常量的调用。TF broadcaster 与服务客户端不属于话题。
    """
    subscribed: set[str] = set()
    published: set[str] = set()
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)):
            continue
        method = node.func.attr
        if method not in {"create_subscription", "create_publisher"}:
            continue
        args = node.args
        if len(args) < 2:
            continue
        topic = _constant_string(args[1])
        if topic is None:
            continue
        if method == "create_subscription":
            subscribed.add(topic)
        else:
            published.add(topic)
    return subscribed, published


def analyze_launch(
    launch_path: Path, src_root: Path
) -> tuple[list[LaunchGraphIssue], dict[str, set[str]]]:
    """分析一个 launch 文件的图完整性。

    返回 (缺失发布者的问题列表, 图内总发布话题集合)。
    """
    entrypoints = entrypoint_modules(src_root)
    published_globally: set[str] = set()
    issues: list[LaunchGraphIssue] = []
    for package, executable in _launch_nodes_recursive(
        launch_path, src_root, set()
    ):
        external = _external_node_topics(package, executable)
        if external is not None:
            subscribed, published = external
        else:
            module_ref = entrypoints.get((package, executable))
            if module_ref is None:
                # 无法映射 executable -> 源码：跳过（可能是不在 console_scripts 的脚本）。
                continue
            module_path = src_root / package / (module_ref.replace(".", "/") + ".py")
            if not module_path.is_file():
                continue
            subscribed, published = node_topics(module_path)
        published_globally |= published
        for topic in sorted(subscribed):
            # 本节点自己发布又订阅的话题不算缺口。
            if topic in published:
                continue
            if topic not in published_globally:
                issues.append(LaunchGraphIssue(
                    launch=launch_path.name,
                    topic=topic,
                    subscriber=executable,
                ))
    return issues, published_globally


def _node_remappings(launch_path: Path) -> dict[str, str]:
    """解析 launch 文件顶层 Node(...) 的 remappings，返回 {原话题: 新话题}。

    仅处理 ``remappings=[("/a", "/b"), ...]`` 常量元组；include 的子 launch
    由调用方在解析其自身时合并。
    """
    tree = ast.parse(launch_path.read_text(encoding="utf-8"))
    remap: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "Node"):
            continue
        for keyword in node.keywords:
            if keyword.arg != "remappings" or not isinstance(keyword.value, ast.List):
                continue
            for item in keyword.value.elts:
                if not isinstance(item, ast.Tuple) or len(item.elts) != 2:
                    continue
                src = _constant_string(item.elts[0])
                dst = _constant_string(item.elts[1])
                if src is not None and dst is not None:
                    remap[src] = dst
    return remap


def missing_publishers(
    launch_path: Path, src_root: Path
) -> dict[str, list[str]]:
    """返回 {topic: [订阅它的 executable]}，仅含图中无发布者的话题。

    注意：一次性遍历结束后再统一判定，避免节点顺序影响（后面的发布者
    应能补上前面的订阅）。外部包节点（如 usb_cam）经 EXTERNAL_NODE_TOPICS
    声明话题；include 的子 launch 会被递归展开，remappings 会被应用。
    """
    entrypoints = entrypoint_modules(src_root)
    subscriptions: dict[str, list[str]] = {}
    published_globally: set[str] = set()

    def _apply_remap(topic: str, remap: dict[str, str]) -> str:
        return remap.get(topic, topic)

    def _scan(scan_path: Path) -> None:
        nonlocal published_globally
        for package, executable, source_file in _launch_nodes_with_source(
            scan_path, src_root, set()
        ):
            remap = _node_remappings(source_file)
            external = _external_node_topics(package, executable)
            if external is not None:
                subscribed, published = external
            else:
                module_ref = entrypoints.get((package, executable))
                if module_ref is None:
                    continue
                module_path = src_root / package / (
                    module_ref.replace(".", "/") + ".py"
                )
                if not module_path.is_file():
                    continue
                subscribed, published = node_topics(module_path)
            published_globally |= {_apply_remap(t, remap) for t in published}
            for topic in subscribed:
                mapped = _apply_remap(topic, remap)
                subscriptions.setdefault(mapped, []).append(executable)

    _scan(launch_path)
    return {
        topic: subscribers
        for topic, subscribers in sorted(subscriptions.items())
        if topic not in published_globally
    }


def analyze_all_launches(
    src_root: Path,
) -> dict[str, dict[str, list[str]]]:
    """分析 robogame_bringup/launch 下全部 launch 文件。

    返回 {launch文件名: missing_publishers(...)}。
    """
    launch_dir = src_root / "robogame_bringup" / "launch"
    result: dict[str, dict[str, list[str]]] = {}
    if not launch_dir.is_dir():
        return result
    for launch_path in sorted(launch_dir.glob("*.launch.py")):
        result[launch_path.name] = missing_publishers(launch_path, src_root)
    return result
