from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from .mission_recovery import RecoveryAction, recovery_for_failure
from .mission_route import RoutePlan, RouteRunner, SegmentRole
from .models import Cargo, CubeColor, MissionResult


class MissionState(str, Enum):
    # A2 / P0-1: 上电启动前置状态——真实固件握手完成前 communication_ok=False
    # 是正常时序，此状态下只等待、不评估、不 fail（见 MissionMachine.tick）。
    WAIT_FOR_COMMUNICATION = "WAIT_FOR_COMMUNICATION"
    SELF_CHECK = "SELF_CHECK"
    WAIT_FOR_PHYSICAL_START = "WAIT_FOR_PHYSICAL_START"
    # B1 新增：全流程路线模式（启动区→巡线→上坡→取3→下坡→搭建2层）。
    # 绑定 RoutePlan 后走这条；未绑定（route=None）时仍走下面的旧演示流程。
    ROUTE_RUNNING = "ROUTE_RUNNING"
    # --- 以下为旧「单方块/一橙一紫」演示流程，B1 起对完整比赛 run 作废，
    # --- 但保留供 single_cube 演示链路与既有测试使用（不要删）。
    GO_TO_ORANGE = "GO_TO_ORANGE"
    PICK_ORANGE = "PICK_ORANGE"
    GO_TO_PURPLE = "GO_TO_PURPLE"
    PICK_PURPLE = "PICK_PURPLE"
    GO_TO_BUILD = "GO_TO_BUILD"
    PLACE_ORANGE = "PLACE_ORANGE"
    PLACE_PURPLE = "PLACE_PURPLE"
    RETREAT = "RETREAT"
    VERIFY_BUILD = "VERIFY_BUILD"
    SAFE_STOP = "SAFE_STOP"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class MissionPhase(str, Enum):
    """路线模式下的段内阶段（给网页/日志显示「现在在干什么」）。

    IDLE = 未开始或已结束；MOVING = 正在走/正在转；WORKING = 原地作业（抓/放）；
    VERIFY = 搭建后稳定观察（复用既有 VERIFY_BUILD 计时）。
    """

    IDLE = "IDLE"
    MOVING = "MOVING"
    WORKING = "WORKING"
    VERIFY = "VERIFY"


@dataclass(frozen=True)
class MissionConfig:
    orange_target: int = 1
    purple_target: int = 0
    max_retries: int = 2
    state_timeout_s: float = 20.0
    #: 运动段的超时预算按**本段实际需要的时间**推导：
    #:     budget = max(state_timeout_s, 本段长度 / 本段限速 × factor + extra)
    #: 为什么不能用一个常数：最长的主路段 2.8 m 在计划限速 0.25 m/s 下要 11.2 s，
    #: 20 s 只有 1.8 倍余量；现场一旦为了安全把限速调慢（例如减半），该段就会
    #: 「超时失败」而不是「只是变慢」——离线预演正是这样发现的（见工作留痕 R12）。
    timeout_margin_factor: float = 2.0
    timeout_extra_s: float = 5.0
    #: 段没写限速时用的默认速度（与 RouteChain 的 default_speed_mps 同源）
    route_default_speed: float = 0.15
    #: 作业段（取/放，含多次抓取 + 之间的车体微移）单独的超时预算。
    #: 依据 `docs/field/TIME_BUDGET.csv`：单次抓取典型 7.1 s、最坏 14.3 s；
    #: 「取 3 块 + 2 次侧移」最坏约 50 s，因此默认给 120 s 余量。
    #: ⚠️ 用 20 s（与运动段相同的预算）会让取件段**必然超时**——离线端到端预演
    #: （`tools/mission_sim.py`）正是这样发现的。
    work_state_timeout_s: float = 120.0
    build_stability_s: float = 3.0
    # A2 / P0-1: 上电等待通信就绪的上限（覆盖握手 3s×3 + 余量），
    # 超时才 fail(COMMUNICATION_ERROR)；等待期内不评估、不 fail。
    startup_wait_timeout_s: float = 15.0
    # B4：段失败重试耗尽后是否按「降级阶梯」继续（取块失败但有存货 → 去搭建等）。
    # 关掉就回到「一失败即判死」，便于现场对照排查。
    degrade_on_failure: bool = True
    # B4：比赛总时长上限（秒）。规则 3.2.1：正式比赛 6 分钟，计时结束后动作无效。
    # >0 时，路线执行超过这个时长就**安全停车**（而不是继续跑到自然结束）。
    match_time_limit_s: float = 360.0
    # B4：开赛前是否要求巡线标定**可用**（`/line_follow/status` 的 calibration_ready）。
    # 为什么要在任务层设这道门：上电自主意味着没有人会在赛前看一眼归一化读数。
    # 标定不可用时巡线的偏差是假的，车看起来「在巡线」但实际在乱走——这种失败
    # 无法在赛中补救，只能在开赛前拒绝启动并说清原因。
    # 默认 False：单元测试与 mock 演示不接真线，不能因为缺标定就跑不起来；
    # 正式场地配置（robot_field.yaml）把它打开。
    require_line_calibration: bool = False


