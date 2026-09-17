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
        if now - self.entered_at > self.config.state_timeout_s:
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
        return self.state

    def route_progress(self) -> dict[str, object]:
        """路线进度快照（B2 推给网页显示）。"""
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
            if self.match_remaining_s() is None
            else round(self.match_remaining_s(), 1),
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
            self.detail = "mission complete"
