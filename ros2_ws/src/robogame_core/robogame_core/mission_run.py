"""任务运行循环（纯逻辑）：把「当前段 → 该发什么命令」的全部决策收在一处。

## 为什么从 ROS 节点里抽出来

`mission_manager` 原来是「ROS 订阅/发布 + 一大坨运行逻辑」混在一起。后果有两个：

1. 那坨逻辑（段切换、重试重发、作业步骤、授权、目标、限速/坡道/转弯命令）**只能靠
   AST 结构断言**验证——本机没有 rclpy，跑不起来；
2. 想离线做**端到端预演**（用虚拟场地把整条路线跑一遍）时，没有可调用的入口，
   只能在测试里复制一份逻辑——而复制品会与真身分叉。

抽出来以后：节点只剩「订阅 → 调这里 → 发布命令」，而循环本身可以离线跑完整条路线。

## 边界

- 这里**不做运动控制**：只产生「给谁授权、发什么目标、发什么机构命令」；
- 这里**不做安全门控**：急停/通信丢失仍由各节点自己的安全门控优先处理；
- 观测（巡线状态、位姿）由调用方喂进来，命令由调用方发出去。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum

from .authorization import SOURCE_NONE
from .mission import (
    MissionMachine,
    MissionState,
    classify_action_result,
    line_calibration_blocker,
)
from .mission_dispatch import (
    LineStatus,
    SOURCE_ALIGN,
    SOURCE_NAVIGATE,
    color_from_work_command,
    decide,
    line_command_for,
    turn_command_for,
)
from .mission_route import CargoPlan, RoutePlan, SegmentRole
from .models import CubeColor, MissionResult, Pose2D
from .ramp_control import SlipDecision
from .work_sequence import WorkPlan, WorkStep, WorkStepKind, shifted_pose, work_steps

#: 作业步骤由谁驱动：抓/放归机构（视觉对准自己动底盘），侧移归位姿控制
STEP_ACTIVE_SOURCE = {
    WorkStepKind.PICK: SOURCE_ALIGN,
    WorkStepKind.PLACE: SOURCE_ALIGN,
    WorkStepKind.SHIFT: SOURCE_NAVIGATE,
}


def calibration_readiness(
    status: "LineStatus | None",
    status_time: float,
    now: float | None,
    timeout_s: float,
) -> bool | None:
    """从「最近一次巡线状态 + 它的时间戳」推出标定可用性（三态）。

    为什么抽成模块级函数：路线模式（`MissionRun`）和演示模式（节点里的旧流程）
    都要在开赛那一拍回答同一个问题。两处各写一遍必然漂移，而漂移的表现就是
    「同一个 `require_line_calibration: true`，换条流程语义就变了」。

    - 没收到过状态 → `None`（未知）；时间戳缺失（`status_time <= 0`）同样算未知；
    - 收到但已断流（超过 `timeout_s`）→ `None`；
    - 否则照抄状态里的三态值。
    """
    if status is None or status_time <= 0.0:
        return None
    if now is not None and now - status_time > timeout_s:
        return None
    return status.calibration_ready


class CommandKind(str, Enum):
    """要发布的命令类型（节点按类型选发布者）。"""

    AUTHORITY = "authority"  # /mission/active_source（文本）
    GOAL = "goal"  # /motion/goal（位姿）
    MANIPULATOR = "manipulator"  # /manipulator/command（文本）
    LINE = "line"  # /mission/line（JSON 或空串）
    TURN = "turn"  # /mission/turn（JSON 或空串）
    STOP = "stop"  # /cmd_vel 零速（演示模式的终态用）


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    text: str = ""
    pose: Pose2D | None = None

    def __post_init__(self) -> None:
        if self.kind is CommandKind.GOAL and self.pose is None:
            raise ValueError("goal command requires a pose")
        if self.kind is not CommandKind.GOAL and self.pose is not None:
            raise ValueError("only goal commands carry a pose")


@dataclass
class MissionRun:
    """路线模式的运行循环：喂观测 / 收结果 → 产出命令。"""

    machine: MissionMachine
    plan: RoutePlan
    cargo_plan: CargoPlan
    #: 巡线状态断流多久算「过期」（过期时不许推进段）
    line_status_timeout_s: float = 0.5
    #: 坡道打滑卡住连续上报多少次 → 按失败重试本段
    ramp_stuck_retry_reports: int = 3

    # -- 运行期簿记（原在节点里） -----------------------------------------
    published_segment_id: str = ""
    published_retries: int = 0
    published_step_position: int = -1
    published_work_index: int = -1
    published_goal: Pose2D | None = None
    work_plan: WorkPlan | None = None
    work_reference_pose: Pose2D | None = None
    segment_distance_m: float = 0.0
    previous_pose: Pose2D | None = None
    last_line_status: LineStatus | None = None
    last_line_status_time: float = 0.0
    ramp_stuck_reports: int = 0
    #: 本拍产生的说明文字（节点负责记日志；测试用来断言行为）
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 观测输入
    # ------------------------------------------------------------------

    def observe_line_status(self, status: LineStatus, now: float) -> None:
        """喂 `/line_follow/status` 的解析结果。"""
        self.last_line_status = status
        self.last_line_status_time = now
        runner = self.machine.route_runner
        if runner is not None:
            runner.observe_line(status.state)
            runner.mark_stale(status.stale)
        # 坡道打滑卡住 → 按失败重试本段（节点已经停车，这里决定重试还是判死）
        if (
            self.machine.state is MissionState.ROUTE_RUNNING
            and status.ramp_decision == SlipDecision.STUCK.value
        ):
            self.ramp_stuck_reports += 1
            if self.ramp_stuck_reports >= self.ramp_stuck_retry_reports:
                self.ramp_stuck_reports = 0
                self.notes.append("坡道打滑卡住：按失败重试本段")
                self.machine.tick(
                    now=now,
                    action_failed=True,
                    failure_result=MissionResult.MECHANISM_ERROR,
                    failure_detail="ramp stuck (slip timeout)",
                )
        else:
            self.ramp_stuck_reports = 0

    def line_calibration_ready(self, now: float | None = None) -> bool | None:
        """巡线标定可用性（三态），供开赛门使用。

        断流时返回 `None` 而不是上一次的旧值：**旧的「可用」不能证明现在可用**
        （节点可能重启成未标定状态），而 `None` 会被开赛门当成拒绝，
        这正是我们要的保守方向。
        """
        return calibration_readiness(
            self.last_line_status,
            self.last_line_status_time,
            now,
            self.line_status_timeout_s,
        )

    def observe_pose(self, pose: Pose2D, now: float) -> None:
        """喂 `/pose`：累计段内位移并交给退出判据。"""
        if self.previous_pose is not None and self.machine.state is MissionState.ROUTE_RUNNING:
            self.segment_distance_m += (
                (pose.x - self.previous_pose.x) ** 2 + (pose.y - self.previous_pose.y) ** 2
            ) ** 0.5
        self.previous_pose = pose
        runner = self.machine.route_runner
        if runner is not None:
            runner.observe_pose(pose)
            runner.observe_distance(self.segment_distance_m)

    def handle_result(self, source: str, text: str, now: float | None = None) -> None:
        """处理 `/motion/result` 或 `/manipulator/result`。

        `source` 必须区分：作业段里两者都会出现（抓取 + 车体微移），
        不区分就会把一次侧移成功记成「抓了一块」。

        **必须传 `now`**：整条运行循环用一个时钟源。省略时会退化成真实时钟，
        与循环的虚拟时间不一致（曾经因此让比赛总时钟瞬间「到点」——见工作留痕）。
        """
        if now is None:
            now = time.monotonic()
        succeeded, failure_result, detail = classify_action_result(text)
        plan = self.work_plan
        step = None if plan is None else plan.current
        if succeeded:
            if (
                plan is not None
                and step is not None
                and self.machine.state is MissionState.ROUTE_RUNNING
            ):
                if not plan.advance(result_source=source):
                    self.notes.append(
                        f"忽略 {source} 结果：当前步骤是 {step.kind.value}（等待 {step.result_source}）"
                    )
                    return
                if step.kind is WorkStepKind.SHIFT:
                    # 微移成功：只推进步骤，不计入作业次数（次数由机构结果决定）
                    self.machine.tick(now=now)
                    return
                self._update_cargo(step)
            self.machine.tick(now=now, action_succeeded=True)
        else:
            self.published_work_index = -1
            self.machine.tick(
                now=now, action_failed=True, failure_result=failure_result,
                failure_detail=detail,
            )

    def _update_cargo(self, step: WorkStep) -> None:
        """抓/放成功后更新「车上载货」，并保证**永不让簿记打断任务**。

        为什么必须有这一步（R15 故障矩阵发现的真缺陷）：路线模式下原先只在
        `machine.cargo` 的旧演示流程里记账，路线模式**从不更新**它。后果有两个，
        都是真车上的：
        1. `/mission/cargo` 一整趟都显示 0 块 → 网页面板显示的载货是假的；
        2. 降级阶梯用 `cargo.total >= 1` 判断「框里已有块 → 跳过剩余取块去搭建」，
           而它永远读到 0 → **`SKIP_TO_BUILD` 这条分支在真车上根本走不到**：
           抓到 1~2 块后取块失败会直接撤退，已经抓到的块白白丢掉、搭建也不做了。

        对不上的时候（例如机构报告成功但手里没有块）**不抛异常**：
        运动与机构的安全不能被簿记问题拖垮。做法是把 `cargo.valid` 置假并写一条 note
        （节点会打日志、网页能看到），让不一致**看得见**而不是被吞掉。
        """
        color = color_from_work_command(step.command)
        if color is None:  # pragma: no cover - 命令只有 4 种，出了就是契约被改坏
            self.notes.append(f"载货簿记跳过：无法识别机构命令 {step.command!r}")
            self.machine.cargo.valid = False
            return
        cargo = self.machine.cargo
        if step.kind is WorkStepKind.PICK:
            if cargo.can_add(color):
                cargo.add(color)
            else:
                self.machine.cargo.valid = False
                self.notes.append(
                    f"载货簿记异常：又抓到一块 {color.value}，但账面已有 {cargo.total} 块"
                    "（不再计入，载货状态标记为不可信）"
                )
        elif step.kind is WorkStepKind.PLACE:
            onboard = cargo.orange if color is CubeColor.ORANGE else cargo.purple
            if onboard > 0:
                cargo.remove(color)
            else:
                self.machine.cargo.valid = False
                self.notes.append(
                    f"载货簿记异常：放下了一块 {color.value}，但账面上没有"
                    "（载货状态标记为不可信）"
                )

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def tick(
        self,
        now: float,
        *,
        communication_ok: bool = True,
        emergency_stop: bool = False,
        physical_start: bool = True,
        mechanism_fault: bool = False,
    ) -> list[Command]:
        """推进一拍，返回本拍要发布的命令（顺序即建议的发布顺序）。

        安全输入（通信/急停/开赛信号/机构故障）由调用方从 `/robot/status` 取，
        与真实的节点行为一致——这里不替它们做判断。
        """
        self.notes = []
        commands: list[Command] = []

        if (
            self.machine.state is MissionState.ROUTE_RUNNING
            and now - self.last_line_status_time > self.line_status_timeout_s
        ):
            runner = self.machine.route_runner
            if runner is not None:
                runner.mark_stale(True)  # 巡线状态断流：不许推进任何段

        state = self.machine.state
        if state in (MissionState.WAIT_FOR_COMMUNICATION, MissionState.SELF_CHECK):
            self.machine.tick(
                now=now, communication_ok=communication_ok, emergency_stop=emergency_stop,
                action_succeeded=not mechanism_fault,
                # 自检期机构故障：**立刻**按机构故障处理，而不是干等到状态超时。
                # 为什么（R15 故障矩阵发现的真问题）：原来只把 mechanism_fault 当成
                # 「成功条件不满足」，于是自检既不通过也不失败，一路等到
                # state_timeout_s（20 s）× 重试次数 = 60 s 才报 TIMEOUT——
                # 现场看到的原因是「状态超时」，而真实原因是机构没就绪；
                # 无人干预模式下这段等待毫无意义（没人会去复位机构）。
                action_failed=mechanism_fault,
                failure_result=MissionResult.MECHANISM_ERROR,
                failure_detail="机制故障：自检未通过（/robot/status mechanism_fault=true）",
            )
        elif state is MissionState.WAIT_FOR_PHYSICAL_START:
            self.machine.tick(
                now=now, communication_ok=communication_ok, emergency_stop=emergency_stop,
                physical_start=physical_start,
                line_calibration_ready=self.line_calibration_ready(now),
            )
        elif state is MissionState.VERIFY_BUILD:
            stable_for = now - self.machine.entered_at
            self.machine.tick(
                now=now, communication_ok=communication_ok, emergency_stop=emergency_stop,
                action_succeeded=stable_for >= self.machine.config.build_stability_s,
            )
        else:
            self.machine.tick(
                now=now, communication_ok=communication_ok, emergency_stop=emergency_stop
            )

        if self.machine.state is MissionState.ROUTE_RUNNING:
            commands.extend(self._dispatch_route())
        elif self.machine.state in (
            MissionState.COMPLETE, MissionState.FAILED, MissionState.SAFE_STOP
        ):
            commands.extend(self._release_commands())
        return commands

    def _release_commands(self) -> list[Command]:
        """终态：释放授权 + 清除待执行命令（防止残留指令在下次授权时突然执行）。"""
        return [
            Command(CommandKind.AUTHORITY, text=SOURCE_NONE),
            Command(CommandKind.TURN, text=""),
            Command(CommandKind.LINE, text=""),
        ]

    def _dispatch_route(self) -> list[Command]:
        runner = self.machine.route_runner
        segment = self.machine.current_segment
        if runner is None or segment is None:
            return []
        commands: list[Command] = []
        if segment.id != self.published_segment_id:
            self.published_segment_id = segment.id
            self.published_work_index = -1
            self.published_retries = 0
            self.published_goal = None
            self.segment_distance_m = 0.0
            self.previous_pose = None
            commands.extend(self._prepare_work_plan(segment))
            commands.append(self._turn_command(segment))
            commands.append(self._line_command(segment))
            self.notes.append(f"进入段 {segment.id}：{segment.label}")
        elif self.machine.retries != self.published_retries:
            # 本段重试：必须重发本段命令——巡线节点会把「打滑卡住」保持零速，
            # 只重置计时是叫不醒它的；重发命令会让它重建转弯器/坡道控制器重新开始。
            self.published_retries = self.machine.retries
            self.notes.append(f"{segment.id} 第 {self.machine.retries} 次重试：重发本段命令")
            commands.extend(self._prepare_work_plan(segment))
            commands.append(self._turn_command(segment))
            commands.append(self._line_command(segment))

        decision = decide(segment, self.cargo_plan, max(runner.observations.work_count, 0))
        step = None if self.work_plan is None else self.work_plan.current
        if step is not None:
            # 作业段：按动作序列逐步下发（抓/放归机构，侧移归位姿控制）
            commands.extend(self._work_step_commands(segment, step))
            commands.append(Command(CommandKind.AUTHORITY, text=STEP_ACTIVE_SOURCE[step.kind]))
            return commands

        # 顺序：先给目标（若有），再授权——未授权的运动节点只会发零速，两种顺序都安全
        # 目标只在**变化时**才发：每拍重发同一个目标会不断刷新位姿控制器的目标计时器，
        # 让它的「目标超时」永远不触发（离线预演里看到过 500+ 次重复目标）。
        if decision.goal is not None and decision.goal != self.published_goal:
            commands.append(Command(CommandKind.GOAL, pose=decision.goal))
            self.published_goal = decision.goal
        commands.append(Command(CommandKind.AUTHORITY, text=decision.active_source))
        if segment.role is SegmentRole.WORK and decision.work_command is not None:
            index = runner.observations.work_count
            if index != self.published_work_index:
                commands.append(Command(CommandKind.MANIPULATOR, text=decision.work_command))
                self.published_work_index = index
                self.notes.append(f"{segment.id}：第 {index + 1} 次作业 → {decision.work_command}")
        return commands

    def _prepare_work_plan(self, segment) -> list[Command]:
        """进入/重试作业段时重建动作序列（抓放 + 之间的车体微移）。"""
        self.published_step_position = -1
        self.work_reference_pose = None
        if segment.role is not SegmentRole.WORK:
            self.work_plan = None
            return []
        try:
            steps = work_steps(segment, self.cargo_plan)
        except ValueError as exc:
            self.work_plan = None
            self.notes.append(f"{segment.id}：作业序列无法展开（{exc}），本段将不做动作")
            return []
        self.work_plan = WorkPlan(steps=steps)
        self.work_reference_pose = segment.to_pose
        self.notes.append(
            f"{segment.id}：作业序列共 {len(steps)} 步 — "
            + "；".join(step.label for step in steps)
        )
        return []

    def _work_step_commands(self, segment, step) -> list[Command]:
        """当前作业步骤的命令：抓/放 → 机构命令；侧移 → 位姿目标（累积位移）。

        去重用**步骤位置**（`WorkPlan.index`）而不是 `WorkStep.index`：后者对
        「侧移 + 紧随其后的抓取」是同一个号，用它去重会让第二次抓取**永远发不出去**
        （离线端到端预演抓到的真 bug）。
        """
        position = self.work_plan.index if self.work_plan is not None else -1
        if position == self.published_step_position:
            return []
        if step.kind is WorkStepKind.SHIFT:
            base = self.work_reference_pose or segment.to_pose
            target = shifted_pose(base, forward_m=step.forward_m, lateral_m=step.lateral_m)
            self.work_reference_pose = target
            self.published_step_position = position
            self.notes.append(
                f"{segment.id}：车体微移 → ({target.x:.3f}, {target.y:.3f})"
                f"（前向 {step.forward_m:+.3f} m / 侧向 {step.lateral_m:+.3f} m）"
            )
            return [Command(CommandKind.GOAL, pose=target)]
        self.published_step_position = position
        self.notes.append(f"{segment.id}：{step.label} → {step.command}")
        return [Command(CommandKind.MANIPULATOR, text=step.command or "")]

    def _turn_command(self, segment) -> Command:
        command = turn_command_for(segment)
        return Command(
            CommandKind.TURN,
            text="" if command is None else json.dumps(command, ensure_ascii=False),
        )

    def _line_command(self, segment) -> Command:
        command = line_command_for(segment)
        return Command(
            CommandKind.LINE, text="" if command is None else command.to_json()
        )

    # ------------------------------------------------------------------
    # 对外快照（网页）
    # ------------------------------------------------------------------

    def payload(self, now: float, *, route_error: str = "") -> dict:
        """`/mission/route` 的载荷（网页显示用）。"""
        progress = self.machine.route_progress(now)
        segment = self.machine.current_segment
        runner = self.machine.route_runner
        next_segment = None
        if runner is not None and not runner.is_complete:
            index = runner.segment_index + 1
            if 0 <= index < len(self.plan.segments):
                next_segment = self.plan.segments[index].id
        active_source = None
        if segment is not None:
            active_source = decide(segment, self.cargo_plan, 0).active_source
        return {
            **progress,
            "route_version": self.plan.version,
            "rounds": self.plan.rounds,
            "next_segment_id": next_segment,
            "work_count": 0 if runner is None else runner.observations.work_count,
            "work_required": None if segment is None else segment.exit.required_count,
            "active_source": active_source,
            "route_error": route_error,
            "line_limit_mps": None
            if self.last_line_status is None
            else self.last_line_status.line_limit_mps,
            "ramp_decision": ""
            if self.last_line_status is None
            else self.last_line_status.ramp_decision,
            "turn_phase": ""
            if self.last_line_status is None
            else self.last_line_status.turn_phase,
            "work_step": "" if self.work_plan is None else self.work_plan.progress_text(),
            "work_step_kind": ""
            if self.work_plan is None or self.work_plan.current is None
            else self.work_plan.current.kind.value,
            "work_step_label": ""
            if self.work_plan is None or self.work_plan.current is None
            else self.work_plan.current.label,
            # B4 开赛门：网页上必须能一眼看出「现在能不能开赛」，
            # 而不是等按下开始后任务直接 FAILED 才知道。
            "line_calibration_ready": self.line_calibration_ready(now),
            "readiness_blockers": self.readiness_blockers(now),
            # 载货：路线模式下这个数以前从来没被更新过（R15 修），
            # 而且 `/mission/cargo` 当时没有任何订阅者——所以这里直接放进网页读的载荷里，
            # 「车上有几块」是现场最需要一眼看到的数之一。
            "cargo_onboard": self.machine.cargo.total,
            "cargo_valid": self.machine.cargo.valid,
        }

    def readiness_blockers(self, now: float) -> list[str]:
        """当前**已知**的开赛阻塞项（人类可读原因列表）。

        只报「任务层确实知道」的项：标定不可用/未知。故意不在这里猜车、机构、
        电池——那些没有传感器上报的项猜出来只会变成假信息。
        """
        blockers: list[str] = []
        if self.machine.config.require_line_calibration:
            blocker = line_calibration_blocker(
                self.machine.config, self.line_calibration_ready(now)
            )
            if blocker is not None:
                blockers.append(blocker)
        return blockers
