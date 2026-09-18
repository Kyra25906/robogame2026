"""具名姿态表：把"手动摆出来的姿态"记下来、并能一条命令复现。

## 为什么需要它

这台臂**没有位置反馈**（`arm.h:115-117`），所以"到某个位置"这件事只有两种来源：

1. **人看着摆出来的**（遥控器 / 网页关节微调）——可靠，但**做完就丢了**；
2. **算出来的**——需要逆运动学，而我们没有实测的连杆几何，算不了。

于是唯一可行的自动化路径是：**人摆一次 → 记成具名姿态 → 以后复现**。
本模块就是"记"和"复现计划"这两半的纯逻辑；下发仍走原来的 0x20 `ARM_SET`
（逐关节、绝对角度、整度），**不需要改固件**。

## 三条必须写清楚的边界

1. **记的是"我们发出去的目标角度"，不是测到的角度**。它可信的前提是"每次复现都从
   同一个已知起点出发"——所以实践上应该先回安全姿态（`/mechanism/home`），再回放。
2. **复现是开环的**：固件的 `SUCCEEDED` 只代表"输出脉宽到了目标 ±4µs"
   （`arm.c` 的 `ARM_AUTO_TOLERANCE_US`），不代表舵机真的转到了那个角度。
   被挡、丢步、电池电压不足都不会被发现。
3. **顺序是机械问题**：先动肩还是先动肘，取决于碰撞包络和负载下垂，没有数据就不能猜。
   所以本模块**按记录时的顺序复现**（人示教时走过的顺序就是人看过的那条路径），
   而不是自作主张排序。
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

try:  # 安全姿态角度从权威来源算出来，不另抄一份
    from .arm import ARM_JOINT_ANGLE_RANGE_DEG, ArmJoint, arm_angle_for_pulse_us
    from .arm import ARM_SAFE_REFERENCE_PULSE_US
except ImportError:  # pragma: no cover
    from robogame_core.arm import (  # type: ignore[no-redef]
        ARM_JOINT_ANGLE_RANGE_DEG,
        ARM_SAFE_REFERENCE_PULSE_US,
        ArmJoint,
        arm_angle_for_pulse_us,
    )

#: 可进姿态表的关节（**不含爪子**）：爪子走 GRAB / RELEASE 开关命令（C-2 / C-9）。
POSE_JOINTS: tuple[int, ...] = (0, 1, 2, 3)

#: 与固件 `ARM_JOINT_ANGLE_RANGE_DEG` 一致。超出这个范围的命令固件会拒绝。
POSE_LIMIT_DEG: tuple[int, int] = (0, 270)

POSE_NAME_MAX_LEN = 24

#: 出厂就有的姿态：固件上电授权后的安全姿态（`arm.c:774` 把臂放在这里）。
SAFE_POSE_NAME = "安全姿态"
SAFE_POSE_NOTE = (
    "固件上电授权后的安全姿态（ARM_SAFE_PULSE_US）；角度由固件常量换算，非实测"
)


def safe_pose_angles() -> dict[int, int]:
    """安全姿态的四个整度角度（由固件脉宽常量按同一线性映射换算）。"""
    angles: dict[int, int] = {}
    for joint in POSE_JOINTS:
        member = ArmJoint(joint)
        angle = arm_angle_for_pulse_us(member, ARM_SAFE_REFERENCE_PULSE_US[member])
        angles[joint] = int(round(angle))
    return angles


def validate_pose_name(name: Any) -> str:
    if not isinstance(name, str):
        raise ValueError("姿态名必须是字符串")
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("姿态名不能为空")
    if len(cleaned) > POSE_NAME_MAX_LEN:
        raise ValueError(f"姿态名最长 {POSE_NAME_MAX_LEN} 个字符")
    return cleaned


def validate_pose_angles(angles: Mapping[Any, Any] | None) -> dict[int, int]:
    """校验一组关节角度；不合法就抛 ``ValueError``（消息直接给人看）。

    拒绝而不是夹取：静默把 300° 改成 270°，会让"你以为记下的姿态"和"实际记下的"
    不一致——而这一整条链上唯一可靠的东西就是"记下来的和发出去的一致"。
    """
    if not isinstance(angles, Mapping):
        raise ValueError("姿态必须给出「关节→角度」的映射")
    cleaned: dict[int, int] = {}
    for raw_joint, raw_angle in angles.items():
        try:
            joint = int(raw_joint)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"关节编号必须是整数，收到 {raw_joint!r}") from exc
        if joint not in POSE_JOINTS:
            if joint == 4:
                raise ValueError(
                    "爪子不进姿态表：抓放走「夹取/释放」（C-2 / C-9），"
                    "它的角度语义是开合端点而不是关节角"
                )
            raise ValueError(
                f"关节编号只能是 {', '.join(str(j) for j in POSE_JOINTS)}（0=腰/云盘,1=肩,2=肘,3=腕）"
            )
        if isinstance(raw_angle, bool) or not isinstance(raw_angle, (int, float)):
            raise ValueError(f"关节 {joint} 的角度必须是数字")
        if not math.isfinite(float(raw_angle)) or float(raw_angle) != int(raw_angle):
            raise ValueError(f"关节 {joint} 的角度必须是整度（0x20 的 parameter 没有小数位）")
        angle = int(raw_angle)
        low, high = POSE_LIMIT_DEG
        if not low <= angle <= high:
            raise ValueError(f"关节 {joint} 的角度 {angle} 超出 {low}～{high}°")
        cleaned[joint] = angle
    if not cleaned:
        raise ValueError("姿态至少要有一个关节")
    return cleaned


@dataclass(frozen=True)
class ArmPoseRecord:
    """一条具名姿态。

    `order` 是**记录时的操作顺序**（人先动了哪个关节）。复现按它来，因为顺序是机械
    问题：先动肩还是先动肘取决于碰撞包络与负载下垂，没有数据就不能替操作员排序。
    """

    name: str
    angles: dict[int, int]
    order: tuple[int, ...] = ()
    note: str = ""
    recorded_at: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", validate_pose_name(self.name))
        cleaned = validate_pose_angles(self.angles)
        object.__setattr__(self, "angles", cleaned)
        declared = tuple(int(item) for item in self.order) if self.order else tuple(cleaned)
        unknown = [item for item in declared if item not in cleaned]
        if unknown:
            raise ValueError(f"复现顺序里出现了姿态中没有的关节：{unknown}")
        missing = [item for item in cleaned if item not in declared]
        # 没写进顺序的关节按编号补在后面：不丢关节，也不改变已声明的相对顺序。
        object.__setattr__(self, "order", tuple(declared) + tuple(sorted(missing)))
        if not isinstance(self.note, str):
            raise ValueError("备注必须是字符串")
        if not isinstance(self.recorded_at, (int, float)) or not math.isfinite(
            float(self.recorded_at)
        ):
            raise ValueError("记录时间必须是有限数")

    def replay_steps(self) -> tuple[dict[str, Any], ...]:
        """复现计划：按 `order` 排出"下一步给哪个关节、发多少度"。"""
        return tuple(
            {"joint": joint, "angle_deg": self.angles[joint]} for joint in self.order
        )

    def describe(self) -> str:
        parts = "、".join(
            f"{joint}={self.angles[joint]}°" for joint in self.order
        )
        return f"{self.name}：{parts}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "angles": {str(joint): self.angles[joint] for joint in self.order},
            "order": list(self.order),
            "note": self.note,
            "recorded_at": float(self.recorded_at),
            "description": self.describe(),
        }


@dataclass
class PoseTable:
    """具名姿态集合（可落盘）。"""

    records: dict[str, ArmPoseRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.records, dict):
            raise ValueError("姿态表必须是「名字→姿态」的字典")

    def upsert(self, record: ArmPoseRecord) -> None:
        if not isinstance(record, ArmPoseRecord):
            raise ValueError("只能存 ArmPoseRecord")
        self.records[record.name] = record

    def get(self, name: str) -> ArmPoseRecord | None:
        return self.records.get(validate_pose_name(name))

    def remove(self, name: str) -> bool:
        return self.records.pop(validate_pose_name(name), None) is not None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.records))

    def as_dict(self) -> dict[str, Any]:
        return {name: self.records[name].as_dict() for name in self.names()}

    def __len__(self) -> int:
        return len(self.records)


def builtin_pose_table(now: float | None = None) -> PoseTable:
    """出厂姿态表：只含固件自己的安全姿态（不是实测值，页面会如实标注）。"""
    table = PoseTable()
    table.upsert(ArmPoseRecord(
        name=SAFE_POSE_NAME, angles=safe_pose_angles(),
        order=POSE_JOINTS, note=SAFE_POSE_NOTE,
        recorded_at=0.0 if now is None else float(now),
    ))
    return table


def load_pose_table(path: str | os.PathLike[str], now: float | None = None) -> PoseTable:
    """读姿态表；文件不存在 → 返回出厂表（含安全姿态），**不报错**。

    为什么不报错：第一次部署时这个文件本来就不存在。但读到一个**坏文件**必须报错——
    静默退回出厂表会让人以为"我记的姿态还在"，而它已经丢了。
    """
    target = Path(path).expanduser()
    if not target.exists():
        return builtin_pose_table(now)
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"姿态表格式错误（顶层必须是对象）：{target}")
    table = builtin_pose_table(now)
    for name, payload in raw.items():
        if not isinstance(payload, dict):
            raise ValueError(f"姿态 {name!r} 的格式错误（必须是对象）")
        angles = payload.get("angles", {})
        if not isinstance(angles, dict):
            raise ValueError(f"姿态 {name!r} 的 angles 必须是对象")
        table.upsert(ArmPoseRecord(
            name=name,
            angles={int(key): value for key, value in angles.items()},
            order=tuple(payload.get("order") or ()),
            note=str(payload.get("note", "")),
            recorded_at=float(payload.get("recorded_at", 0.0)),
        ))
    return table


def save_pose_table(path: str | os.PathLike[str], table: PoseTable) -> str:
    """原子落盘：先写临时文件再替换，避免断电/中断留下半个文件。"""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(table.as_dict(), ensure_ascii=False, indent=2)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, target)
    return str(target)


def replay_plan(record: ArmPoseRecord, *, settle_s: float = 0.0) -> list[dict[str, Any]]:
    """回放计划（给人看的、也是给循环执行用的）。

    `settle_s` 是每步之间的停顿：0 表示"上一步的固件回执一到就发下一步"。
    串行是**故意的**——0x20 同一时刻只允许一条机构命令（`robot_bridge` 并发直接
    回错误码 6），所以"同时下发整个姿态"在现有协议下做不到，只能一步一等。
    """
    if settle_s < 0.0 or not math.isfinite(float(settle_s)):
        raise ValueError("settle_s 必须是非负有限数")
    plan: list[dict[str, Any]] = []
    for index, step in enumerate(record.replay_steps()):
        plan.append({
            "index": index,
            "joint": step["joint"],
            "angle_deg": step["angle_deg"],
            "settle_s": float(settle_s),
        })
    return plan


def record_from_command_history(
    *,
    name: str,
    history: Iterable[tuple[int, int]],
    note: str = "",
    now: float | None = None,
) -> ArmPoseRecord:
    """从"最后一次给每个关节下发的角度"造一条姿态记录。

    `history` 是 `(joint, angle)` 序列（调用方按时间顺序给，同一个关节可能出现多次——
    取最后一次，保序）。这正是"我们相信它现在在哪"的来源：**下发过的目标**，
    而不是测量值。
    """
    angles: dict[int, int] = {}
    order: list[int] = []
    for joint, angle in history:
        member = int(joint)
        angles[member] = int(angle)
        if member in order:
            order.remove(member)
        order.append(member)
    return ArmPoseRecord(
        name=name, angles=angles, order=tuple(order), note=note,
        recorded_at=time.time() if now is None else float(now),
    )
