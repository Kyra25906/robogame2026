"""里程计标定：把「定距试验」变成可下结论的标度（纯逻辑，零 ROS、零硬件）。

## 这个模块回答什么问题

现有「底盘定距测试」是**用里程计闭环停车**：命令走 0.5 m，里程计报 0.5 m 就停。
所以「已前进 = 目标」是自证——**它证明不了里程计准不准**。同一个问题其实有
三个层次，必须分开看：

| 问题 | 怎么答 | 不依赖尺子？ |
|---|---|---|
| ① 命令速度可信吗（开环：速度×时间≈实际位移） | 尺量位移 ÷ (命令速度×时间) | 需要尺量 |
| ② 里程计自洽吗（报的位移与命令时间一致吗） | 里程计等效速度 ÷ 命令速度 | **不需要尺量** |
| ③ 里程计读数要乘多少才等于真实位移 | 尺量位移 ÷ 里程计位移 | 需要尺量 |

②是关键：若里程计读数被放大 10 倍，闭环就会在**实际只走了 1/10** 时停车，
于是耗时变成 1/10、等效速度变成 10 倍——**不用尺子就能看出来**。
所以面板上先看②，再用①③定标度。

## 结论的诚实等级

- 1 组尺量给不出标度（只能看趋势）；
- ≥3 组同距离同速度，且组间相对波动 <3%，才给「可用标度」；
- 组间波动大 → 先查打滑/地面/压线/起步方向，不要急着乘系数；
- 标度接近整数比（如 0.1 / 10）→ 优先查固件轮径或减速比常量，而不是在软件里
  乘一个补丁系数（补丁会掩盖真正的错，换车换固件就失效）。

所有结论都写成**人话 + 下一步**，因为现场看这面板的人需要在 1 分钟内知道
「现在能不能信里程计、下一步量什么」。
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

#: 组间相对波动超过它就认为「不可用于定标度」
MAX_RELATIVE_SPREAD = 0.03
#: 里程计等效速度与命令速度的相对偏差超过它就认为「里程计自身不自洽」
INCONSISTENCY_RATIO = 0.25
#: 开环速度（命令速度×时间 vs 尺量）的相对偏差超过它就认为命令速度不可信
OPEN_LOOP_TOLERANCE = 0.15
#: 至少这么多组尺量才给「可用标度」
MIN_SAMPLES_FOR_SCALE = 3
#: 标度与 1 的偏差小于它 → 认为里程计与尺量一致
SCALE_CLOSE_TO_ONE = 0.03


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数字，收到 {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数值，收到 {value!r}")
    return number


@dataclass(frozen=True)
class OdomTrial:
    """一次定距试验的原始记录（派生量由属性算，不重复存）。"""

    target_m: float  # 里程计闭环目标
    odom_m: float  # 结束时里程计报告的位移
    elapsed_s: float  # 起步到停车的时间
    commanded_speed_mps: float  # 命令速度上限
    measured_m: float | None = None  # 尺量真实位移（人工输入）
    note: str = ""
    completed: bool = True  # 是否跑完整（中途急停/超时的不算样本）

    def __post_init__(self) -> None:
        self_target = _finite(self.target_m, "target_m")
        odom = _finite(self.odom_m, "odom_m")
        elapsed = _finite(self.elapsed_s, "elapsed_s")
        speed = _finite(self.commanded_speed_mps, "commanded_speed_mps")
        if self_target <= 0.0:
            raise ValueError("target_m 必须为正")
        if odom < 0.0:
            raise ValueError("odom_m 不能为负")
        if elapsed <= 0.0:
            raise ValueError("elapsed_s 必须为正")
        if speed <= 0.0:
            raise ValueError("commanded_speed_mps 必须为正")
        if self.measured_m is not None:
            measured = _finite(self.measured_m, "measured_m")
            if measured <= 0.0:
                raise ValueError("尺量位移必须为正（没量就别填）")

    # -- 派生量 -----------------------------------------------------------
    @property
    def open_loop_expected_m(self) -> float:
        """开环预期位移 = 命令速度 × 时间（忽略起步斜坡与末段减速，属量级估计）。"""
        return self.commanded_speed_mps * self.elapsed_s

    @property
    def odom_speed_mps(self) -> float:
        """里程计等效速度：它报告走了多少 ÷ 用了多久。"""
        return self.odom_m / self.elapsed_s

    @property
    def odom_over_command(self) -> float:
        """里程计等效速度 ÷ 命令速度（1.0 = 自洽；10.0 = 里程计被放大 10 倍）。"""
        return self.odom_speed_mps / self.commanded_speed_mps

    @property
    def scale_from_tape(self) -> float | None:
        """尺量标度 = 真实位移 ÷ 里程计位移（≈1 说明里程计准）。"""
        if self.measured_m is None or self.odom_m <= 0.0:
            return None
        return self.measured_m / self.odom_m

    @property
    def open_loop_ratio(self) -> float | None:
        """尺量位移 ÷ 开环预期（≈1 说明「给速度、给时间、车走够了」）。"""
        if self.measured_m is None or self.open_loop_expected_m <= 0.0:
            return None
        return self.measured_m / self.open_loop_expected_m

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_m": round(self.target_m, 4),
            "odom_m": round(self.odom_m, 4),
            "elapsed_s": round(self.elapsed_s, 3),
            "commanded_speed_mps": round(self.commanded_speed_mps, 4),
            "measured_m": None if self.measured_m is None else round(self.measured_m, 4),
            "note": self.note,
            "completed": self.completed,
            "odom_speed_mps": round(self.odom_speed_mps, 4),
            "odom_over_command": round(self.odom_over_command, 3),
            "open_loop_expected_m": round(self.open_loop_expected_m, 4),
            "scale_from_tape": None
            if self.scale_from_tape is None
            else round(self.scale_from_tape, 4),
            "open_loop_ratio": None
            if self.open_loop_ratio is None
            else round(self.open_loop_ratio, 4),
        }


def trial_from_result(
    result: Mapping[str, Any], *, measured_m: float | None = None, note: str = ""
) -> OdomTrial:
    """把网页服务里的定距结果转成一条记录（字段缺失就报人话错误）。"""
    if not isinstance(result, Mapping):
        raise ValueError("定距结果格式不对（不是字典）")
    required = ("target_m", "odom_m", "elapsed_s", "commanded_speed_mps")
    missing = [key for key in required if result.get(key) is None]
    if missing:
        raise ValueError(
            "定距结果缺少字段 " + ", ".join(missing) + "：请先完整跑一次定距测试"
        )
    return OdomTrial(
        target_m=result["target_m"],
        odom_m=result["odom_m"],
        elapsed_s=result["elapsed_s"],
        commanded_speed_mps=result["commanded_speed_mps"],
        measured_m=measured_m,
        note=note,
        completed=bool(result.get("completed", True)),
    )


def _median(values: list[float]) -> float:
    return float(statistics.median(values))


def _spread(values: list[float]) -> tuple[float, float]:
    """返回 (标准差, 相对波动)。样本 <2 时相对波动记 0。"""
    if len(values) < 2:
        return 0.0, 0.0
    stdev = float(statistics.pstdev(values))
    centre = abs(_median(values))
    return stdev, (stdev / centre if centre > 1e-9 else 0.0)


def analyze_trials(trials: Iterable[OdomTrial]) -> dict[str, Any]:
    """把若干条试验记录汇成一个可读结论（含「下一步做什么」）。"""
    records = [trial for trial in trials if isinstance(trial, OdomTrial)]
    completed = [trial for trial in records if trial.completed]
    measured = [trial for trial in completed if trial.measured_m is not None]

    consistency_ratios = [trial.odom_over_command for trial in completed]
    consistency = {
        "ratios": [round(value, 3) for value in consistency_ratios],
        "median": None if not consistency_ratios else round(_median(consistency_ratios), 3),
        "suspicious": False,
    }
    if consistency_ratios:
        consistency["suspicious"] = (
            abs(consistency["median"] - 1.0) > INCONSISTENCY_RATIO
        )

    scale_values = [
        value for value in (trial.scale_from_tape for trial in measured) if value is not None
    ]
    scale: dict[str, Any] | None = None
    if scale_values:
        stdev, relative = _spread(scale_values)
        scale = {
            "n": len(scale_values),
            "values": [round(value, 4) for value in scale_values],
            "mean": round(float(statistics.fmean(scale_values)), 4),
            "median": round(_median(scale_values), 4),
            "stdev": round(stdev, 4),
            "relative_spread": round(relative, 4),
            "usable": (
                len(scale_values) >= MIN_SAMPLES_FOR_SCALE
                and relative <= MAX_RELATIVE_SPREAD
            ),
        }

    open_loop_values = [
        value for value in (trial.open_loop_ratio for trial in measured) if value is not None
    ]
    open_loop: dict[str, Any] | None = None
    if open_loop_values:
        stdev, relative = _spread(open_loop_values)
        open_loop = {
            "n": len(open_loop_values),
            "values": [round(value, 4) for value in open_loop_values],
            "median": round(_median(open_loop_values), 4),
            "stdev": round(stdev, 4),
            "relative_spread": round(relative, 4),
            "trustworthy": abs(_median(open_loop_values) - 1.0) <= OPEN_LOOP_TOLERANCE,
        }

    verdict, next_step = _conclude(completed, measured, consistency, scale, open_loop)
    return {
        "count": len(records),
        "completed_count": len(completed),
        "measured_count": len(measured),
        "trials": [trial.as_dict() for trial in records],
        "consistency": consistency,
        "scale": scale,
        "open_loop": open_loop,
        "verdict": verdict,
        "next_step": next_step,
    }


def _conclude(
    completed: list[OdomTrial],
    measured: list[OdomTrial],
    consistency: dict[str, Any],
    scale: dict[str, Any] | None,
    open_loop: dict[str, Any] | None,
) -> tuple[str, str]:
    if not completed:
        return (
            "还没有跑完整的定距试验。",
            "在「底盘定距测试」里跑一次 0.50 m / 0.05 m·s⁻¹，跑完用尺量实际位移。",
        )
    if consistency["suspicious"]:
        percent = (consistency["median"] - 1.0) * 100.0
        return (
            f"里程计自身不自洽：等效速度是命令速度的 {consistency['median']:.2f} 倍"
            f"（偏差 {percent:+.0f}%）。",
            "先解决这个再谈标度：查固件轮径/减速比常量、编码器丢帧、以及是否打滑；"
            "若倍数是 0.1 或 10 这种整数比，几乎一定是常量写错。",
        )
    if not measured:
        return (
            f"里程计与命令时间自洽（等效速度/命令速度 ≈ {consistency['median']:.2f}），"
            "但还没有尺量数据。",
            "把车对齐起点跑一次，跑完用尺量实际位移填进「尺量距离」并记录。",
        )
    if scale is not None and scale["n"] < MIN_SAMPLES_FOR_SCALE:
        return (
            f"只有 {scale['n']} 组尺量（中位数标度 {scale['median']:.4f}）：样本不足，"
            "还不能下结论。",
            f"同距离同速度再跑 {MIN_SAMPLES_FOR_SCALE - scale['n']} 次（建议共 "
            f"{MIN_SAMPLES_FOR_SCALE} 组），再看波动。",
        )
    if scale is not None and not scale["usable"]:
        return (
            f"组间波动 {scale['relative_spread'] * 100:.1f}%（上限 "
            f"{MAX_RELATIVE_SPREAD * 100:.0f}%）：数据还不能用来定标度。",
            "先查打滑、地面（黑线/接缝）、轮子压线、起步方向与末段减速是否被截断，"
            "再重跑同一组参数。",
        )
    if scale is None:
        return ("数据不完整，无法给出标度。", "重跑一次完整的定距试验。")

    median = scale["median"]
    base = (
        f"尺量标度 {median:.4f}（n={scale['n']}，波动 {scale['relative_spread'] * 100:.1f}%）"
        f"，里程计等效速度/命令速度 ≈ {consistency['median']:.2f}。"
    )
    if open_loop is not None and not open_loop["trustworthy"]:
        base += (
            f" 另注：开环「命令速度×时间」与实际位移中位数比 {open_loop['median']:.3f}"
            "（偏差超 15%）。"
        )
    if abs(median - 1.0) <= SCALE_CLOSE_TO_ONE:
        return (
            base + " 里程计与尺量一致（±3%）。",
            "可以按里程计做距离判据（路线里 estimated 的那些段可以升为 measured）。",
        )
    integer_hint = ""
    for candidate, label in ((0.1, "1/10"), (0.01, "1/100"), (10.0, "10 倍"), (100.0, "100 倍")):
        if abs(median - candidate) / candidate < 0.1:
            integer_hint = (
                f" 标度接近 {label} 这种整数比，优先查固件轮径或减速比常量，"
                "而不是在软件里乘补丁系数。"
            )
            break
    return (
        base + f" 里程计读数需乘以 {median:.4f} 才等于真实位移。",
        "先在固件侧按这个比例核对轮径/减速比；若固件暂时改不了，再考虑在 "
        "robot_bridge 的里程计换算处乘这个系数（并注明它是补丁，换固件要复核）。"
        + integer_hint,
    )