def line_calibration_blocker(
    config: MissionConfig, ready: bool | None
) -> str | None:
    """开赛前置条件：巡线标定是否可用。

    返回 `None` = 放行；返回字符串 = **必须拒绝开赛**，字符串就是要给人看的原因。

    三态语义（`ready`）：

    - `True`：巡线节点确认在用一份可二值化的基准 → 放行；
    - `False`：节点明确说标定不可用 → 拒绝；
    - `None`：没收到过 `/line_follow/status`（或已断流）→ **也拒绝**。
      为什么「不知道」要当「不行」：上电自主模式下没有任何人在赛前看一眼读数，
      标定不可用时的巡线偏差是假数据，车会「看起来在巡线」地走错路线。
      这种失败赛中无法补救，只能开赛前拦下。宁可不开赛，不可瞎跑。
    """
    if not config.require_line_calibration:
        return None
    if ready is True:
        return None
    if ready is False:
        return (
            "开赛被拒绝：巡线黑白标定不可用（/line_follow/status calibration_ready=false）。"
            "请在网页面板完成黑白标定并落盘后重新上电"
        )
    return (
        "开赛被拒绝：没有收到巡线标定状态（/line_follow/status 未上报或已断流）。"
        "请确认 line_follow_controller 已启动并在上报 calibration_ready"
    )


def segment_required_time_s(segment, *, default_speed: float = 0.15) -> float:
    """本段「按几何与限速」需要的时间（秒）。

    段长度取实测两点距离（或原地段的 0）；限速为 0（未覆盖）时用 `default_speed`。
    用途：按段推导超时预算（见 `MissionConfig.timeout_margin_factor`）。
    """
    import math

    length = math.hypot(
        segment.to_pose.x - segment.from_pose.x,
        segment.to_pose.y - segment.from_pose.y,
    )
    speed = segment.max_speed_mps if segment.max_speed_mps > 0.0 else default_speed
    return length / speed if speed > 0.0 else 0.0


def classify_action_result(data: str) -> tuple[bool, MissionResult | None, str]:
    """Classify an action-result string into a machine tick.

    Returns ``(succeeded, failure_result, detail)``:

    - ``SUCCESS`` / ``STABLE`` / ``INCONCLUSIVE`` -> succeeded; INCONCLUSIVE
      intentionally does NOT fail the mission: it enters the mission-level
      VERIFY_BUILD timer instead of being treated as MECHANISM_ERROR (A4/P0-6).
    - anything else -> failed, with the longest matching ``MissionResult``
      member (``INCONCLUSIVE`` is matched before the generic fallback).
    """
    prefix = data.split(":", 1)[0].strip()
    if prefix in {"SUCCESS", "STABLE", MissionResult.INCONCLUSIVE.value}:
        return True, None, data
    result = MissionResult.__members__.get(prefix)
    if result is None:
        result = MissionResult.MECHANISM_ERROR
    return False, result, data


