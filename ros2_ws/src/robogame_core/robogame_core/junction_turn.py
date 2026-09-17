"""B3: 路口转弯器（纯逻辑，零 ROS）——「到路口了、转过去了」由巡线自己判断。

## 为什么不用里程计判断转弯

仓库多处记录：里程计有 10 倍量级偏差、`/pose` 还会偶发断流 0.5~1.6 s
（`docs/field/ODOM_DROP_ROOT_CAUSE_2026-08-19.md`）。用没标定的里程计去判断
「转够 90° 没有」，等于把转弯押在一个已知不可信的量上。所以本模块的主判据是
**巡线阵列自己**：

    APPROACH  沿当前线前进，等路口签名（多路黑）连续出现
    TURNING   原地按固定角速度转，等「线重新出现在正下方且居中」
    SETTLE    停住，连续若干拍确认线稳定居中（抗抖动）
    DONE      交回巡线控制器继续走
    超时/读数过期过久 → FAILED（停车）

航向（IMU/里程计）只作为**可选**的附加确认（`use_yaw_check`，默认关闭），
因为在标定完成前它不可信——但现场量准之后打开它会更稳。

## 这些数都是占位值

`TurnParams` 里除 `direction` 外全部是占位值（fake），必须现场整定：
- `turn_rate_radps`：原地转向角速度。太快会转过头，太慢会在 6 分钟预算里浪费；
- `min_turn_s`：最短转向时间。**防止刚看到路口就判定「转完了」**（T 形路口
  转向过程中会短暂看到垂直的那条线）；
- `center_tolerance`：判定「线回到正下方」的偏差上限；
- `reacquire_samples`：稳定拍数。

现场整定顺序与判据见 `docs/field/现场待测清单_B2B3_待填值.md` B2 节。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .line_follow import LineSensorState
from .mission_route import JUNCTION_STATES, TurnDirection, TurnSpec

#: 90° 转弯的理论角度
RIGHT_ANGLE_RAD = math.pi / 2.0
#: 掉头的理论角度
STRAIGHT_ANGLE_RAD = math.pi


class TurnPhase(str, Enum):
    """转弯的四个阶段 + 两个终态。"""

    APPROACH = "APPROACH"  # 找路口
    TURNING = "TURNING"  # 原地转
    SETTLE = "SETTLE"  # 停住确认线已稳
    DONE = "DONE"
    FAILED = "FAILED"


def _angle_diff(a: float, b: float) -> float:
    return (a - b + math.pi) % (2.0 * math.pi) - math.pi


@dataclass(frozen=True)
class TurnParams:
    """转弯参数（除 direction 外全是占位值，见模块文档）。"""

    direction: TurnDirection
    expect_states: tuple[LineSensorState, ...] = JUNCTION_STATES
    #: 连续几拍路口签名才认定「到路口了」（抗单帧噪声）
    junction_samples: int = 2
    #: 原地转向角速度（rad/s，正=左转/逆时针）
    turn_rate_radps: float = 0.6
    #: 进入转弯前的爬行速度（m/s）
    approach_speed_mps: float = 0.15
    #: 最短转向时间（s）：防止刚见路口就判完成
    min_turn_s: float = 0.20
    #: 转向超时（s）：超过即判失败停车
    max_turn_s: float = 8.0
    #: 找路口的时间上限（s）：一直没出现路口（例如车停在路口前）也要响亮失败
    approach_timeout_s: float = 10.0
    #: 判定「线回到正下方」的横向偏差上限
    center_tolerance: float = 0.35
    #: 稳定拍数（连续多少拍线居中才算转完）
    reacquire_samples: int = 3
    #: 是否额外用航向确认（默认关闭：标定前航向不可信）
    use_yaw_check: bool = False
    #: 航向确认时，至少转过目标角度的这个比例
    yaw_fraction: float = 0.8
    #: 掉头方向：+1 = 向左掉头（逆时针），-1 = 向右掉头
    around_sign: int = 1
    #: 巡线读数连续过期超过这个时长 → 判失败（不许无限等）
    max_stale_s: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.direction, TurnDirection):
            raise ValueError(f"direction must be TurnDirection, got {self.direction!r}")
        if self.direction is TurnDirection.STRAIGHT:
            raise ValueError("STRAIGHT 不需要转弯器（直行通过，不转向）")
        if not self.expect_states:
            raise ValueError("expect_states cannot be empty")
        for state in self.expect_states:
            if not isinstance(state, LineSensorState):
                raise ValueError(f"expect_states must be LineSensorState, got {state!r}")
        if self.junction_samples < 1:
            raise ValueError("junction_samples must be >= 1")
        if self.reacquire_samples < 1:
            raise ValueError("reacquire_samples must be >= 1")
        for name, value in (
            ("turn_rate_radps", self.turn_rate_radps),
            ("approach_speed_mps", self.approach_speed_mps),
            ("min_turn_s", self.min_turn_s),
            ("max_turn_s", self.max_turn_s),
            ("approach_timeout_s", self.approach_timeout_s),
            ("center_tolerance", self.center_tolerance),
            ("max_stale_s", self.max_stale_s),
            ("yaw_fraction", self.yaw_fraction),
        ):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be positive and finite")
        if self.min_turn_s >= self.max_turn_s:
            raise ValueError("min_turn_s must be smaller than max_turn_s")
        if self.approach_speed_mps <= 0.0:
            raise ValueError("approach_speed_mps must be positive")
        if self.around_sign not in (1, -1):
            raise ValueError("around_sign must be +1 or -1")
        if not 0.0 < self.yaw_fraction <= 1.0:
            raise ValueError("yaw_fraction must be in (0, 1]")

    @property
    def target_angle_rad(self) -> float:
        """本次转弯的目标角度（弧度，取正值）。"""
        if self.direction is TurnDirection.AROUND:
            return STRAIGHT_ANGLE_RAD
        return RIGHT_ANGLE_RAD

    @property
    def turn_sign(self) -> float:
        """角速度符号：+1 = 逆时针（左转），-1 = 顺时针（右转）。"""
        if self.direction is TurnDirection.LEFT:
            return 1.0
        if self.direction is TurnDirection.RIGHT:
            return -1.0
        return float(self.around_sign)


def expected_turn_duration_s(params: TurnParams) -> float:
    """按角速度积分估算转弯耗时（现场估超时与 6 分钟预算时用）。"""
    return params.target_angle_rad / params.turn_rate_radps


@dataclass(frozen=True)
class TurnCommand:
    """一次 update 的输出：底盘该怎么动 + 是否结束。"""

    phase: TurnPhase
    vx: float
    wz: float
    done: bool
    failed: bool
    reason: str


class JunctionTurner:
    """路口转弯状态机。调用方每拍喂一次巡线状态，拿到速度命令。"""

    def __init__(self, params: TurnParams) -> None:
        self.params = params
        self.reset()

    # -- 状态 -------------------------------------------------------------
    def reset(self) -> None:
        self.phase = TurnPhase.APPROACH
        self.junction_count = 0
        self.reacquire_count = 0
        self.stale_since: float | None = None
        self.started_at: float | None = None
        self.turn_started_at: float | None = None
        self.yaw_at_turn_start: float | None = None
        self.last_update_at: float | None = None

    @property
    def turn_elapsed_s(self) -> float:
        """已转向时长（诊断/记录用；未开始转向时为 0）。"""
        if self.turn_started_at is None or self.last_update_at is None:
            return 0.0
        return self.last_update_at - self.turn_started_at

    def _failed(self, reason: str) -> TurnCommand:
        self.phase = TurnPhase.FAILED
        return TurnCommand(
            phase=self.phase, vx=0.0, wz=0.0, done=False, failed=True, reason=reason
        )

    # -- 主循环 -----------------------------------------------------------
    def update(
        self,
        *,
        line_state: LineSensorState,
        now: float,
        deviation: float | None = None,
        yaw: float | None = None,
        stale: bool = False,
    ) -> TurnCommand:
        """喂一拍观测，返回底盘命令。

        - `stale=True`（读数过期/无效）：一律停车，且不推进任何计数；
          连续过期超过 `max_stale_s` → FAILED。
        - `deviation` 为 None 时，只用线状态判断「居中」（退化为「回到 ON_LINE」）。
        """
        if not isinstance(line_state, LineSensorState):
            raise ValueError(f"line_state must be LineSensorState, got {line_state!r}")
        if not math.isfinite(now):
            raise ValueError("now must be finite")
        if self.started_at is None:
            self.started_at = now
        self.last_update_at = now

        if self.phase in (TurnPhase.DONE, TurnPhase.FAILED):
            return TurnCommand(
                phase=self.phase, vx=0.0, wz=0.0,
                done=self.phase is TurnPhase.DONE, failed=self.phase is TurnPhase.FAILED,
                reason="转弯已结束（调用方应 reset 后再用）",
            )

        # 读数过期：停车 + 计时；太久则响亮失败
        if stale:
            if self.stale_since is None:
                self.stale_since = now
            if now - self.stale_since > self.params.max_stale_s:
                return self._failed(
                    f"巡线读数连续过期 {now - self.stale_since:.2f}s > {self.params.max_stale_s:.2f}s"
                )
            return TurnCommand(
                phase=self.phase, vx=0.0, wz=0.0, done=False, failed=False,
                reason="巡线读数过期：停车等待",
            )
        self.stale_since = None

        if self.phase is TurnPhase.APPROACH:
            if now - self.started_at > self.params.approach_timeout_s:
                return self._failed(
                    f"找路口超时 {now - self.started_at:.2f}s：路口签名 {self.params.expect_states} 未出现"
                )
            if line_state in self.params.expect_states:
                self.junction_count += 1
                if self.junction_count >= self.params.junction_samples:
                    self.phase = TurnPhase.TURNING
                    self.turn_started_at = now
                    self.yaw_at_turn_start = yaw
                    self.reacquire_count = 0
                    return TurnCommand(
                        phase=self.phase, vx=0.0,
                        wz=self.params.turn_sign * self.params.turn_rate_radps,
                        done=False, failed=False,
                        reason=f"到达路口（{line_state.value} ×{self.junction_count}）：开始原地转",
                    )
                # 还没连续够：保持爬行（不减速，次数够了立刻停）
            else:
                self.junction_count = 0
            return TurnCommand(
                phase=self.phase, vx=self.params.approach_speed_mps, wz=0.0,
                done=False, failed=False, reason="沿当前线爬行，等路口签名",
            )

        # TURNING / SETTLE
        turn_elapsed = now - (self.turn_started_at or now)
        if turn_elapsed > self.params.max_turn_s:
            return self._failed(
                f"转弯超时 {turn_elapsed:.2f}s > {self.params.max_turn_s:.2f}s（线未重新捕获）"
            )

        centered = line_state is LineSensorState.ON_LINE and (
            deviation is None or abs(deviation) <= self.params.center_tolerance
        )
        # 航向基线：优先在刚进入转向时采；若那时还没有航向（IMU 尚未有效/刚上电），
        # 就在转向途中第一次拿到航向时补采。**这会让 Δ 偏小、检查偏向放行**，
        # 所以它只作附加确认——主判据始终是巡线自己（居中 + 稳定拍数）。
        if self.yaw_at_turn_start is None and yaw is not None:
            self.yaw_at_turn_start = yaw
        yaw_ok = True
        if self.params.use_yaw_check and self.params.target_angle_rad > 0.0:
            if yaw is None or self.yaw_at_turn_start is None:
                yaw_ok = False
            else:
                turned = abs(_angle_diff(yaw, self.yaw_at_turn_start))
                yaw_ok = turned >= self.params.target_angle_rad * self.params.yaw_fraction

        if self.phase is TurnPhase.TURNING:
            if not (centered and turn_elapsed >= self.params.min_turn_s and yaw_ok):
                return TurnCommand(
                    phase=self.phase, vx=0.0,
                    wz=self.params.turn_sign * self.params.turn_rate_radps,
                    done=False, failed=False,
                    reason=f"原地转向中（{turn_elapsed:.2f}s / 最短 {self.params.min_turn_s:.2f}s）",
                )
            # 进入确认阶段，并由下面的同一段计数逻辑处理这一拍——
            # 这样「稳定 N 拍」就是**恰好 N 拍**（曾经这里 +1 后又进确认段 +1，
            # 导致 reacquire_samples=1 时实际要求 2 拍，名不符实）。
            self.phase = TurnPhase.SETTLE
            self.reacquire_count = 0

        # SETTLE：停住数拍；线一乱就退回继续转
        if centered:
            self.reacquire_count += 1
            if self.reacquire_count >= self.params.reacquire_samples:
                self.phase = TurnPhase.DONE
                return TurnCommand(
                    phase=self.phase, vx=0.0, wz=0.0, done=True, failed=False,
                    reason=f"线稳定居中 {self.reacquire_count} 拍：转弯完成",
                )
        else:
            self.reacquire_count = 0
            self.phase = TurnPhase.TURNING
            return TurnCommand(
                phase=self.phase, vx=0.0,
                wz=self.params.turn_sign * self.params.turn_rate_radps,
                done=False, failed=False,
                reason="确认阶段线又偏了：继续转向",
            )
        return TurnCommand(
            phase=self.phase, vx=0.0, wz=0.0, done=False, failed=False,
            reason=f"确认线居中（{self.reacquire_count}/{self.params.reacquire_samples}）",
        )


# ---------------------------------------------------------------------------
# 与 B1 路线登记表的对接
# ---------------------------------------------------------------------------

#: 90° 转弯的最短转向时间 = 理论耗时 × 该比例（占位值，现场整定）
MIN_TURN_FRACTION_RIGHT_ANGLE = 0.30
#: 掉头的最短转向时间比例（掉头更容易「转一半就以为到了」）
MIN_TURN_FRACTION_AROUND = 0.60


def turn_params_for_direction(
    direction: TurnDirection,
    *,
    reacquire_samples: int = 3,
    **overrides,
) -> TurnParams:
    """按方向生成一组参数（含由角速度推出的最短转向时间占位值）。"""
    base_rate = float(overrides.pop("turn_rate_radps", 0.6))
    if direction is TurnDirection.AROUND:
        angle = STRAIGHT_ANGLE_RAD
        fraction = MIN_TURN_FRACTION_AROUND
        default_min = max(0.30, angle / base_rate * fraction)
    else:
        angle = RIGHT_ANGLE_RAD
        fraction = MIN_TURN_FRACTION_RIGHT_ANGLE
        default_min = max(0.15, angle / base_rate * fraction)
    min_turn_s = float(overrides.pop("min_turn_s", default_min))
    return TurnParams(
        direction=direction,
        turn_rate_radps=base_rate,
        min_turn_s=min_turn_s,
        reacquire_samples=reacquire_samples,
        **overrides,
    )


def turn_params_for(spec: TurnSpec, **overrides) -> TurnParams:
    """从 B1 路线登记的 `TurnSpec` 生成转弯参数。

    这样「路线说要在这里右转、要略过 1 个直行路口、线要稳 3 拍」与「转弯器怎么转」
    就不会各写一套：改路线登记表，转弯参数跟着变。
    """
    return turn_params_for_direction(
        spec.direction,
        reacquire_samples=spec.settle_samples,
        expect_states=tuple(overrides.pop("expect_states", JUNCTION_STATES)),
        **overrides,
    )
