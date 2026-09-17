"""作业段的动作序列（纯逻辑）：W02 连取 3 块、W04 搭 2 层。

## 为什么需要「序列」而不是「重复发同一条命令」

`PICK_ORANGE` 连发 3 次的问题：机构只管抓，**方块的横向位置是车给的**。墙体材料区
有 10 个槽、槽距约 150 mm（规则文本 1500 mm/10 槽），抓完一块必须让车侧移一个槽距
才能抓下一块。所以一个「取 3 块」的段实际是：

    抓 → 侧移 0.15 m → 抓 → 侧移 0.15 m → 抓

搭建「2 层」同理：按摆法（`CargoPlan.layout`，如 `2+1` = 底层 2 块 + 顶层 1 块），
底层两块之间要侧移一个方块宽（约 0.11 m），顶层再回到中间：

    放 → 侧移 +0.11 m → 放 → 侧移 -0.11 m → 放

摆法改成 `1+1`（每层 1 块、共 2 块）时序列自动变成「放 → 放」——**序列由计划推导，
不是写死的**。

## 坐标系约定（必须现场核对）

侧移量用**车体系**表达：`forward_m` 沿车头方向，`lateral_m` **正 = 车的右侧**。

    车头朝 +y（W02/W04 都是）时：右侧 = +x
    世界坐标换算：x += forward·cos(yaw) + lateral·sin(yaw)
                  y += forward·sin(yaw) - lateral·cos(yaw)

墙材区的槽沿 x 方向排列（区域 1.8 m 在 x 方向），车头朝 +y，所以侧移为正（向右）。
⚠️ **符号与槽距都是占位值**：现场必须核对「第 1 槽在左还是右」「实际槽距多少」，
见 `docs/field/现场待测清单_B2B3_待填值.md` C 类。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from .mission_dispatch import layer_counts, work_command_for, work_sequence
from .mission_route import CargoPlan, RouteSegmentPlan, SegmentRole, WorkKind
from .models import Pose2D

#: 墙体材料区槽距（规则 1500 mm / 10 槽）——占位值，现场实测
DEFAULT_SLOT_PITCH_M = 0.15
#: 搭建区同层两块之间的横向间隔（方块 0.10 m + 0.01 m 余量）——占位值，现场实测
DEFAULT_BUILD_PITCH_M = 0.11


class WorkStepKind(str, Enum):
    """作业段里的一步。"""

    PICK = "PICK"  # 抓一块（发机构命令）
    PLACE = "PLACE"  # 放一块（发机构命令）
    SHIFT = "SHIFT"  # 车体微移（发导航目标）


#: 每一步由哪个结果推进：机构结果 or 运动结果
STEP_RESULT_SOURCE: dict[WorkStepKind, str] = {
    WorkStepKind.PICK: "manipulator",
    WorkStepKind.PLACE: "manipulator",
    WorkStepKind.SHIFT: "motion",
}


@dataclass(frozen=True)
class WorkStep:
    """作业段里的一步：做什么 + 怎么做（命令或位移）。"""

    kind: WorkStepKind
    label: str
    index: int  # 第几次作业（PICK/PLACE 从 0 起；SHIFT 与所属作业同号）
    command: str | None = None  # 机构命令（PICK/PLACE）
    forward_m: float = 0.0  # 车体系前向位移（SHIFT）
    lateral_m: float = 0.0  # 车体系侧向位移，正 = 右（SHIFT）

    def __post_init__(self) -> None:
        if not isinstance(self.kind, WorkStepKind):
            raise ValueError(f"kind must be WorkStepKind, got {self.kind!r}")
        if self.index < 0:
            raise ValueError("step index cannot be negative")
        if self.kind is WorkStepKind.SHIFT:
            if self.command is not None:
                raise ValueError("SHIFT 步不发机构命令")
            if math.hypot(self.forward_m, self.lateral_m) <= 0.0:
                raise ValueError("SHIFT 步必须有非零位移")
        else:
            if not self.command:
                raise ValueError(f"{self.kind.value} 步必须带机构命令")
            if self.forward_m or self.lateral_m:
                raise ValueError(f"{self.kind.value} 步不负责车体位移")

    @property
    def result_source(self) -> str:
        """这一步由哪个结果推进（manipulator / motion）。"""
        return STEP_RESULT_SOURCE[self.kind]

    @property
    def distance_m(self) -> float:
        return math.hypot(self.forward_m, self.lateral_m)


def shifted_pose(pose: Pose2D, *, forward_m: float = 0.0, lateral_m: float = 0.0) -> Pose2D:
    """按车体系位移算出新的世界位姿（朝向不变）。正 lateral = 车的右侧。"""
    for name, value in (("forward_m", forward_m), ("lateral_m", lateral_m)):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    yaw = pose.yaw
    return Pose2D(
        x=pose.x + forward_m * math.cos(yaw) + lateral_m * math.sin(yaw),
        y=pose.y + forward_m * math.sin(yaw) - lateral_m * math.cos(yaw),
        yaw=yaw,
    )


def work_steps(
    segment: RouteSegmentPlan,
    cargo_plan: CargoPlan,
    *,
    slot_pitch_m: float = DEFAULT_SLOT_PITCH_M,
    build_pitch_m: float = DEFAULT_BUILD_PITCH_M,
) -> tuple[WorkStep, ...]:
    """把作业段展开成动作序列（取 / 放 + 之间的微移）。

    取块：3 块 → 抓 → 侧移 → 抓 → 侧移 → 抓（槽距 `slot_pitch_m`）。
    搭建：按 `cargo_plan.layout` 的每层块数展开；同层第 2 块起侧移 `build_pitch_m`，
    换层时侧移回中间（下一块放上一层中间上方）。
    """
    if segment.role is not SegmentRole.WORK or segment.work is None:
        return ()
    required = segment.exit.required_count
    colors = work_sequence(cargo_plan)
    if required > len(colors):
        raise ValueError(
            f"{segment.id}: 需要 {required} 次作业，但计划只带了 {len(colors)} 块"
        )
    if slot_pitch_m <= 0.0 or build_pitch_m <= 0.0:
        raise ValueError("pitch values must be positive")

    steps: list[WorkStep] = []
    if segment.work is WorkKind.PICK:
        for index in range(required):
            if index:
                # 槽沿车体右侧排列（占位约定，现场核对）：每抓一块向右挪一个槽距
                steps.append(WorkStep(
                    kind=WorkStepKind.SHIFT, index=index,
                    label=f"侧移 {slot_pitch_m:.2f} m 到第 {index + 1} 槽",
                    lateral_m=slot_pitch_m,
                ))
            steps.append(WorkStep(
                kind=WorkStepKind.PICK, index=index,
                label=f"抓第 {index + 1} 块",
                command=work_command_for(segment, cargo_plan, index),
            ))
        return tuple(steps)

    # PLACE：按摆法展开（同层内左右交替，换层回到中间）
    counts = layer_counts(cargo_plan)
    cube_index = 0
    for layer_index, count in enumerate(counts):
        for within_layer in range(count):
            if within_layer == 1:
                steps.append(WorkStep(
                    kind=WorkStepKind.SHIFT, index=cube_index,
                    label=f"同层侧移 {build_pitch_m:.2f} m 放第 {within_layer + 1} 块",
                    lateral_m=build_pitch_m,
                ))
            steps.append(WorkStep(
                kind=WorkStepKind.PLACE, index=cube_index,
                label=f"放第 {cube_index + 1} 块（第 {layer_index + 1} 层）",
                command=work_command_for(segment, cargo_plan, cube_index),
            ))
            cube_index += 1
            if within_layer == count - 1 and count > 1:
                # 换层：退回中间（下一块要放在上一层中间上方）
                steps.append(WorkStep(
                    kind=WorkStepKind.SHIFT, index=cube_index - 1,
                    label="退回到中间（准备叠上一层）",
                    lateral_m=-build_pitch_m,
                ))
    return tuple(steps)


@dataclass
class WorkPlan:
    """作业段的步骤执行器（纯状态机，由 mission_manager 驱动）。"""

    steps: tuple[WorkStep, ...] = field(default_factory=tuple)
    index: int = 0

    @property
    def current(self) -> WorkStep | None:
        if 0 <= self.index < len(self.steps):
            return self.steps[self.index]
        return None

    @property
    def is_complete(self) -> bool:
        return self.index >= len(self.steps)

    @property
    def total(self) -> int:
        return len(self.steps)

    def advance(self, *, result_source: str) -> bool:
        """按结果来源推进；来源不匹配当前步骤则忽略（返回 False）。

        这条「来源必须匹配」的检查是为了防一类真实错误：抓取结果和车体微移结果
        都走同一个回调入口，若不区分，一次侧移成功会被记成抓了一块。
        """
        step = self.current
        if step is None:
            return False
        if result_source != step.result_source:
            return False
        self.index += 1
        return True

    def reset(self) -> None:
        self.index = 0

    def progress_text(self) -> str:
        step = self.current
        if step is None:
            return f"已完成 {len(self.steps)} 步"
        return f"第 {self.index + 1}/{len(self.steps)} 步：{step.label}"