@dataclass
class MissionMachine:
    config: MissionConfig = field(default_factory=MissionConfig)
    cargo: Cargo = field(default_factory=Cargo)
    state: MissionState = MissionState.WAIT_FOR_COMMUNICATION
    retries: int = 0
    result: MissionResult = MissionResult.RUNNING
    detail: str = ""
    entered_at: float = field(default_factory=time.monotonic)
    # B1：绑定 RoutePlan 后走路线模式（ROUTE_RUNNING），否则走旧的演示流程。
    route: RoutePlan | None = None
    segment_index: int = -1
    segment_id: str = ""
    phase: MissionPhase = MissionPhase.IDLE
    #: B4：已执行的降级次数（取块失败但有存货 → 去搭建 等），网页/日志可见
    degradations: int = 0
    #: B4：路线开始时刻（比赛总时钟的起点）
    route_started_at: float | None = None
    _route_runner: RouteRunner | None = field(default=None, repr=False)

    @property
    def route_runner(self) -> RouteRunner | None:
        """路线运行器（未进入路线模式时为 None）。"""
        return self._route_runner

    @property
    def current_segment(self):
        """当前段计划（未进入路线模式时为 None）。"""
        runner = self._route_runner
        return None if runner is None else runner.current_segment
    def _enter(self, state: MissionState, now: float) -> None:
        self.state = state
        self.entered_at = now
        self.retries = 0
        self.result = MissionResult.RUNNING
        self.detail = ""

    def fail(self, result: MissionResult, detail: str, now: float | None = None) -> MissionState:
        if result is MissionResult.RUNNING or result is MissionResult.SUCCESS:
            raise ValueError("failure requires a failure result")
        self.result = result
        self.detail = detail
        self.state = MissionState.FAILED
        self.entered_at = time.monotonic() if now is None else now
        return self.state

    def tick(
        self,
        *,
        now: float | None = None,
        communication_ok: bool = True,
        emergency_stop: bool = False,
        physical_start: bool = False,
        line_calibration_ready: bool | None = None,
        action_succeeded: bool = False,
        action_failed: bool = False,
        failure_result: MissionResult = MissionResult.MECHANISM_ERROR,
        failure_detail: str = "action reported failure",
    ) -> MissionState:
        now = time.monotonic() if now is None else now
        if emergency_stop:
            self._enter(MissionState.SAFE_STOP, now)
            self.result = MissionResult.SAFETY_STOP
            self.detail = "emergency stop active"
            return self.state
        if self.state is MissionState.WAIT_FOR_COMMUNICATION:
            # A2 / P0-1: 上电握手期间 communication_ok=False 是正常时序。
            # 在 startup_wait_timeout_s 内持续等待、不评估、不 fail；
            # 超时才 fail(COMMUNICATION_ERROR)。通信就绪则转入 SELF_CHECK，
            # 并在同一 tick 内继续按 SELF_CHECK 语义处理（mechanism 就绪即前进）。
            if not communication_ok:
                if now - self.entered_at > self.config.startup_wait_timeout_s:
                    return self.fail(
                        MissionResult.COMMUNICATION_ERROR,
                        "communication not ready within startup window",
                        now,
                    )
                return self.state
            self._enter(MissionState.SELF_CHECK, now)
        elif not communication_ok:
            # 运行时语义不变：离开启动等待后，心跳丢失仍立即按失败处理。
            return self.fail(MissionResult.COMMUNICATION_ERROR, "hardware heartbeat lost", now)
        if self.state in {MissionState.COMPLETE, MissionState.FAILED, MissionState.SAFE_STOP}:
            return self.state
        if self.state is MissionState.ROUTE_RUNNING:
            # B1：路线模式自己管超时（按段计时，不是整条路线一个预算），
            # 因此放在通用 state_timeout_s 检查之前。
            return self._tick_route(
                now=now,
                action_succeeded=action_succeeded,
                action_failed=action_failed,
                failure_result=failure_result,
                failure_detail=failure_detail,
            )
        if now - self.entered_at > self.config.state_timeout_s:
            return self._retry_or_fail(MissionResult.TIMEOUT, "state timeout", now)

        if self.state is MissionState.SELF_CHECK and action_succeeded:
            self._enter(MissionState.WAIT_FOR_PHYSICAL_START, now)
        elif self.state is MissionState.WAIT_FOR_PHYSICAL_START and physical_start:
            # B4 开赛门：只在**真的要开赛**这一拍评估。
            # 放在这里而不是 SELF_CHECK，是因为标定可能在 SELF_CHECK 之后才被
            # 面板推送/落盘；放在这里能吃到最后一刻的观测，也不会误拦 mock 演示。
            blocker = line_calibration_blocker(self.config, line_calibration_ready)
            if blocker is not None:
                return self.fail(MissionResult.MECHANISM_ERROR, blocker, now)
            if self.route is not None:
                self._enter_route(now)
            else:
                self._enter(MissionState.GO_TO_ORANGE, now)
        elif action_failed:
            return self._retry_or_fail(failure_result, failure_detail, now)
        elif action_succeeded:
            self._advance_after_success(now)
        return self.state

    def _retry_or_degrade(
        self, result: MissionResult, detail: str, now: float
    ) -> MissionState:
        """路线模式下的失败处理：先重试，重试耗尽后走降级阶梯（B4）。

        与旧的 `_retry_or_fail` 的区别：重试耗尽**不一定**判死——
        取块失败但框里已有块时，跳到搭建段继续（那些块仍然计分）；
        位置/朝向不可信时（丢线、没转过去、微移失败）才安全停车。
        """
        self.retries += 1
        self.entered_at = now
        runner = self._route_runner
        if self.retries <= self.config.max_retries:
            self.result = result
            self.detail = f"retry {self.retries}: {detail}"
            if runner is not None:
                # 还在重试本段：清本段累计量，否则已满足的判据会立刻再次触发。
                runner.observations.reset_segment()
            return self.state

        if not self.config.degrade_on_failure:
            return self.fail(result, f"retry limit exceeded: {detail}", now)

        decision = recovery_for_failure(self.route, self.current_segment, self.cargo)
        target = decision.target_segment_id
        if decision.action is RecoveryAction.SAFE_STOP or target is None or runner is None:
            return self.fail(
                result, f"retry limit exceeded: {detail}；降级为安全停车：{decision.reason}", now
            )
        try:
            runner.skip_to(target)
        except ValueError as exc:  # 目标段不在计划里：宁可不降级
            return self.fail(
                result, f"retry limit exceeded: {detail}；降级失败（{exc}）", now
            )
        self.degradations += 1
        self.retries = 0
        self.result = MissionResult.RUNNING
        self.detail = f"degraded: {decision.reason} → {target}"
        self._sync_route()
        return self.state

    def segment_timeout_budget(self) -> float:
        """当前段的超时预算（秒）。

        - 作业段：固定的长预算（多次抓取 + 微移本来就要几十秒）；
        - 运动段：按「本段长度 / 本段限速」推导（有下限与余量），
          这样现场把限速调慢只会**变慢**，不会变成「超时失败」。
        - 不在路线模式 / 没有当前段：退回 `state_timeout_s`。
        """
        segment = self.current_segment
        if segment is None:
            return self.config.state_timeout_s
        if segment.role is SegmentRole.WORK:
            return self.config.work_state_timeout_s
        required = segment_required_time_s(
            segment, default_speed=self.config.route_default_speed
        )
        return max(
            self.config.state_timeout_s,
            required * self.config.timeout_margin_factor + self.config.timeout_extra_s,
        )

    # -- B1: 路线模式 -----------------------------------------------------
    def _enter_route(self, now: float) -> None:
        """进入 ROUTE_RUNNING：建运行器、从第一段开始、同步段信息。"""
        self._enter(MissionState.ROUTE_RUNNING, now)
        self._route_runner = RouteRunner(self.route)
        self._route_runner.start()
        self.route_started_at = now
        self._sync_route()

    def match_remaining_s(self, now: float | None = None) -> float | None:
        """比赛剩余时间（秒）；未开始或未设上限时返回 None。"""
        limit = self.config.match_time_limit_s
        if limit <= 0.0 or self.route_started_at is None:
            return None
        now = time.monotonic() if now is None else now
        return max(0.0, limit - (now - self.route_started_at))

    def _sync_route(self) -> None:
        """把运行器状态同步到对外字段（网页/日志用）。"""
        runner = self._route_runner
        if runner is None:
            self.segment_index = -1
            self.segment_id = ""
            self.phase = MissionPhase.IDLE
            return
        segment = runner.current_segment
        self.segment_index = runner.segment_index
        self.segment_id = segment.id if segment is not None else ""
        if segment is None:
            self.phase = MissionPhase.IDLE
        elif segment.role is SegmentRole.WORK:
            self.phase = MissionPhase.WORKING
        else:
            self.phase = MissionPhase.MOVING

    def _tick_route(
        self,
        *,
        now: float,
        action_succeeded: bool,
        action_failed: bool,
        failure_result: MissionResult,
        failure_detail: str,
    ) -> MissionState:
        runner = self._route_runner
        if runner is None:
            return self.fail(MissionResult.MECHANISM_ERROR, "route mode without runner", now)

        # B4：比赛总时钟（规则 3.2.1：6 分钟，计时结束后动作无效）。
        # 到点就**安全停车并释放授权**，而不是继续跑到自然结束。
        limit = self.config.match_time_limit_s
        if limit > 0.0 and self.route_started_at is not None:
            elapsed = now - self.route_started_at
            if elapsed > limit:
                self._enter(MissionState.SAFE_STOP, now)
                self.result = MissionResult.SAFETY_STOP
                self.detail = (
                    f"match time limit reached: {elapsed:.1f}s > {limit:.1f}s"
                    "（比赛时间到，安全停车）"
                )
                return self.state

        # 每段一个超时预算：entered_at 在段切换时刷新（见下面 switched 分支）。
        budget = self.segment_timeout_budget()
        if now - self.entered_at > budget:
            segment_id = self.segment_id or "?"
            return self._retry_or_degrade(
                MissionResult.TIMEOUT, f"segment {segment_id} timeout", now
            )
        if action_failed:
            return self._retry_or_degrade(failure_result, failure_detail, now)

        segment = runner.current_segment
        if action_succeeded and segment is not None and segment.role is SegmentRole.WORK:
            # WORK 段：一次作业完成（抓一块/放一块）记一次；达到 required_count 才退出。
            runner.note_work_done()

        switched = runner.tick()
        if switched:
            self.entered_at = now  # 下一段重新计时
        self._sync_route()

        if runner.is_complete:
            # 3s 稳定观察仍由既有 VERIFY_BUILD 计时兜底（build_stability_s）。
            self._enter(MissionState.VERIFY_BUILD, now)
            self.phase = MissionPhase.VERIFY
            self.detail = "route complete"
            # 降级过就必须写出来（R15 故障矩阵发现）：降级会让任务**继续走到终点**，
            # 于是终态是 COMPLETE/SUCCESS——只看这一行会以为「全都做成了」。
            # 无人干预模式下没人会去翻 degradations，所以把话写在这句里。
            if self.degradations:
                self.detail = (
                    f"route complete（降级 {self.degradations} 次：有段未按计划完成，"
                    "COMPLETE 不等于全部作业都做成了；明细见 /mission/route 的 degradations）"
                )
        return self.state

    def route_progress(self, now: float | None = None) -> dict[str, object]:
        """路线进度快照（B2 推给网页显示）。

        `now` 只用于测试注入模拟时钟；生产路径传 None 即用真实单调时钟。
        """
        runner = self._route_runner
        segment = None if runner is None else runner.current_segment
        total = 0 if self.route is None else len(self.route.segments)
        # 注意：C1 的 RouteChain 走完最后一段时把状态置 COMPLETE，但 current_index
        # 停在最后一段（不 +1）。所以「已完成段数」要单独算，别用 index 糊过去。
        if runner is None:
            completed = 0
        elif runner.is_complete:
            completed = total
        else:
            completed = max(runner.segment_index, 0)
        return {
            "state": self.state.value,
            "phase": self.phase.value,
            "segment_index": self.segment_index,
            "segments_completed": completed,
            "segment_id": self.segment_id,
            "segment_label": "" if segment is None else segment.label,
            "segment_count": total,
            "retries": self.retries,
            "degradations": self.degradations,
            "match_remaining_s": None
            if self.match_remaining_s(now) is None
            else round(self.match_remaining_s(now), 1),
            "detail": self.detail,
        }

    def _retry_or_fail(self, result: MissionResult, detail: str, now: float) -> MissionState:
        self.retries += 1
        self.entered_at = now
        if self.retries <= self.config.max_retries:
            self.result = result
            self.detail = f"retry {self.retries}: {detail}"
            return self.state
        return self.fail(result, f"retry limit exceeded: {detail}", now)

    def _advance_after_success(self, now: float) -> None:
        if self.state is MissionState.GO_TO_ORANGE:
            self._enter(MissionState.PICK_ORANGE, now)
        elif self.state is MissionState.PICK_ORANGE:
            self.cargo.add(CubeColor.ORANGE)
            if self.cargo.orange < self.config.orange_target:
                self._enter(MissionState.GO_TO_ORANGE, now)
            elif self.config.purple_target:
                self._enter(MissionState.GO_TO_PURPLE, now)
            else:
                self._enter(MissionState.GO_TO_BUILD, now)
        elif self.state is MissionState.GO_TO_PURPLE:
            self._enter(MissionState.PICK_PURPLE, now)
        elif self.state is MissionState.PICK_PURPLE:
            self.cargo.add(CubeColor.PURPLE)
            self._enter(MissionState.GO_TO_BUILD, now)
        elif self.state is MissionState.GO_TO_BUILD:
            self._enter(MissionState.PLACE_ORANGE, now)
        elif self.state is MissionState.PLACE_ORANGE:
            self.cargo.remove(CubeColor.ORANGE)
            if self.cargo.orange:
                self._enter(MissionState.PLACE_ORANGE, now)
            elif self.cargo.purple:
                self._enter(MissionState.PLACE_PURPLE, now)
            else:
                self._enter(MissionState.RETREAT, now)
        elif self.state is MissionState.PLACE_PURPLE:
            self.cargo.remove(CubeColor.PURPLE)
            self._enter(MissionState.RETREAT, now)
        elif self.state is MissionState.RETREAT:
            self._enter(MissionState.VERIFY_BUILD, now)
        elif self.state is MissionState.VERIFY_BUILD:
            self._enter(MissionState.COMPLETE, now)
            self.result = MissionResult.SUCCESS
            # 降级过就必须写出来（R15 故障矩阵发现）：降级让任务继续走到终点，
            # 终态是 COMPLETE/SUCCESS——只看这一行会以为「全都做成了」。
            self.detail = (
                "mission complete"
                if not self.degradations
                else (
                    f"mission complete（降级 {self.degradations} 次：有段未按计划完成，"
                    "COMPLETE 不等于全部作业都做成了；明细见 /mission/route 的 degradations）"
                )
            )
