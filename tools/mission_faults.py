"""故障注入矩阵（离线）：**不管出什么故障，车都必须收敛到一个安全的终态**。

## 这份工具回答什么问题

B4 里有三件事：整栈自主启动、无人干预流程、**故障降级**。前两件是接线与流程，
第三件只有靠"真的把故障灌进去看会怎样"才能有证据。
`docs/field/FAULT_INJECTION_TEST_CARD.md` 是**真车**故障注入卡（人拔线、压住轮子…），
而本工具是它的**离线对应物**：在虚拟场地上按脚本注入同样的故障，检查决策链是否收敛。

**它证明什么**：纯逻辑层面，故障发生后状态机不会卡死、会释放底盘授权、
不会留下残留指令、原因会被人看得懂。
**它不证明什么**：真车的传感器/执行器是否真的会这样表现；也不证明故障检测本身
（例如"轮子空转车不走"轮速里程计根本看不到）。真车必须另做
`FAULT_INJECTION_TEST_CARD.md`。

## 与 `mission_sim.py` 的关系

正常流程的端到端预演在 `mission_sim.py`；本文件复用它的虚拟场地（`VirtualPlant`），
只在主循环里加了**故障注入点**，并额外检查一组「安全不变式」。

## 不变式（每个场景都必须满足，与具体故障无关）

| 代号 | 内容 |
|---|---|
| I1 | 终态必须是 COMPLETE / FAILED / SAFE_STOP 之一（不许永远停在运行中） |
| I2 | 进入终态那一拍必须释放底盘授权（`/mission/active_source` = `none`） |
| I3 | 终态那一拍不许留下 GOAL / 机构 / 巡线 / 转弯指令（残留指令会在下次授权时突然执行） |
| I4 | 诚实：没走完就不能报 COMPLETE；`detail` 必须非空（要能看懂为什么停） |
| I5 | 重试次数不超过 `max_retries`（不许无限重试） |

用法：

```bash
python3 tools/mission_faults.py          # 打印故障矩阵
```
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# 开发机上直接跑也能用（与 mission_sim.py 同一套做法）
_CORE_SRC = Path(__file__).resolve().parents[1] / "ros2_ws" / "src" / "robogame_core"
if str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

from robogame_core.authorization import SOURCE_NONE  # noqa: E402
from robogame_core.mission import MissionConfig, MissionMachine, MissionState  # noqa: E402
from robogame_core.mission_dispatch import LineStatus  # noqa: E402
from robogame_core.route_loader import load_route_plan  # noqa: E402
from robogame_core.mission_run import Command, CommandKind, MissionRun  # noqa: E402
from robogame_core.models import MissionResult  # noqa: E402
from robogame_core.ramp_control import SlipDecision  # noqa: E402
from robogame_core.work_sequence import WorkStepKind  # noqa: E402
from mission_sim import SimConfig, VirtualPlant  # noqa: E402

#: 能出现在 `/line_follow/status` 里的故障类型（与真实上送字符串同源）
KIND_COMMS_LOSS = "comms_loss"
KIND_ESTOP = "estop"
KIND_LINE_BLACKOUT = "line_blackout"
KIND_LOCALIZATION_ERROR = "localization_error"
KIND_MANIPULATOR_FAILURE = "manipulator_failure"
KIND_RAMP_STUCK = "ramp_stuck"
KIND_MECHANISM_FAULT_AT_START = "mechanism_fault_at_start"

KINDS = (
    KIND_COMMS_LOSS,
    KIND_ESTOP,
    KIND_LINE_BLACKOUT,
    KIND_LOCALIZATION_ERROR,
    KIND_MANIPULATOR_FAILURE,
    KIND_RAMP_STUCK,
    KIND_MECHANISM_FAULT_AT_START,
)


@dataclass(frozen=True)
class FaultScenario:
    """一个故障场景：**在哪、什么时候、注入什么**，以及期望的安全结果。"""

    name: str
    kind: str
    #: 故障在哪一段激活（前缀匹配段 id）；空串 = 一进入路线就激活
    at_segment: str = ""
    #: 进入该段后多久激活（虚拟秒）；用来让段先正常走一段
    delay_s: float = 0.0
    #: 先放行多少次「成功结果」再开始注入（模拟「第 2 次抓取才坏」）
    after_successes: int = 0
    #: 期望终态 / 结果 / 原因片段（空串 = 不检查这一项，只查不变式）
    expect_state: str = ""
    expect_result: str = ""
    expect_detail_contains: str = ""
    #: 有些故障的**归段是竞态的**（结果到达时任务层可能已经切到下一段），
    #: 这时不假装确定，而是列出可接受集合 + 在报告里打印实际观测。
    expect_state_in: tuple[str, ...] = ()
    expect_detail_any: tuple[str, ...] = ()
    #: 期望走过 / 不该走过的段
    expect_segments: tuple[str, ...] = ()
    expect_not_segments: tuple[str, ...] = ()
    min_degradations: int = 0
    #: 故障是否**锁存**（真实故障不会自己好）。默认锁存；
    #: 「这一段抓取一直失败」这类是段内故障，离开该段就结束，故置 False。
    latched: bool = True
    note: str = ""


def scenarios() -> tuple[FaultScenario, ...]:
    """故障矩阵的定义（每条都对应真车故障卡里的一个动作）。"""
    return (
        FaultScenario(
            name="通信中断（巡线中）",
            kind=KIND_COMMS_LOSS,
            at_segment="S02_LINE_MAIN",
            delay_s=1.0,
            expect_state=MissionState.FAILED.value,
            expect_result=MissionResult.COMMUNICATION_ERROR.value,
            note="真车对应：拔掉 STM32 串口/断开树莓派心跳",
        ),
        FaultScenario(
            name="急停（巡线中）",
            kind=KIND_ESTOP,
            at_segment="S03_RAMP_APPROACH",
            delay_s=1.0,
            expect_state=MissionState.SAFE_STOP.value,
            expect_result=MissionResult.SAFETY_STOP.value,
            note="真车对应：按急停按钮（规则 4.2.4 S3，唯一允许的人工动作）",
        ),
        FaultScenario(
            name="巡线状态断流（不推进任何段）",
            kind=KIND_LINE_BLACKOUT,
            at_segment="S02_LINE_MAIN",
            delay_s=1.0,
            expect_state=MissionState.FAILED.value,
            expect_result=MissionResult.TIMEOUT.value,
            expect_detail_contains="安全停车",
            note="真车对应：探头掉线/树莓派收不到 0x14 帧",
        ),
        FaultScenario(
            name="LOCALIZATION_ERROR（车体微移/搭建期间）",
            kind=KIND_LOCALIZATION_ERROR,
            at_segment="S11_SHIFT_TO_BUILD",
            delay_s=0.5,
            # 归段是竞态的：任务层用**位姿判据**结束 S11，而 `/motion/result` 稍后才到；
            # 结果可能落在 S11（→ 安全停车）或已切到 S12（→ 搭建段降级撤退）。
            # 两种都是安全的，所以这里列出可接受集合，并在报告里打印实际归段。
            expect_state_in=(MissionState.FAILED.value, MissionState.COMPLETE.value),
            expect_detail_any=("LOCALIZATION_ERROR", "位姿不可信", "降级"),
            note="真车对应：里程计跳变/位姿不可信（微移要毫米级，位置不可信就不许继续动）",
        ),
        FaultScenario(
            name="坡道打滑卡住",
            kind=KIND_RAMP_STUCK,
            at_segment="S04_RAMP_UP",
            delay_s=1.0,
            expect_state=MissionState.FAILED.value,
            expect_result=MissionResult.MECHANISM_ERROR.value,
            note="真车对应：坡上打滑上不去（压住轮子/洒水）",
        ),
        FaultScenario(
            name="第一次抓取就失败（一块都没拿到）",
            kind=KIND_MANIPULATOR_FAILURE,
            at_segment="S06_PICK3",
            after_successes=0,
            latched=False,
            expect_state=MissionState.COMPLETE.value,
            expect_segments=("S13_RETREAT",),
            expect_not_segments=("S12_BUILD_2LAYER",),
            min_degradations=1,
            note="真车对应：夹爪抓空。策略：没什么可搭 → 退到撤退位安全收场",
        ),
        FaultScenario(
            name="抓到 1 块后抓取持续失败（有存货）",
            kind=KIND_MANIPULATOR_FAILURE,
            at_segment="S06_PICK3",
            after_successes=1,
            latched=False,
            expect_state=MissionState.COMPLETE.value,
            expect_segments=("S12_BUILD_2LAYER", "S13_RETREAT"),
            min_degradations=1,
            note=(
                "真车对应：第一块成功、第二块开始抓空。策略：跳到搭建段，已有块仍计分；"
                "手里只有 1 块却要放 3 块 → 第 2 次放置必然失败 → 再降级撤退（本矩阵会跑出这条链）"
            ),
        ),
        FaultScenario(
            name="放置持续失败",
            kind=KIND_MANIPULATOR_FAILURE,
            at_segment="S12_BUILD_2LAYER",
            after_successes=0,
            latched=False,
            expect_state=MissionState.COMPLETE.value,
            expect_segments=("S13_RETREAT",),
            min_degradations=1,
            note="真车对应：放不稳/压块。策略：退开，不继续挡在搭建区",
        ),
        FaultScenario(
            name="自检期机构故障（还没进路线）",
            kind=KIND_MECHANISM_FAULT_AT_START,
            expect_state=MissionState.FAILED.value,
            expect_result=MissionResult.MECHANISM_ERROR.value,
            note="真车对应：上电时机构未就绪/未复位",
        ),
    )


@dataclass
class FaultResult:
    """一个场景跑完之后的观测与不变式检查结果。"""

    name: str
    kind: str
    final_state: str
    result: str
    detail: str
    degradations: int
    retries: int
    segments_visited: list[str]
    terminal_authority: str
    terminal_leftovers: list[str]
    stalls: list[str]
    virtual_time_s: float
    invariant_violations: list[str] = field(default_factory=list)
    expectation_mismatches: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.invariant_violations and not self.expectation_mismatches

    def summary(self) -> str:
        head = f"{'✅' if self.ok else '❌'} {self.name}（{self.kind}）"
        body = (
            f"终态 {self.final_state}/{self.result}｜降级 {self.degradations} 次｜"
            f"重试 {self.retries}｜段 {len(self.segments_visited)}｜虚拟 {self.virtual_time_s:.1f}s"
        )
        lines = [head, "   " + body, f"   原因：{self.detail}"]
        for violation in self.invariant_violations:
            lines.append(f"   ⚠️ 不变式：{violation}")
        for mismatch in self.expectation_mismatches:
            lines.append(f"   ⚠️ 期望不符：{mismatch}")
        return "\n".join(lines)


def _fault_window_open(
    scenario: FaultScenario, *, segment_id: str, segment_entered_at: float,
    now: float, successes_seen: int,
) -> bool:
    """故障窗口是否已经打开（还没考虑锁存）。"""
    if scenario.at_segment and not segment_id.startswith(scenario.at_segment):
        return False
    if now - segment_entered_at < scenario.delay_s:
        return False
    return successes_seen >= scenario.after_successes


def run_scenario(
    scenario: FaultScenario,
    *,
    sim_config: SimConfig | None = None,
    mission_config: MissionConfig | None = None,
    layout_path=None,
) -> FaultResult:
    """在虚拟场地上跑一遍「正常流程 + 该故障」，返回观测与不变式检查结果。"""
    if layout_path is None:
        layout_path = (
            Path(__file__).resolve().parents[1]
            / "ros2_ws/src/robogame_bringup/config/field_layout.yaml"
        )
    plan = load_route_plan(layout_path)
    config = sim_config or SimConfig()
    mconfig = mission_config or MissionConfig()
    machine = MissionMachine(mconfig, route=plan)
    run = MissionRun(machine=machine, plan=plan, cargo_plan=plan.cargo_plan)
    plant = VirtualPlant(plan=plan, runner=None, config=config)

    now = 0.0
    visited: list[str] = []
    last_segment = ""
    segment_entered_at = 0.0
    successes_seen = 0
    armed = False
    stalls: list[str] = []
    stall_since = now
    last_progress = ""
    commands: list[Command] = []

    while now < config.max_virtual_s:
        segment_id = machine.segment_id or ""
        window_open = _fault_window_open(
            scenario, segment_id=segment_id, segment_entered_at=segment_entered_at,
            now=now, successes_seen=successes_seen,
        )
        if window_open:
            armed = True
        # 锁存语义：传感器/执行器级故障一旦发生就**不会自己好**（断线不会自愈、
        # 位姿不可信不会自己变可信），所以激活后保持；段内故障（如"这一段抓取一直失败"）
        # 用 latched=False，离开该段即结束。
        active = armed if scenario.latched else window_open
        in_startup = machine.state in (
            MissionState.WAIT_FOR_COMMUNICATION, MissionState.SELF_CHECK
        )
        mechanism_fault = (
            scenario.kind == KIND_MECHANISM_FAULT_AT_START and in_startup
        )

        commands = run.tick(
            now,
            communication_ok=not (active and scenario.kind == KIND_COMMS_LOSS),
            emergency_stop=active and scenario.kind == KIND_ESTOP,
            physical_start=True,
            mechanism_fault=mechanism_fault,
        )
        plant.runner = run.machine.route_runner
        plant.set_work_plan(run.work_plan)
        plant.apply(commands, now)

        segment = machine.current_segment
        if segment is not None and segment.id != last_segment:
            last_segment = segment.id
            segment_entered_at = now
            visited.append(segment.id)
            stall_since = now
            last_progress = f"进入 {segment.id}"

        if machine.state in (MissionState.COMPLETE, MissionState.FAILED, MissionState.SAFE_STOP):
            break

        pose, line_status = plant.observation()
        if not (active and scenario.kind == KIND_LINE_BLACKOUT):
            if active and scenario.kind == KIND_RAMP_STUCK:
                line_status = LineStatus(
                    state=line_status.state, stale=False, blocked=False,
                    deviation=line_status.deviation,
                    ramp_decision=SlipDecision.STUCK.value,
                )
            run.observe_pose(pose, now)
            run.observe_line_status(line_status, now)
        else:
            # 断流期间位姿照旧（真车上位姿是另一个来源），但巡线状态不再更新
            run.observe_pose(pose, now)

        step = run.work_plan.current if run.work_plan is not None else None
        is_place = step is not None and step.kind is WorkStepKind.PLACE
        for source, text in plant.step(config.dt_s, now):
            if text.startswith("SUCCESS"):
                if source == "manipulator":
                    # 「手里没有块却要放」——虚拟场地默认不模拟这件事，
                    # 但这正是跳段降级之后真实会遇到的场景，所以这里补上。
                    if is_place and machine.cargo.total <= 0:
                        text = "MECHANISM_ERROR: 夹爪里没有块（虚拟场地：cargo=0）"
                    elif active and scenario.kind == KIND_MANIPULATOR_FAILURE:
                        text = "MECHANISM_ERROR: 模拟夹爪失败"
                    else:
                        successes_seen += 1
                elif source == "motion" and active and scenario.kind == KIND_LOCALIZATION_ERROR:
                    text = "LOCALIZATION_ERROR: 模拟位姿不可信"
                else:
                    successes_seen += 1
            run.handle_result(source, text, now)

        marker = f"{machine.state.value}|{machine.segment_id}|{machine.retries}"
        if marker != last_progress:
            last_progress = marker
            stall_since = now
        elif now - stall_since > 90.0:
            stalls.append(f"{machine.segment_id} 卡住 >90s（{machine.detail}）")
            break

        now += config.dt_s

    return _evaluate(
        scenario, machine=machine, commands=commands, visited=visited,
        stalls=stalls, virtual_time_s=now,
    )


def _evaluate(
    scenario: FaultScenario,
    *,
    machine: MissionMachine,
    commands: list[Command],
    visited: list[str],
    stalls: list[str],
    virtual_time_s: float,
) -> FaultResult:
    """检查不变式与期望（分开报，便于区分「不安全」与「和预期不同」）。"""
    terminal = machine.state in (
        MissionState.COMPLETE, MissionState.FAILED, MissionState.SAFE_STOP
    )
    authority = ""
    leftovers: list[str] = []
    for command in commands:
        if command.kind is CommandKind.AUTHORITY:
            authority = command.text
        elif command.kind is CommandKind.GOAL:
            leftovers.append("GOAL")
        elif command.kind is CommandKind.MANIPULATOR:
            leftovers.append(f"MANIPULATOR:{command.text}")
        elif command.kind is CommandKind.LINE and command.text:
            leftovers.append("LINE")
        elif command.kind is CommandKind.TURN and command.text:
            leftovers.append("TURN")

    violations: list[str] = []
    # I1 终态
    if not terminal:
        violations.append(
            f"I1 没有收敛到终态（停在 {machine.state.value}，{virtual_time_s:.1f}s）"
        )
    # I2 释放授权
    if authority != SOURCE_NONE:
        violations.append(f"I2 终态没有释放底盘授权（最后一次授权 = {authority or '无'}）")
    # I3 无残留指令
    if leftovers:
        violations.append(f"I3 终态留下了残留指令：{', '.join(leftovers)}")
    # I4 诚实
    if machine.state is MissionState.COMPLETE and stalls:
        violations.append("I4 一边卡住一边报 COMPLETE")
    if terminal and not machine.detail.strip():
        violations.append("I4 终态没有给出原因（detail 为空）")
    # I5 重试上限。注意语义：`retries` 是**第几次尝试**，重试次数用尽时它会等于
    # max_retries + 1（那一次就是触发降级/判死的那次）。所以上限是 max_retries + 1，
    # 不是 max_retries——写成后者会得到假问题（R15 第一版就写错了）。
    if machine.retries > machine.config.max_retries + 1:
        violations.append(
            f"I5 重试计数 {machine.retries} 超过上限 {machine.config.max_retries + 1}"
        )

    mismatches: list[str] = []
    if scenario.expect_state and machine.state.value != scenario.expect_state:
        mismatches.append(f"终态 {machine.state.value} ≠ 期望 {scenario.expect_state}")
    if scenario.expect_state_in and machine.state.value not in scenario.expect_state_in:
        mismatches.append(
            f"终态 {machine.state.value} 不在可接受集合 {scenario.expect_state_in}"
        )
    if scenario.expect_result and machine.result.value != scenario.expect_result:
        mismatches.append(f"结果 {machine.result.value} ≠ 期望 {scenario.expect_result}")
    if scenario.expect_detail_contains and scenario.expect_detail_contains not in machine.detail:
        mismatches.append(f"原因里没有「{scenario.expect_detail_contains}」：{machine.detail}")
    if scenario.expect_detail_any and not any(
        token in machine.detail for token in scenario.expect_detail_any
    ):
        mismatches.append(
            f"原因里没有 {scenario.expect_detail_any} 中的任何一个：{machine.detail}"
        )
    for segment in scenario.expect_segments:
        if segment not in visited:
            mismatches.append(f"没有走到 {segment}")
    for segment in scenario.expect_not_segments:
        if segment in visited:
            mismatches.append(f"不该走到 {segment}，但走过了")
    if machine.degradations < scenario.min_degradations:
        mismatches.append(
            f"降级次数 {machine.degradations} < 期望 {scenario.min_degradations}"
        )

    return FaultResult(
        name=scenario.name,
        kind=scenario.kind,
        final_state=machine.state.value,
        result=machine.result.value,
        detail=machine.detail,
        degradations=machine.degradations,
        retries=machine.retries,
        segments_visited=visited,
        terminal_authority=authority,
        terminal_leftovers=leftovers,
        stalls=stalls,
        virtual_time_s=virtual_time_s,
        invariant_violations=violations,
        expectation_mismatches=mismatches,
    )


def run_all(**kwargs) -> list[FaultResult]:
    return [run_scenario(scenario, **kwargs) for scenario in scenarios()]


def format_table(results: list[FaultResult]) -> str:
    """故障矩阵表（现场文档可直接引用）。"""
    lines = [
        "故障注入矩阵（虚拟场地，纯逻辑）",
        "⚠️ 边界：本表证明「决策链在故障下收敛到安全终态」，**不证明**真车的传感器/执行器",
        "   会这样表现，也不证明故障检测本身（如「轮子空转车不走」轮速里程计看不到）。",
        "",
        "| 场景 | 注入故障 | 终态 | 结果 | 降级 | 走过关键段 | 不变式 |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in results:
        key_segments = "、".join(result.segments_visited[-3:]) or "-"
        lines.append(
            f"| {result.name} | {result.kind} | {result.final_state} | {result.result} | "
            f"{result.degradations} | {key_segments} | {'✅' if result.ok else '❌'} |"
        )
    failed = [r for r in results if not r.ok]
    lines.append("")
    if failed:
        lines.append(f"❌ {len(failed)}/{len(results)} 个场景不达标：")
        for result in failed:
            lines.extend("  " + line for line in result.summary().splitlines())
    else:
        lines.append(f"✅ {len(results)}/{len(results)} 个场景：全部满足安全不变式，且终态符合预期。")
    return "\n".join(lines)


def main() -> int:  # pragma: no cover - 现场手工运行
    stream = getattr(sys, "stdout", None)
    if callable(getattr(stream, "reconfigure", None)):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
    results = run_all()
    print(format_table(results))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
