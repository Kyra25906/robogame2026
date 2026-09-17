"""机械臂（0x20/0x21）联调自检：把“该看什么、期望什么”写成可重复执行的检查。

## 为什么单独放一个模块

联调阶段最怕两件事：**漏掉一步**和**结论记不清**。所以把每一级验收写成
“名称 / 期望 / 实际 / 通过与否 / 备注”五元组，由网页一键跑完并排成表格，
而不是靠人在四五个终端里对着肉眼看。

## 依赖注入

`ArmSelftest` 不 import ROS：`ros`、`safety`、`mode`、`wait_flag`、`claim_busy`
全部由调用方传入。因此这个模块可以在没有 ROS 的机器上完整单测
（见 `tests/test_arm_selftest.py`），也可以在树莓派上接真正的服务客户端。

## 五个阶段（网页上就是五个按钮）

| stage | 名称 | 会不会动 |
|---|---|---|
| `link` | 链路与授权 | 不动 |
| `arm_path` | ARM_SET 通路自检（下发“当前安全姿态角度”，零位移） | 理论上不动 |
| `gripper` | 夹爪闭环（GRAB → RELEASE） | 爪开合 |
| `timeout` | 超时错误码验证 | 腕约 5° |
| `boundaries` | 停止与边界（无升降 3010） | 不动 |

## 证据边界

网页上的 PASS **不等于**“机械臂真的按预期动了”：机械臂纯开环无位置反馈（C-1），
服务成功只代表命令被接受并走完了流程。位移、方向、幅度必须由现场目视确认，
第 2、4 阶段的结果里都带了这句提醒。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

try:
    from robogame_core.arm import arm_zero_displacement_targets
except ImportError:  # 未安装 robogame_core 时退化为“只跑不需要参考角度的阶段”
    arm_zero_displacement_targets = None


STATUS_STALE_S = 0.30
GRIPPER_WAIT_S = 5.0
ZERO_DISPLACEMENT_TIMEOUT_S = 8.0
TIMEOUT_STAGE_BUDGET_S = 0.3
NO_LIFT_ERROR_CODE = 3010
STALE_ERROR_CODE = 3020
NOT_FROZEN_ERROR_CODE = 9010

STAGES: tuple[tuple[str, str], ...] = (
    ("link", "链路与授权（不动机构）"),
    ("arm_path", "ARM_SET 通路自检（零位移）"),
    ("gripper", "夹爪闭环 GRAB/RELEASE（爪会动）"),
    ("timeout", "超时错误码 3020（腕约 5°）"),
    ("boundaries", "停止与边界（无升降 3010）"),
)

JOINT_NAMES = {0: "腰/云盘", 1: "肩", 2: "肘", 3: "腕", 4: "爪"}


def step(name: str, expect: str, actual: str, ok: bool, note: str = "") -> dict[str, Any]:
    """一条自检结果的统一形状，供网页表格直接渲染。"""
    return {"name": name, "expect": expect, "actual": actual, "ok": bool(ok), "note": note}


def stage_names() -> list[dict[str, str]]:
    return [{"stage": key, "label": label} for key, label in STAGES]


class ArmSelftest:
    """按阶段执行机械臂联调自检。

    注入接口（都由调用方提供，便于替换与测试）：

    - `ros`：`.mechanism(action, timeout_s, height_m=None)`、
      `.arm_set_joint(joint, angle_deg, timeout_s)`、`.service_status()`
    - `safety()`：返回带 `received_at / communication_ok / physical_start /
      emergency_stop / mechanism_fault / gripper_closed` 的状态对象
    - `mode()`：返回底盘模式字符串（非 `OBSERVE` 时不允许下发机构动作，C-4）
    - `wait_flag(attr, want, timeout_s)`：异步等待某个安全标志变成期望值，
      返回 `(ok, 实际值字符串)`
    - `claim_busy()` / `release_busy()`：机构动作互斥；`claim_busy` 返回 `False`
      表示已有动作在跑
    - `now()`：单调时钟
    """

    def __init__(
        self,
        *,
        ros: Any,
        safety: Callable[[], Any],
        mode: Callable[[], str],
        wait_flag: Callable[[str, bool, float], Awaitable[tuple[bool, str]]],
        claim_busy: Callable[[], bool],
        release_busy: Callable[[], None],
        now: Callable[[], float],
    ) -> None:
        self.ros = ros
        self._safety = safety
        self._mode = mode
        self._wait_flag = wait_flag
        self._claim_busy = claim_busy
        self._release_busy = release_busy
        self._now = now

    # -- 公共前置条件 ------------------------------------------------------

    def _precheck(self) -> list[dict[str, Any]]:
        """状态新鲜 + 已授权 + 无急停/机构故障 + 底盘不在运动。

        任何一条不满足都直接返回，后续动作不再执行——不能带着未知状态去动机械臂。
        """
        status = self._safety()
        age = None if status.received_at is None else self._now() - status.received_at
        steps = [
            step(
                "收到 /robot/status",
                "有状态且 ≤ 0.30 s",
                "从未收到" if age is None else f"{age:.3f} s",
                age is not None and age <= STATUS_STALE_S,
            ),
            step("STM32 通信", "communication_ok = true", str(bool(status.communication_ok)),
                 bool(status.communication_ok)),
            step("物理授权（长按 PB2）", "physical_start = true", str(bool(status.physical_start)),
                 bool(status.physical_start)),
            step("急停", "emergency_stop = false", str(bool(status.emergency_stop)),
                 not bool(status.emergency_stop)),
            step("机构故障", "mechanism_fault = false", str(bool(status.mechanism_fault)),
                 not bool(status.mechanism_fault)),
        ]
        current_mode = self._mode()
        steps.append(
            step("底盘不在运动", "mode = OBSERVE", current_mode, current_mode == "OBSERVE",
                 "C-4：车走的时候不要动机构。请先停止底盘或退出接管")
        )
        # ROS 未就绪时不要抛异常，而是给出一张"全是 FAIL 的表"，
        # 让页面能把原因显示出来，而不是一个 HTTP 500。
        steps.append(
            step("ROS 客户端", "已连接 ROS 2",
                 "未就绪" if self.ros is None else "已就绪", self.ros is not None,
                 "检查 bridge 是否已启动：/robot/status 与机构服务都依赖它")
        )
        return steps

    @staticmethod
    def _blocked(steps: list[dict[str, Any]]) -> bool:
        return any(not item["ok"] for item in steps)

    # -- 各阶段 ------------------------------------------------------------

    async def _stage_link(self) -> list[dict[str, Any]]:
        steps = self._precheck()
        services = self.ros.service_status() if self.ros is not None else {}
        wanted = ("grab", "release", "stop", "arm_set_joint")
        missing = [name for name in wanted if not services.get(name)]
        steps.append(
            step(
                "机构服务就绪",
                "grab / release / stop / arm_set_joint 全部可用",
                ("缺失: " + ", ".join(missing)) if missing else "全部可用",
                not missing,
                "arm_set_joint 缺失通常是 robogame_interfaces 新增 SetArmJoint.srv 后"
                "没重新 colcon build",
            )
        )
        steps.append(
            step(
                "爪闭合标志",
                "只作参考：这是固件按命令推算的值",
                str(bool(self._safety().gripper_closed)),
                True,
                "本车没有方块传感器，cube_present 恒为 0；抓取验证只能 service_only",
            )
        )
        return steps

    async def _stage_arm_path(self) -> list[dict[str, Any]]:
        if arm_zero_displacement_targets is None:
            return [step("robogame_core.arm 可用", "能算出零位移参考角度", "未安装", False)]
        steps = self._precheck()
        if self._blocked(steps):
            return steps
        if not self._claim_busy():
            return [step("机构空闲", "没有其他机构动作在跑", "忙碌", False)]
        try:
            for target in arm_zero_displacement_targets():
                name = JOINT_NAMES.get(int(target.joint), str(int(target.joint)))
                result = self.ros.arm_set_joint(
                    int(target.joint), float(target.angle_deg), ZERO_DISPLACEMENT_TIMEOUT_S
                )
                code = int(result.get("error_code") or 0)
                note = str(result.get("detail", ""))
                if code == NOT_FROZEN_ERROR_CODE:
                    note = (
                        "关节值域未冻结：请在硬件参数里填 arm_joint_ranges + "
                        "arm_joint_ranges_evidence（参见 robot_bridge/README.md §8）"
                    )
                elif code == STALE_ERROR_CODE:
                    note = "命令没在预算内到位；零位移指令本该很快完成，请检查固件与链路"
                steps.append(
                    step(
                        f"ARM_SET {name} {target.angle_deg:g}°",
                        "success = true（零位移，不应有可见动作）",
                        f"success={result.get('success')} code={code}",
                        bool(result.get("success")),
                        note,
                    )
                )
        finally:
            self._release_busy()
        return steps

    async def _stage_gripper(self) -> list[dict[str, Any]]:
        steps = self._precheck()
        if self._blocked(steps):
            return steps
        if not self._claim_busy():
            return [step("机构空闲", "没有其他机构动作在跑", "忙碌", False)]
        try:
            steps.append(step("夹爪空载", "爪里不能有方块，且手放在遥控上", "由操作员确认", True))

            grab = self.ros.mechanism("grab", GRIPPER_WAIT_S)
            steps.append(
                step("GRAB 下发", "success = true",
                     f"success={grab.get('success')} code={grab.get('error_code')}",
                     bool(grab.get("success")), str(grab.get("detail", "")))
            )
            if grab.get("success"):
                ok, actual = await self._wait_flag("gripper_closed", True, GRIPPER_WAIT_S)
                steps.append(
                    step("GRAB 后 gripper_closed", "≤ 5 s 内变 true", actual, ok,
                         "固件按命令推算的标志，不是爪的位置传感器")
                )

            release = self.ros.mechanism("release", GRIPPER_WAIT_S)
            steps.append(
                step("RELEASE 下发", "success = true",
                     f"success={release.get('success')} code={release.get('error_code')}",
                     bool(release.get("success")), str(release.get("detail", "")))
            )
            if release.get("success"):
                ok, actual = await self._wait_flag("gripper_closed", False, GRIPPER_WAIT_S)
                steps.append(step("RELEASE 后 gripper_closed", "≤ 5 s 内变 false", actual, ok))
        finally:
            self._release_busy()
        return steps

    async def _stage_timeout(self) -> list[dict[str, Any]]:
        steps = self._precheck()
        if self._blocked(steps):
            return steps
        if not self._claim_busy():
            return [step("机构空闲", "没有其他机构动作在跑", "忙碌", False)]
        try:
            # 腕从安全姿态 683µs 走到 270°（2500µs）需要约 5.4 s；只给 0.3 s 必然超时。
            result = self.ros.arm_set_joint(3, 270.0, TIMEOUT_STAGE_BUDGET_S)
            code = int(result.get("error_code") or 0)
            steps.append(
                step(
                    "故意给不足的超时 → 期望 3020",
                    "error_code = 3020（自动动作超时）",
                    f"success={result.get('success')} code={code}",
                    code == STALE_ERROR_CODE,
                    "腕会有约 5° 的可见动作；3020 表示命令没在预算内到位，"
                    "不是机构故障，不需要按 PB2 重新授权",
                )
            )
        finally:
            self._release_busy()
        return steps

    async def _stage_boundaries(self) -> list[dict[str, Any]]:
        steps = self._precheck()
        if self._blocked(steps):
            return steps

        stop = self.ros.mechanism("stop", 1.0)
        steps.append(
            step("机构停止 /mechanism/stop", "success = true",
                 f"success={stop.get('success')} code={stop.get('error_code')}",
                 bool(stop.get("success")))
        )

        # 抬高到不需要机构空闲的状态：升降在真车本就不存在。
        lift = self.ros.mechanism("lift", 3.0, height_m=0.1)
        code = int(lift.get("error_code") or 0)
        steps.append(
            step(
                "升降 /lift/set_height 0.1 m",
                "error_code = 3010（真车没有升降装置）",
                f"success={lift.get('success')} code={code}",
                code == NO_LIFT_ERROR_CODE,
                "这是预期行为，不是缺陷：机械组 2026-08-19 确认 C-3 真车无升降装置。"
                "若真车模式要放置，任务必须是单层地面放置",
            )
        )
        return steps

    # -- 入口 --------------------------------------------------------------

    async def run(self, stage: str) -> dict[str, Any]:
        runners = {
            "link": self._stage_link,
            "arm_path": self._stage_arm_path,
            "gripper": self._stage_gripper,
            "timeout": self._stage_timeout,
            "boundaries": self._stage_boundaries,
        }
        runner = runners.get(stage)
        if runner is None:
            raise ValueError(
                f"未知自检阶段 {stage!r}；可用: {', '.join(runners)}"
            )
        steps = await runner()
        return {
            "stage": stage,
            "steps": steps,
            "passed": bool(steps) and all(item["ok"] for item in steps),
        }
