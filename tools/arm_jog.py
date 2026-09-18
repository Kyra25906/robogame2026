"""网页关节微调（jog）：像遥控器那样"按住就动一小步"，但每一步都是绝对角度。

## 为什么需要它

固件里只有**一个**位置变量 `arm_pulse_us[]`（`arm.c:158`），遥控和树莓派都改它：

- 遥控：`arm_pulse_us[肩] += delta`（速率控制，按住一直转，松手保持）；
- 树莓派：0x20 `ARM_SET` → `arm_auto_target_us[]` → 每轮推进到目标（绝对角度）。

所以"让特定关节动"树莓派**本来就能**，而且能直接说角度（遥控只能说"继续转"）。
缺的只是一个顺手的操作面：按住小步走、松手就停、每一步留下角度与结果。
本模块就是那个操作面的**纯逻辑**（不碰 ROS、不碰硬件，可离线单测）。

## 为什么是"绝对角度累加"，而不是"相对转 2°"

0x20 的 `ARM_SET` 只有一种语义：`parameter = joint * 1000 + 绝对角度`，而且**整度**
（没有小数位）。协议里没有"相对转 2°"这条命令。所以 jog 必须在**上位机**维护
"我相信它现在在哪"这个数——而这个数**不是测量值**：

> 机械臂没有位置传感器（`arm.h:115-117` 原话："本模块无法证明舵机真的转到了目标角度"），
> 0x21 状态帧也没有位置/脉宽字段。页面显示的"当前角度"是**我们发出去的目标**累加值。

这一点必须写在页面上，否则操作员会以为它是读回来的。

## 安全模型（与网页手动驾驶同一套思路）

- **松手/断线即停**：浏览器持续发心跳，超过 `JOG_HEARTBEAT_TIMEOUT_S` 没有心跳 → 停；
- **安全门**：急停 / 通信不可用 / 机构故障 / 状态过期，任何一条成立 → 停（由调用方传入）；
- **到边界即停**：不越界、不"顶"在极限上，也不做半截步；
- **一次一步**：每一步都等固件的完成回执（`SUCCEEDED`/`FAILED`），失败就停并说明原因。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

#: 允许微调的关节（**不含爪子**）：爪子按 C-2 / C-9 走 GRAB / RELEASE 开关命令，
#: 走 ARM_SET 会被拒（错误码 9012）。
JOG_JOINTS: tuple[int, ...] = (0, 1, 2, 3)

JOG_JOINT_LABELS: dict[int, str] = {
    0: "腰/云盘",
    1: "肩",
    2: "肘",
    3: "腕",
    4: "爪（不走 ARM_SET）",
}

#: 已知的安全姿态角度（由固件 `arm.c` 的 `ARM_SAFE_PULSE_US` 按同一线性映射换算）。
#: ⚠️ 这是**固件常量的换算值**，不是车上实测的角度；页面拿它当"起点角度"的默认值，
#: 因为上电授权后固件把臂放在这个姿态上（`arm.c:774`）。
JOG_SAFE_POSE_DEG: dict[int, int] = {0: 131, 1: 158, 2: 135, 3: 25}

#: 与固件 `arm.c` 的 `ARM_JOINT_ANGLE_RANGE_DEG` 一致：脉宽下限 ↔ 0°，上限 ↔ 270°。
JOG_LIMIT_DEG: tuple[int, int] = (0, 270)

#: 单步步长（度）。必须是整数：`parameter` 里没有小数位。
JOG_MIN_STEP_DEG = 1
JOG_MAX_STEP_DEG = 10

#: 两步之间的间隔（秒）。注意：整步耗时 = 服务往返 + 固件走过去的时间（约 45°/s）+ 本间隔。
JOG_MIN_PERIOD_S = 0.10
JOG_MAX_PERIOD_S = 1.00

#: 心跳超时：超过这么久没收到浏览器心跳就停（松手、关窗口、断线都会走到这里）。
JOG_HEARTBEAT_TIMEOUT_S = 0.60

#: 单步服务超时。固件按约 45°/s 走，10° 约 0.22s；留足余量，别让慢动作被误判成失败。
JOG_SERVICE_TIMEOUT_S = 8.0

JOG_REASON_HEARTBEAT = "网页心跳中断（松手、切走窗口或断线），已停止微调"
JOG_REASON_BOUNDARY = "已到角度边界，微调结束"


def joint_label(joint: int) -> str:
    return JOG_JOINT_LABELS.get(int(joint), f"未知关节 {joint}")


def validate_jog_request(
    *,
    joint: Any,
    direction: Any,
    step_deg: Any,
    period_s: Any,
    start_deg: Any,
    low_deg: int = JOG_LIMIT_DEG[0],
    high_deg: int = JOG_LIMIT_DEG[1],
) -> "JogRequest":
    """校验一次微调请求；不合法就抛 ``ValueError``，消息直接给人看。

    拒绝而不是夹取，理由同 `robogame_core.arm`：静默把 20° 的步长改成 10°，
    会让"你以为发出去的东西"和"实际发出去的东西"不一致。
    """
    if isinstance(joint, bool) or not isinstance(joint, int):
        raise ValueError("关节编号必须是整数")
    if joint not in JOG_JOINTS:
        if joint == 4:
            raise ValueError(
                "爪子不走 ARM_SET：抓放请用「机械臂」卡片的夹取/释放按钮"
            )
        allowed = "、".join(f"{item}={joint_label(item)}" for item in JOG_JOINTS)
        raise ValueError(f"关节编号必须是 {allowed}")

    if isinstance(direction, bool) or not isinstance(direction, int) or direction not in (-1, 1):
        raise ValueError("方向必须是 +1（角度增大）或 -1（角度减小）")

    if isinstance(step_deg, bool) or not isinstance(step_deg, (int, float)):
        raise ValueError("步长必须是数字")
    if not math.isfinite(float(step_deg)) or float(step_deg) != int(step_deg):
        raise ValueError("步长必须是整度：0x20 的 parameter 里没有小数位")
    step = int(step_deg)
    if not JOG_MIN_STEP_DEG <= step <= JOG_MAX_STEP_DEG:
        raise ValueError(f"步长必须在 {JOG_MIN_STEP_DEG}～{JOG_MAX_STEP_DEG} 度之间")

    if isinstance(period_s, bool) or not isinstance(period_s, (int, float)):
        raise ValueError("间隔必须是数字")
    period = float(period_s)
    if not math.isfinite(period) or not JOG_MIN_PERIOD_S <= period <= JOG_MAX_PERIOD_S:
        raise ValueError(f"间隔必须在 {JOG_MIN_PERIOD_S:g}～{JOG_MAX_PERIOD_S:g} 秒之间")

    if isinstance(start_deg, bool) or not isinstance(start_deg, (int, float)):
        raise ValueError("起点角度必须是数字")
    if not math.isfinite(float(start_deg)) or float(start_deg) != int(start_deg):
        raise ValueError("起点角度必须是整度")
    start = int(start_deg)
    if not low_deg <= start <= high_deg:
        raise ValueError(
            f"起点角度必须在 {low_deg}～{high_deg} 度之间（超出这个范围的命令会被固件拒绝）"
        )

    return JogRequest(
        joint=joint, direction=direction, step_deg=step, period_s=period,
        start_deg=start, low_deg=int(low_deg), high_deg=int(high_deg),
    )


@dataclass(frozen=True)
class JogRequest:
    """一次微调的参数（已校验）。"""

    joint: int
    direction: int
    step_deg: int
    period_s: float
    start_deg: int
    low_deg: int = JOG_LIMIT_DEG[0]
    high_deg: int = JOG_LIMIT_DEG[1]

    @property
    def label(self) -> str:
        arrow = "角度增大" if self.direction > 0 else "角度减小"
        return (
            f"{joint_label(self.joint)}（joint={self.joint}）{arrow} "
            f"每步 {self.step_deg}°，间隔 {self.period_s:g}s，起点 {self.start_deg}°"
        )


@dataclass
class JogSession:
    """一次正在进行的微调：记住"我们发到哪儿了"，并在每一步留下结果。"""

    request: JogRequest
    token: str
    started_at: float
    heartbeat_at: float
    last_target_deg: int
    steps_done: int = 0
    last_result: dict[str, Any] | None = None
    stop_reason: str | None = None
    finished_at: float | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.stop_reason is None

    def next_target_deg(self) -> int | None:
        """下一步的目标角度；到边界返回 ``None``（不做半截步）。"""
        candidate = self.last_target_deg + self.request.direction * self.request.step_deg
        if candidate < self.request.low_deg or candidate > self.request.high_deg:
            return None
        if candidate == self.last_target_deg:
            return None
        return candidate

    def note_step(self, angle_deg: int, result: dict[str, Any]) -> None:
        """记录一步的结果。**只有成功后**才推进"我们相信它现在在哪"。"""
        self.last_result = dict(result)
        step = {
            "index": self.steps_done + 1,
            "angle_deg": int(angle_deg),
            "success": bool(result.get("success")),
            "error_code": int(result.get("error_code", 0)),
            "detail": str(result.get("detail", "")),
        }
        self.steps.append(step)
        self.steps_done += 1
        if step["success"]:
            self.last_target_deg = int(angle_deg)

    def as_dict(self, now: float) -> dict[str, Any]:
        return {
            "active": self.active,
            "token": self.token,
            "joint": self.request.joint,
            "joint_label": joint_label(self.request.joint),
            "direction": self.request.direction,
            "step_deg": self.request.step_deg,
            "period_s": self.request.period_s,
            "start_deg": self.request.start_deg,
            "low_deg": self.request.low_deg,
            "high_deg": self.request.high_deg,
            "last_target_deg": self.last_target_deg,
            "steps_done": self.steps_done,
            "last_result": self.last_result,
            "stop_reason": self.stop_reason,
            "heartbeat_age_s": round(max(0.0, now - self.heartbeat_at), 3),
            "elapsed_s": round(max(0.0, (self.finished_at or now) - self.started_at), 2),
            "steps": self.steps[-20:],
        }


def jog_stop_reason(
    session: JogSession,
    now: float,
    *,
    blockers: Sequence[Any] = (),
    heartbeat_timeout_s: float = JOG_HEARTBEAT_TIMEOUT_S,
) -> str | None:
    """每一步之前问一次"还能不能继续"；返回 ``None`` 表示可以继续。

    `blockers` 是 `field_dashboard_core.action_blockers` 的输出（急停/通信/状态过期/
    机构故障/物理授权）。它们与手动驾驶用的是**同一套**判据——不另立一套，
    否则"为什么手动能开、微调不能动"会变成现场查不出来的谜。
    """
    if session.stop_reason is not None:
        return session.stop_reason
    if blockers:
        first = blockers[0]
        message = first.get("message") if isinstance(first, dict) else str(first)
        evidence = first.get("evidence") if isinstance(first, dict) else ""
        return f"安全门拦截：{message}（{evidence}）" if evidence else f"安全门拦截：{message}"
    if now - session.heartbeat_at > heartbeat_timeout_s:
        return JOG_REASON_HEARTBEAT
    return None
