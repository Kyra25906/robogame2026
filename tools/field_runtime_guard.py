"""网页「真伪与实例」核查：现在跑的到底是真车还是 mock？有没有第二个实例？

## 为什么需要它

现场只有一条纪律能防住事故：**同一个话题同时只能有一个发布者**。但网页/控制台
只知道**自己启动过的**进程——`field_console.FieldConsole` 只记自己 spawn 的 PID，
对「有人先在 SSH 里起了 `hardware.launch.py`」完全无感。于是网页上的「启动底盘链路」
会心安理得地起第二个 `robot_bridge`。后果不是一句报错：

- `runtime_source_guard` 采样 3 s 后发现关键话题有两个发布者 → **关掉整个 launch**；
- 或者更糟：两个 bridge 同时往同一个串口写、两个节点同时抢 `/cmd_vel`。

同一件事的另一半是「真假」：`robot_bridge` 的 `mock_mode` **默认是 True**
（`robot_bridge/node.py:124`），只带共用层启动就是假数据。此时安全总览会显示
「通信正常」，而车上根本没接固件——这是「看起来一切正常」型的误判。

## 判据从哪来（单一来源，绝不复制一份）

- **真 / 假**：`/robot/status` 的 `detail` 字段，分类函数用
  `robogame_core.runtime_source.classify_status_detail`——与 launch 里的
  `runtime_source_guard` **是同一个函数**。这里另写一份就会分叉，而分叉的表现
  是「launch 判定为 field、网页判定为 mock」这种最难查的矛盾。
- **关键话题**：`robogame_core.runtime_source.CRITICAL_TOPICS`，同样与 source guard 同源。
- **第二个实例**：系统进程表里匹配到已知节点、但 PID 不属于本控制台。

## 边界

本模块**纯逻辑**：不查 ROS、不读系统、不写文件。调用方把观测喂进来，它只做判定与
措辞。因此它可以在没有 rclpy、没有树莓派的开发机上离线单测。

它**不能**证明的：进程表里没有第二个实例 ≠ 串口没有被别的进程占用（`lsof` 才是那个
判据）；`detail` 说 field ≠ 车上烧的固件就是仓库里这份。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

try:  # 与 runtime_source_guard 同源；robogame_core 未就绪时降级为本地常量
    from robogame_core.runtime_source import CRITICAL_TOPICS, classify_status_detail
    _RUNTIME_SOURCE_READY = True
except ImportError:  # pragma: no cover - 只在未 source 工作空间时走到
    CRITICAL_TOPICS = ("/robot/status", "/wheel_odom", "/imu/data")
    _RUNTIME_SOURCE_READY = False

    def classify_status_detail(detail: str):  # type: ignore[misc]
        """"降级副本：**只在 robogame_core 缺失时**使用，并且只在导入期决定。

        为什么可以容忍这份副本：它只影响「本机没装 robogame_core」这一种情况，
        而那种情况下 launch 也起不来。真正需要警惕的是**两份同时在用**，
        所以 `runtime_source_ready=False` 会作为一条 finding 报到网页上。
        """
        from enum import Enum

        class _Source(str, Enum):
            MOCK = "mock"
            FIELD = "field"
            UNKNOWN = "unknown"

        if detail == "mock hardware":
            return _Source.MOCK
        if detail == "decoded MCU V1 STATUS" or detail.startswith("MCU transport "):
            return _Source.FIELD
        return _Source.UNKNOWN


#: 关键话题之外还要看的话题。为什么也要看：
#: - `/line_sensor` 两个发布者 = 巡线读数半真半假（mock 与 bridge 混着来），
#:   而巡线节点无法分辨，`docs/field/首次上车执行清单.md` A4 明确警告过这一条；
#: - `/cmd_vel` 的重复发布者由仲裁器兜，但网页自己也是一个发布者（发零速），
#:   所以这里的名字要先被排除掉，否则永远「有 2 个」。
OPTIONAL_TOPICS = ("/line_sensor", "/cmd_vel")

#: 这些发布者不算「别人在发」：网页/控制台自身。
IGNORED_PUBLISHER_NODES = ("field_dashboard",)

#: 命令行特征串 → （网页进程名，中文名）。顺序有意义：先匹配到的算数。
#: 早于后一项不是随意的——`line_follow_controller` 与 `line_sensor_mock`、
#: `motion_controller` 与 `manipulator_client` 都是不同的节点，不能互相吞掉。
NODE_SIGNATURES: tuple[tuple[str, str | None, str], ...] = (
    ("hardware.launch.py", None, "整栈 launch（hardware.launch.py）"),
    ("runtime_source_guard", "source_guard", "来源守卫 runtime_source_guard"),
    ("mission_manager", "mission", "任务层 mission_manager"),
    ("robot_bridge", "bridge", "串口桥 robot_bridge"),
    ("localization_node", "localization", "定位 localization"),
    ("line_follow_controller", "line", "巡线控制器 line_follow_controller"),
    ("line_sensor_mock", "line_mock", "巡线模拟数据源 line_sensor_mock"),
    ("motion_controller", "motion", "运动控制 motion_controller"),
    ("manipulator_client", "arm", "机械臂客户端 manipulator_client"),
    ("cube_perception", "perception", "视觉 cube_perception"),
    ("usb_cam", "camera", "相机驱动 usb_cam"),
)

#: 来源标签：给人看的那一句话。
_SOURCE_LABELS = {
    "real": "真车（/robot/status 已解码 MCU V1 STATUS）",
    "mock": "模拟（/robot/status 来自 mock hardware）",
    "unknown": "未知（detail 不是已知来源标记）",
    "mixed": "冲突（同时观察到 mock 与真车两种来源）",
    "none": "未知（还没收到 /robot/status）",
}

#: 启动某个网页进程前必须确认「没有同名外部实例」的映射。
#: 就是这些名字对应上面的第三个字段（中文名）里的进程。
START_BLOCKS_BY_TOPIC = {
    "/robot/status": "bridge",
    "/wheel_odom": "bridge",
    "/imu/data": "bridge",
}


def classify_command(args: str) -> str | None:
    """命令行 → 已知节点特征串；都不匹配返回 None。"""
    text = args or ""
    for needle, _name, _label in NODE_SIGNATURES:
        if needle in text:
            return needle
    return None


def node_label(needle: str | None) -> str:
    for candidate, _name, label in NODE_SIGNATURES:
        if candidate == needle:
            return label
    return "未知进程"


def console_name(needle: str | None) -> str | None:
    for candidate, name, _label in NODE_SIGNATURES:
        if candidate == needle:
            return name
    return None


def parse_process_listing(text: str, *, with_pgid: bool = False) -> list[dict[str, Any]]:
    """解析 `ps -eo pid=,args=`（或 `ps -eo pid=,pgid=,args=`）的输出。

    只保留「能解析出 PID + 命令行」的行；解析不了的行**直接丢掉**而不是猜——
    进程表是多来源拼起来的，猜出来的证据不如没有证据。

    `with_pgid=True` 时多读一列进程组 ID，这一列是**归属判定**的关键：
    控制台启动的是 `sh -c "ros2 run ..."`，真正的节点是它的子进程，两者 PID
    不同、进程组相同。只比 PID 会把「自己刚起的节点」误报成「网页之外的实例」。
    """
    processes: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 2 if with_pgid else 1)
        if with_pgid:
            if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
                continue
            pid, pgid, args = int(parts[0]), int(parts[1]), parts[2].strip()
        else:
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            pid, pgid, args = int(parts[0]), None, parts[1].strip()
        if not args:
            continue
        processes.append({
            "pid": pid,
            "pgid": pgid,
            "args": args,
            "node": classify_command(args),
        })
    return processes


def split_instances(
    processes: Sequence[dict[str, Any]],
    managed_pids: Iterable[int],
    managed_pgids: Iterable[int] = (),
) -> dict[str, list[dict[str, Any]]]:
    """把已知节点分成「本控制台起的」和「别人起的」。

    这就是本模块存在的理由：`managed` 是控制台自己知道的，
    `external` 才是那个盲区。不认识的进程（node=None）两边都不进。

    归属判据是「PID 属于控制台」**或**「进程组属于控制台」——见
    `parse_process_listing` 里关于两跳的解释。进程组判据只在能拿到 pgid 时生效。
    """
    owned_pids = {int(pid) for pid in managed_pids}
    owned_pgids = {int(pgid) for pgid in managed_pgids}
    managed: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []
    for item in processes:
        if item.get("node") is None:
            continue
        pgid = item.get("pgid")
        owned = int(item.get("pid", -1)) in owned_pids or (
            pgid is not None and int(pgid) in owned_pgids
        )
        (managed if owned else external).append(item)
    return {"managed": managed, "external": external}


def source_state(status_details: Sequence[str] | None) -> dict[str, Any]:
    """从最近的 `/robot/status.detail` 样本推出「真 / 假 / 未知 / 冲突」。"""
    samples = [str(item) for item in (status_details or [])]
    observed: list[str] = []
    for detail in samples:
        kind = classify_status_detail(detail).value
        if kind not in observed:
            observed.append(kind)
    if not samples:
        kind = "none"
    elif "mock" in observed and "field" in observed:
        kind = "mixed"
    elif observed == ["field"]:
        kind = "real"
    elif observed == ["mock"]:
        kind = "mock"
    else:
        kind = "unknown"
    # 「串口在但没解码到状态」是一个独立情况：它既不是 mock 也不是真状态，
    # 而且此时 communication_ok 一定是 false。必须与「还没收到」分开说。
    transport_without_status = any(
        detail.startswith("MCU transport ") and "unavailable" in detail for detail in samples
    )
    return {
        "kind": kind,
        "label": _SOURCE_LABELS[kind],
        "observed": observed,
        "sample": samples[-1] if samples else "",
        "sample_count": len(samples),
        "transport_without_status": transport_without_status,
    }


def _finding(
    level: str,
    code: str,
    message: str,
    evidence: str,
    *,
    blocks: Sequence[str] = (),
) -> dict[str, Any]:
    """一条结论。

    `blocks` 是这条结论**挡住了哪些网页进程名**——把「证据」与「拦截动作」写在
    同一个对象上，调用方就不必再用两套映射去对账（那种对账迟早会漂移）。
    非空 `blocks` 只出现在真的「起了就是第二个实例」的结论里。
    """
    return {
        "level": level, "code": code, "message": message,
        "evidence": evidence, "blocks": list(blocks),
    }


def topic_rows(
    topic_publishers: dict[str, Sequence[str]] | None,
    *,
    ignored: Sequence[str] = IGNORED_PUBLISHER_NODES,
) -> list[dict[str, Any]]:
    """每个话题一行：过滤掉本面板自身后，还剩几个发布者。

    取值只认「序列」类型：传进来 Mock / None / 字符串都不算数（网页整页的状态
    不该因为一个观测缺了就挂掉），此时按「无发布者」处理并保留 info 级别。
    """
    publishers = topic_publishers if isinstance(topic_publishers, dict) else {}
    rows: list[dict[str, Any]] = []
    for topic in tuple(CRITICAL_TOPICS) + OPTIONAL_TOPICS:
        raw = publishers.get(topic)
        if not isinstance(raw, (list, tuple, set, frozenset)):
            raw = []
        names = sorted({
            str(name) for name in raw if str(name) not in ignored
        })
        critical = topic in CRITICAL_TOPICS
        if not names:
            level = "info"
        elif len(names) > 1:
            level = "block" if critical else "warn"
        elif critical and names[0] != "robot_bridge":
            level = "block"
        else:
            level = "ok"
        rows.append({
            "topic": topic,
            "publishers": names,
            "count": len(names),
            "critical": critical,
            "level": level,
        })
    return rows


def guard_report(
    *,
    topic_publishers: dict[str, Sequence[str]] | None = None,
    status_details: Sequence[str] | None = None,
    processes: Sequence[dict[str, Any]] | None = None,
    managed_pids: Iterable[int] = (),
    managed_pgids: Iterable[int] = (),
    expected_source: str = "field",
    process_scan_available: bool = True,
) -> dict[str, Any]:
    """产出网页要显示、启动前要检查的完整结论。

    返回值里的 `blocked_starts` 是**网页进程名**列表：这些进程现在不许启动，
    因为起了就是第二个实例。调用方（`field_dashboard.DashboardController`）
    用它来拒绝 `/api/process/<name>/start`。
    """
    findings: list[dict[str, str]] = []
    rows = topic_rows(topic_publishers)
    source = source_state(status_details)
    instances = split_instances(processes or [], managed_pids, managed_pgids)
    external = instances["external"]

    if not _RUNTIME_SOURCE_READY:
        findings.append(_finding(
            "info", "LOCAL_FALLBACK_CLASSIFIER",
            "本机没有 robogame_core：真伪判据用的是本模块的降级副本（launch 判据不可用）",
            "robogame_core.runtime_source 导入失败",
        ))

    # ---- ① 话题发布者：这是与 source guard 同源的硬判据 ----
    for row in rows:
        if row["level"] == "block" and row["critical"]:
            blocked = START_BLOCKS_BY_TOPIC.get(row["topic"])
            if row["count"] > 1:
                findings.append(_finding(
                    "block", "DUP_PUBLISHER",
                    f"{row['topic']} 有 {row['count']} 个发布者：第二个实例正在跑",
                    f"{row['topic']} → {', '.join(row['publishers'])}",
                    blocks=[] if blocked is None else [blocked],
                ))
            elif row["count"] == 1:
                findings.append(_finding(
                    "block", "FOREIGN_PUBLISHER",
                    f"{row['topic']} 的发布者不是 robot_bridge",
                    f"{row['topic']} → {row['publishers'][0]}",
                    blocks=[] if blocked is None else [blocked],
                ))
        elif row["level"] == "warn":
            # 巡线读数被两个来源混着发，巡线节点自己分不清哪一帧是真的。
            findings.append(_finding(
                "warn", "DUP_OPTIONAL_PUBLISHER",
                f"{row['topic']} 有 {row['count']} 个发布者（同一话题多来源会让读数半真半假）",
                f"{row['topic']} → {', '.join(row['publishers'])}",
                blocks=["line"] if row["topic"] == "/line_sensor" else [],
            ))

    # ---- ② /robot/status 的 detail：真车还是 mock ----
    if source["kind"] == "none":
        findings.append(_finding(
            "warn", "NO_STATUS",
            "还没收到 /robot/status：此刻网页上的通信/急停绿灯都没有依据",
            "status_details 为空",
        ))
    elif source["kind"] == "mixed":
        findings.append(_finding(
            "block", "MIXED_SOURCES",
            "同时观察到 mock 与真车两种 /robot/status 来源：有假数据混进来",
            f"observed={','.join(source['observed'])}",
        ))
    elif source["kind"] == "mock":
        level = "info" if expected_source == "mock" else "warn"
        findings.append(_finding(
            level, "MOCK_OBSERVED",
            "当前 /robot/status 来自 mock（假数据）：通信正常等绿灯不可信，车上没接固件",
            f"detail={source['sample']!r}",
        ))
    elif source["kind"] == "unknown":
        findings.append(_finding(
            "warn", "UNKNOWN_DETAIL",
            "detail 不是已知来源标记：无法判断真假（固件版本可能变了）",
            f"detail={source['sample']!r}",
        ))
    if source["transport_without_status"]:
        findings.append(_finding(
            "warn", "TRANSPORT_NO_STATUS",
            "串口在、但没解码到 RobotStatus：communication_ok 必为 false，不是 mock",
            f"detail={source['sample']!r}",
        ))

    # ---- ③ 进程表：控制台之外还有谁在跑 ----
    if not process_scan_available:
        findings.append(_finding(
            "info", "NO_PROCESS_SCAN",
            "本机不提供进程表扫描：只看得到本网页启动的进程，看不到 SSH 里起的那些",
            "ps 不可用（非树莓派环境）",
        ))
    for item in external:
        label = node_label(item.get("node"))
        name = console_name(item.get("node"))
        if item.get("node") == "hardware.launch.py":
            findings.append(_finding(
                "warn", "STACK_RUNNING",
                "检测到整栈 launch 在跑：此时再用网页按钮起单个节点会变成双实例",
                f"pid={item['pid']} args={item['args']}",
            ))
            continue
        findings.append(_finding(
            "warn", "EXTERNAL_INSTANCE",
            f"网页之外还有一个「{label}」在跑（不是本网页启动的）",
            f"pid={item['pid']} args={item['args']}",
            blocks=[] if name is None else [name],
        ))

    blocked_starts: list[str] = []
    for item in findings:
        for name in item.get("blocks") or []:
            if name not in blocked_starts:
                blocked_starts.append(name)

    verdict = "ok"
    if any(item["level"] == "block" for item in findings):
        verdict = "block"
    elif any(item["level"] == "warn" for item in findings):
        verdict = "warn"

    ranked = [item for item in findings if item["level"] == "block"] \
        or [item for item in findings if item["level"] == "warn"]
    if verdict == "ok":
        headline = f"来源：{source['label']}；关键话题各只有一个发布者"
    else:
        marker = "⛔" if verdict == "block" else "⚠️"
        headline = f"{marker} 来源：{source['label']}｜{ranked[0]['message']}"

    return {
        "verdict": verdict,
        "headline": headline,
        "source": source,
        "findings": findings,
        "topics": rows,
        "instances": {
            "managed": instances["managed"],
            "external": external,
            "scan_available": bool(process_scan_available),
        },
        "blocked_starts": blocked_starts,
        "expected_source": expected_source,
    }


def start_block_reason(report: dict[str, Any], name: str) -> str | None:
    """启动 `<name>` 之前该不该拒绝；该拒绝就返回给人看的理由（含原始证据）。

    只拦「起了就是第二个实例」这一种情况——判据是结论自己带的 `blocks` 字段。
    其余情况（mock、未知 detail、别的节点的外部实例）只警告不拦：现场需要能手动
    联调，不能因为一句警告就寸步难行。
    """
    for item in report.get("findings") or []:
        if name in (item.get("blocks") or []):
            return f"{item['message']}（{item['evidence']}）"
    return None
