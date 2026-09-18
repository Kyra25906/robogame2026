"""离线端到端预演：用虚拟场地把整条比赛路线跑一遍（纯逻辑，零 ROS）。

## 为什么需要它

到这一轮为止，验证分两层：单模块单测（跑得快但看不到交互）+ 静态集成审计
（看得到接线但不知道运行时会不会收敛）。中间缺一层——**决策链能不能真的走完全程**。
典型失败：某一段的退出判据永远不成立、重发命令没人理、作业步骤卡住不动。
这些在单文件测试里都看不见，只有把整条链跑起来才会暴露。

## 虚拟场地是什么、不是什么

**是**：命令 → 位姿/巡线状态 → 结果 这条语义链的迷你实现。
巡线被理想化为「车始终保持在当前段的中心线上」（不做 PD、不打滑、无噪声）。

**不是**物理仿真：不验证动力学、打滑、噪声、通信延迟。
所以它只能证明「决策链收敛、命令与授权合理」，**不能**证明真车能跑。

## 怎么用

```python
from tools.mission_sim import simulate_route
result = simulate_route()          # 默认：单趟（与现场配置一致）
print(result.summary())
```
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 让「开发机上直接 python tools/mission_sim.py」也能跑：
# 开发机不 source ROS，`robogame_core` 不在 import 路径上（真车 colcon 安装后本来就在）。
# 与 tools/grasp_alignment_sim.py、tools/serial_v1_acceptance.py 同一套做法。
_CORE_SRC = Path(__file__).resolve().parents[1] / "ros2_ws" / "src" / "robogame_core"
if str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

from robogame_core.line_follow import LineSensorState  # noqa: E402
from robogame_core.mission import MissionConfig, MissionMachine, MissionState
from robogame_core.mission_dispatch import LineStatus
from robogame_core.mission_route import RoutePlan
from robogame_core.mission_run import Command, CommandKind, MissionRun
from robogame_core.models import Pose2D
from robogame_core.route_loader import load_route_plan
from robogame_core.work_sequence import WorkStepKind

#: 到达判据的距离容差（虚拟场地用）
ARRIVE_TOLERANCE_M = 0.03
#: 距离路口/线尽头多近时开始给「路口签名 / 丢线」帧
FEATURE_RADIUS_M = 0.08


@dataclass
class SimConfig:
    """虚拟场地参数（都是占位值，只影响仿真时长，不影响真车）。"""

    dt_s: float = 0.05
    goal_speed_mps: float = 0.12
    turn_rate_radps: float = 0.6
    pick_s: float = 6.0
    place_s: float = 5.0
    max_virtual_s: float = 360.0
    #: 速度倍率：乘在「路线登记表的分段限速」与位姿目标速度上。
    #: 用来做时间预算敏感性分析（现场把限速整体调快/调慢会怎样）。
    #: 1.0 = 按计划限速跑。注意它**不能**超过底盘限幅的实际含义——
    #: 这里只是仿真旋钮，真车上限速仍由 robot.yaml 与固件限幅约束。
    speed_scale: float = 1.0


@dataclass
class SimResult:
    completed: bool
    virtual_time_s: float
    segments_visited: list[str]
    authority_by_segment: dict[str, list[str]]
    counters: dict[str, int]
    stalls: list[str]
    final_state: str
    detail: str

    def summary(self) -> str:
        lines = [
            f"路线{'走完了' if self.completed else '没走完'}："
            f"{len(self.segments_visited)} 段、虚拟 {self.virtual_time_s:.1f} s",
            f"终态 {self.final_state}（{self.detail}）",
            "计数：" + "，".join(f"{key}={value}" for key, value in sorted(self.counters.items())),
        ]
        if self.stalls:
            lines.append("卡住：" + "；".join(self.stalls))
        return "\n".join(lines)


@dataclass
class VirtualPlant:
    """虚拟场地：消费命令、积分位姿、产生观测与结果。"""

    plan: RoutePlan
    runner: object  # RouteRunner（用来知道「现在在哪一段」）
    config: SimConfig = field(default_factory=SimConfig)
    pose: Pose2D | None = None
    authority: str = ""
    line_limit_mps: float = 0.2
    goal: Pose2D | None = None
    pending_manipulator: tuple[float, str] | None = None  # (完成时刻, 命令)
    pending_turn: dict | None = None  # 收到的转弯命令（等到路口才开始转）
    turning: bool = False  # 正在原地转
    creep_remaining_m: float = 0.0  # 转完弯后要沿新方向驶离路口的剩余距离
    counters: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.pose is None:
            self.pose = self.plan.start_pose
        self.counters = {
            "goals": 0, "manipulator_commands": 0, "motion_results": 0,
            "manipulator_results": 0, "authority_switches": 0, "turn_commands": 0,
        }

    # -- 命令 -------------------------------------------------------------
    def apply(self, commands: list[Command], now: float) -> list[tuple[str, str]]:
        """处理本拍命令，返回要回送给任务层的结果。"""
        results: list[tuple[str, str]] = []
        for command in commands:
            if command.kind is CommandKind.AUTHORITY:
                if command.text != self.authority:
                    self.counters["authority_switches"] += 1
                self.authority = command.text
            elif command.kind is CommandKind.GOAL:
                self.goal = command.pose
                self.counters["goals"] += 1
            elif command.kind is CommandKind.LINE:
                if command.text:
                    import json

                    self.line_limit_mps = (
                        json.loads(command.text)["max_speed_mps"] * self.config.speed_scale
                    )
            elif command.kind is CommandKind.TURN:
                if command.text:
                    import json

                    self.pending_turn = json.loads(command.text)
                    self.counters["turn_commands"] += 1
                else:
                    self.pending_turn = None
                    self.turning = False
            elif command.kind is CommandKind.MANIPULATOR:
                if self.pending_manipulator is None:
                    self.counters["manipulator_commands"] += 1
                    step = self._current_step()
                    duration = (
                        self.config.place_s
                        if step is not None and step.kind is WorkStepKind.PLACE
                        else self.config.pick_s
                    )
                    self.pending_manipulator = (now + duration, command.text)
        return results

    def _current_step(self):
        plan = getattr(self, "_work_plan", None)
        return None if plan is None else plan.current

    def set_work_plan(self, work_plan) -> None:
        self._work_plan = work_plan

    # -- 积分与观测 --------------------------------------------------------
    def step(self, dt: float, now: float) -> list[tuple[str, str]]:
        """按当前授权推进位姿，并按时间产生结果。"""
        results: list[tuple[str, str]] = []
        segment = self._segment()

        if self.pending_manipulator is not None:
            ready_at, _text = self.pending_manipulator
            if now >= ready_at:
                self.pending_manipulator = None
                self.counters["manipulator_results"] += 1
                results.append(("manipulator", "SUCCESS"))

        if segment is None:
            return results

        if self.goal is not None and self.authority == "motion_control":
            # 位姿控制：先对齐朝向再平移（原地掉头段就是「同位置、新朝向」）
            yaw_error = _wrap(self.goal.yaw - self.pose.yaw)
            if abs(yaw_error) > math.radians(5.0):
                step = min(self.config.turn_rate_radps * dt, abs(yaw_error))
                self.pose = Pose2D(
                    self.pose.x, self.pose.y,
                    self.pose.yaw + math.copysign(step, yaw_error),
                )
            else:
                dx = self.goal.x - self.pose.x
                dy = self.goal.y - self.pose.y
                distance = math.hypot(dx, dy)
                if distance <= ARRIVE_TOLERANCE_M:
                    self.pose = Pose2D(self.pose.x, self.pose.y, self.goal.yaw)
                    self.goal = None
                    self.counters["motion_results"] += 1
                    results.append(("motion", "SUCCESS"))
                else:
                    step = min(
                        self.config.goal_speed_mps * self.config.speed_scale * dt, distance
                    )
                    self.pose = Pose2D(
                        self.pose.x + dx / distance * step,
                        self.pose.y + dy / distance * step,
                        self.goal.yaw,
                    )
        elif self.authority == "line_follow":
            if not self.turning and self.pending_turn is not None and self._at_turn_node():
                # 到路口了才开始原地转（真实节点也是这个相位：先巡线到路口）
                self.turning = True
                self.counters["turns_started"] = self.counters.get("turns_started", 0) + 1
            if self.turning:
                # 原地转向：把朝向转到「下一段」的方向；转到 ±3° 内就停下
                # （真实情况：转到位后线重新出现在阵列正下方，见 observation()）
                target_yaw = self._next_segment_yaw()
                delta = _wrap(target_yaw - self.pose.yaw)
                if abs(delta) <= math.radians(3.0):
                    self.pose = Pose2D(self.pose.x, self.pose.y, target_yaw)
                    self.turning = False
                    self.pending_turn = None
                    # 转完必须沿新方向「驶离路口」：否则车停在路口节点上，
                    # 阵列会一直报路口签名，退出判据永远等不到「线回到正中」。
                    self.creep_remaining_m = 0.12
                    self.counters["turns_completed"] = (
                        self.counters.get("turns_completed", 0) + 1
                    )
                else:
                    step = self.config.turn_rate_radps * dt
                    self.pose = Pose2D(
                        self.pose.x, self.pose.y,
                        self.pose.yaw + math.copysign(min(abs(delta), step), delta),
                    )
            else:
                # 理想巡线：沿本段中心线前进，并把**横向偏差纠回去**（有界纠偏速率）。
                # 为什么必须纠偏：取块时车被侧移挪开约 0.3 m，如果只会沿朝向直走，
                # 返程就会一直偏在线的旁边、永远走不到线尽头——真车上巡线会把它纠回来。
                from_pose = segment.from_pose
                to_pose = segment.to_pose
                dir_x = to_pose.x - from_pose.x
                dir_y = to_pose.y - from_pose.y
                length = math.hypot(dir_x, dir_y) or 1.0
                dir_x, dir_y = dir_x / length, dir_y / length
                # 车相对中心线的横向偏差（左正/右负按叉积符号）
                rel_x = self.pose.x - from_pose.x
                rel_y = self.pose.y - from_pose.y
                lateral = dir_x * rel_y - dir_y * rel_x
                heading = math.atan2(dir_y, dir_x)
                # 有界纠偏：朝向朝着线偏一个小角度，位置按比例回线
                correction_angle = max(-math.radians(30.0),
                                       min(math.radians(30.0), -lateral * 6.0))
                self.pose = Pose2D(
                    self.pose.x + math.cos(heading + correction_angle) * self.line_limit_mps * dt,
                    self.pose.y + math.sin(heading + correction_angle) * self.line_limit_mps * dt,
                    _wrap(heading + correction_angle),
                )
        return results

    def _segment(self):
        return None if self.runner is None else self.runner.current_segment

    def _at_turn_node(self) -> bool:
        """是否已经开到本轮转弯的路口节点（误差 FEATURE_RADIUS 内）。"""
        ref = (self.pending_turn or {}).get("ref")
        if not ref or ref not in self.plan.refs:
            return False
        target = self.plan.refs[ref]
        return math.hypot(target.x - self.pose.x, target.y - self.pose.y) <= FEATURE_RADIUS_M

    def _next_segment_yaw(self) -> float:
        runner = self.runner
        plan = self.plan
        index = min(runner.segment_index + 1, len(plan.segments) - 1)
        return plan.segments[index].from_pose.yaw

    def observation(self) -> tuple[Pose2D, LineStatus]:
        """当前观测：位姿 + 巡线状态（几何推导，不是「按判据编出来」的）。"""
        segment = self._segment()
        if segment is None:
            return self.pose, LineStatus(
                state=LineSensorState.ON_LINE, stale=False, blocked=False, deviation=0.0
            )
        to_pose = segment.to_pose
        distance_left = math.hypot(to_pose.x - self.pose.x, to_pose.y - self.pose.y)
        if self.turning:
            # 转向中：探头扫过侧面 → 看不到线；**转到位后线回到阵列正下方** → 重新看到。
            # （若这里一直报 LOST，转弯器永远等不到「线重新居中」，转向就永远完不成。）
            target_yaw = self._next_segment_yaw()
            aligned = abs(_wrap(target_yaw - self.pose.yaw)) <= math.radians(5.0)
            state = LineSensorState.ON_LINE if aligned else LineSensorState.LOST
        elif distance_left <= FEATURE_RADIUS_M:
            node_type = self.plan.node_types.get(segment.to_ref, "")
            if node_type in ("junction", "turn"):
                state = LineSensorState.INTERSECTION
            elif node_type in ("end", "ramp_start", "ramp_end"):
                state = LineSensorState.LOST
            else:
                state = LineSensorState.ON_LINE
        else:
            state = LineSensorState.ON_LINE
        return self.pose, LineStatus(
            state=state, stale=False, blocked=False,
            deviation=0.01 * math.sin(self.pose.x * 10.0),
        )


def Pose3D_like(x: float, y: float, yaw: float) -> Pose2D:
    return Pose2D(x, y, yaw)


def _wrap(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def simulate_route(
    *,
    rounds: int = 1,
    sim_config: SimConfig | None = None,
    mission_config: MissionConfig | None = None,
    layout_path=None,
) -> SimResult:
    """跑一遍完整路线，返回过程与结果（纯逻辑）。

    默认用仓库里的场地图（按本文件位置解析，与当前工作目录无关）。
    """
    if layout_path is None:
        layout_path = (
            Path(__file__).resolve().parents[1]
            / "ros2_ws/src/robogame_bringup/config/field_layout.yaml"
        )
    plan = load_route_plan(layout_path, rounds=rounds)
    config = sim_config or SimConfig()
    machine = MissionMachine(mission_config or MissionConfig(), route=plan)
    run = MissionRun(machine=machine, plan=plan, cargo_plan=plan.cargo_plan)
    plant = VirtualPlant(plan=plan, runner=None, config=config)

    now = 0.0
    visited: list[str] = []
    authority_by_segment: dict[str, list[str]] = {}
    last_segment = ""
    stalls: list[str] = []
    stall_since = now
    last_progress = ""

    while now < config.max_virtual_s:
        # 起跑：先把状态机推到路线模式
        commands = run.tick(now, communication_ok=True, emergency_stop=False,
                            physical_start=True, mechanism_fault=False)
        plant.runner = run.machine.route_runner
        plant.set_work_plan(run.work_plan)
        plant.apply(commands, now)

        segment = run.machine.current_segment
        if segment is not None and segment.id != last_segment:
            last_segment = segment.id
            visited.append(segment.id)
            stall_since = now
            last_progress = f"进入 {segment.id}"
        if run.machine.state in (MissionState.COMPLETE, MissionState.FAILED, MissionState.SAFE_STOP):
            break

        pose, line_status = plant.observation()
        run.observe_pose(pose, now)
        run.observe_line_status(line_status, now)
        if segment is not None:
            authority_by_segment.setdefault(segment.id, [])
            if plant.authority and (
                not authority_by_segment[segment.id]
                or authority_by_segment[segment.id][-1] != plant.authority
            ):
                authority_by_segment[segment.id].append(plant.authority)

        for source, text in plant.step(config.dt_s, now):
            run.handle_result(source, text, now)

        # 卡住检测：既没换段也没换状态，超过 60 s 虚拟时间就记一笔
        marker = f"{run.machine.state.value}|{run.machine.segment_id}|{run.machine.retries}"
        if marker != last_progress:
            last_progress = marker
            stall_since = now
        elif now - stall_since > 60.0:
            stalls.append(f"{run.machine.segment_id} 卡住 >60s（{run.machine.detail}）")
            break

        now += config.dt_s

    return SimResult(
        completed=run.machine.state in (MissionState.VERIFY_BUILD, MissionState.COMPLETE),
        virtual_time_s=now,
        segments_visited=visited,
        authority_by_segment=authority_by_segment,
        counters=dict(plant.counters),
        stalls=stalls,
        final_state=run.machine.state.value,
        detail=run.machine.detail,
    )


def main() -> int:  # pragma: no cover - 现场手工运行
    """命令行入口。

    为什么补这个入口：现场文档（`docs/field/上电自主完赛流程.md` 第二节）写着
    「改动过后先跑 `python3 tools/mission_sim.py` 看一眼」，但这个文件原先**没有**
    `__main__`，跑出来什么都没有——照着文档做的人会以为「预演通过了」。
    文档里的命令必须真的能用，否则它就是假的。
    """
    result = simulate_route()
    print(result.summary())
    return 0 if result.completed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
