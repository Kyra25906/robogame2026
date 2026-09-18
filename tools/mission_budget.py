"""比赛时间预算敏感性分析：6 分钟够跑几趟？（用离线预演算，不是拍脑袋）

## 这个工具回答什么

「6 分钟里跑一趟还是两趟」「取件慢一点会不会跑不完」这类问题，靠猜没意义。
用 `tools/mission_sim.py` 的虚拟场地按不同参数扫一遍，就能给出**带前提的数字**：

- 巡线速度（0.10 / 0.15 / 0.20 / 0.25 m/s）×
- 单次抓取/放置耗时（现场时间预算：抓典型 7.1 s、最坏 14.3 s；放典型 6.55 s、最坏 12.5 s）

输出的每一格是「一趟的虚拟耗时」与「剩余时间」，以及**最坏情况下会不会撞上 6 分钟时钟**。

## 诚实边界（必须跟着数字一起说）

- 虚拟场地**不是物理仿真**：无动力学、无打滑、无噪声、无通信延迟，巡线是理想跟线
  （带横向纠偏）。所以这些数字是**下界性质**的乐观估计：真车只会更慢。
- 抓/放耗时是**输入假设**，不是实测；现场实测后应当替换再跑一次。
- 结论只能用于「值不值得开第二趟」这类决策，不能用于宣称真车能跑完。

现场实测落地后，把这些数字填进 `docs/field/TIME_BUDGET.csv`，并重跑本工具。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from mission_sim import SimConfig, simulate_route

#: 现场时间预算里的三档（docs/field/TIME_BUDGET.csv）
PICK_PLACE_PRESETS = {
    "typical": (7.1, 6.55),
    "conservative": (10.0, 9.0),
    "worst": (14.3, 12.5),
}
#: 速度倍率档（乘在路线登记表的分段限速上；1.0 = 按计划限速）
SPEED_SCALES = (0.5, 0.75, 1.0, 1.5)
#: 比赛总时长（规则 3.2.1）
MATCH_LIMIT_S = 360.0


@dataclass(frozen=True)
class BudgetCase:
    speed_scale: float
    preset: str
    pick_s: float
    place_s: float
    completed: bool
    virtual_time_s: float
    remaining_s: float
    rounds_that_fit: int
    final_state: str

    @property
    def fits(self) -> bool:
        return self.completed and self.remaining_s >= 0.0


def measure(*, speed_scale: float, preset: str, match_limit_s: float = MATCH_LIMIT_S):
    """跑一次预演，返回 (结果, 抓取耗时, 放置耗时)。"""
    from robogame_core.mission import MissionConfig

    pick_s, place_s = PICK_PLACE_PRESETS[preset]
    result = simulate_route(
        sim_config=SimConfig(pick_s=pick_s, place_s=place_s, speed_scale=speed_scale),
        mission_config=MissionConfig(match_time_limit_s=match_limit_s),
    )
    return result, pick_s, place_s


def sweep(*, match_limit_s: float = MATCH_LIMIT_S) -> list[BudgetCase]:
    """把「速度倍率 × 抓放耗时」扫一遍。"""
    cases: list[BudgetCase] = []
    for scale in SPEED_SCALES:
        for preset in PICK_PLACE_PRESETS:
            result, pick_s, place_s = measure(
                speed_scale=scale, preset=preset, match_limit_s=match_limit_s
            )
            remaining = match_limit_s - result.virtual_time_s
            cases.append(BudgetCase(
                speed_scale=scale,
                preset=preset,
                pick_s=pick_s,
                place_s=place_s,
                completed=result.completed,
                virtual_time_s=result.virtual_time_s,
                remaining_s=remaining,
                rounds_that_fit=max(0, int(match_limit_s // max(result.virtual_time_s, 1.0))),
                final_state=result.final_state,
            ))
    return cases


def format_table(cases: list[BudgetCase], *, match_limit_s: float = MATCH_LIMIT_S) -> str:
    lines = [
        f"比赛时间预算（虚拟预演，6 分钟 = {match_limit_s:.0f} s）",
        "⚠️ 虚拟场地无动力学/打滑/噪声，是乐观下界；抓放耗时是输入假设而非实测。",
        "   速度倍率 = 乘在路线登记表分段限速上的系数（1.00 = 按计划限速）。",
        "",
        f"{'倍率':>6} {'抓/放预设':>12} {'一趟耗时':>9} {'剩余':>8} {'够跑':>5} {'结果':>18}",
    ]
    for case in cases:
        lines.append(
            f"{case.speed_scale:>6.2f} {case.preset:>12} "
            f"{case.virtual_time_s:>8.1f}s {case.remaining_s:>7.1f}s "
            f"{case.rounds_that_fit:>5} "
            f"{'完成' if case.completed else '未完成':>8}（{case.final_state}）"
        )
    worst = [case for case in cases if case.preset == "worst"]
    tight = [case for case in worst if not case.fits]
    lines.append("")
    if tight:
        lines.append(
            "⚠️ 最坏抓放耗时下跑不完的倍率："
            + "，".join(f"{case.speed_scale:.2f}×" for case in tight)
            + "（说明现场必须压缩取件耗时，否则要么减块数、要么只能跑一趟且赶）"
        )
    else:
        lines.append("✓ 即使按最坏抓放耗时，所有倍率档都能跑完一趟。")
    fits_two = [case for case in cases if case.rounds_that_fit >= 2]
    if fits_two:
        best = min(fits_two, key=lambda case: case.virtual_time_s)
        lines.append(
            f"两趟的乐观上界：{best.speed_scale:.2f}× + {best.preset} 预设"
            f"（一趟 {best.virtual_time_s:.0f} s，才放得下第二趟）"
        )
    else:
        lines.append("预演里两趟都放不下——多趟循环暂时没有意义。")
    lines.append(
        "提醒：多趟还卡在两条现场/机械结论（第二座建筑落点、机构层计数复位），见工作留痕 R9。"
    )
    return "\n".join(lines)


def _force_utf8_stdout() -> None:
    """让中文/符号输出不因为控制台编码而崩掉。

    真实踩过：表格里有一个 `⚠️`，Windows 默认 GBK 控制台直接
    `UnicodeEncodeError` 退出——现场照着文档跑这条命令的人只会看到一堆栈，
    而**明明算出来的结论就在表格里**。改成 UTF-8 + `errors="replace"`：
    最坏情况是某个符号变成 `?`，但数字与结论一定打得出来。
    """
    stream = getattr(sys, "stdout", None)
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 极端环境（管道已关闭等）
            pass


def main() -> int:  # pragma: no cover - 现场手工运行
    _force_utf8_stdout()
    print(format_table(sweep()))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
