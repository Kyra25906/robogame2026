"""树莓派侧机械臂关节模型：ARM_SET 的下发资格、值域校验与 parameter 编解码。

## 这个模块解决什么

树莓派要说“把云盘转到 90°”，就必须把这句话变成一个整数塞进 0x20 的
`parameter` 字段。本模块负责这层翻译，以及翻译之前的**下发资格检查**。

## 与 STM32 固件保持一致（本模块的编号和量程都从固件源码抄来）

关节编号**直接复用固件 `Core/Inc/arm.h` 的 `Arm_Joint` 枚举**，两侧零映射：

| 编号 | arm.h 名称 | 固件注释 | 脉宽范围（arm.c） | 总行程（arm.c） |
|---|---|---|---|---|
| 0 | `ARM_JOINT_BASE` | 腰（基座）20KG = 机械组的“云盘” | 500–2500 µs | 270° |
| 1 | `ARM_JOINT_SHOULDER` | 肩（臂底）80KG | 500–**1500** µs | 270°（待实测，可能 180°） |
| 2 | `ARM_JOINT_ELBOW` | 肘（新增）20KG，TIM7 第 5 路 | 500–2500 µs | 270° |
| 3 | `ARM_JOINT_WRIST` | 腕 20KG | 500–2500 µs | 270° |
| 4 | `ARM_JOINT_GRIPPER` | 爪 20KG | 1200–1540 µs | 270°（仅用于速率换算） |

`tests/test_arm_firmware_sync.py` 会就地解析 `arm.h` / `arm.c` 的表格，断言上面
这些数字与固件源码逐项相等——固件改了而树莓派没跟，测试就红。

## 事实来源（只写已确认的，未确认的一律保持“未冻结”）

- `docs/field/给机械组现场问答表_2026-08-19.md` C-9：自由度为底部云盘 + 肩 + 腕
  （3 DOF），计划加肘。旧文档 08-18 的“大臂/小臂/爪子”关节表**已作废**。
  注意固件侧肘已经存在（`ARM_JOINT_ELBOW`，TIM7 第 5 路）。
- 同上 C-10：云盘机械上可转 360°，值域按 360° 设计；软件只允许在“右侧↔车尾”
  约 90° 工作弧内下发，越界必须拒绝。
- 同上 C-11：肩 / 肘 / 腕的角度范围与“角度→脉宽”映射**尚未冻结**。
- 同上 C-2 / C-9：爪子**不走 ARM_SET**，抓放仍用 GRAB / RELEASE 开关命令。
- 同上 C-1 / C-3：机械臂纯开环无位置反馈（完成判据只能是“时间到/超时”）；
  真车没有升降装置。因此本模块**不做高度→关节角度的反解**，也不做到位判断。
- `MECHANISM_0x20_INTERFACE_ALIGNMENT_2026-08-18.md`：parameter 编码锁定为
  `joint_id * 1000 + angle_deg`。

## 设计要点

1. **未确认的值不写死。** `ArmJointSpec` 只承载已确认的范围，未确认就是
   `None`；默认表 `UNFROZEN_ARM_JOINT_TABLE` 里没有任何关节被冻结，于是
   **默认状态下任何 ARM_SET 都会被拒绝**，且拒绝理由直接指向缺哪个确认。
2. **拒绝，不夹取。** 越界目标直接抛异常，绝不静默 clamp 成另一个姿态——
   静默夹取会让“发出去的姿态”和“以为发出去的姿态”不一致。
3. **爪子不进 ARM_SET。** 编号 4 在固件里存在（它是舵机通道），但树莓派策略
   不允许对它下发角度，`validate_arm_target` 会明确拒绝并说明原因。
4. **脉宽不是位置。** `arm_pulse_us_for_angle` 只是“软件输出指令的预测值”，
   机械臂无位置反馈（C-1），它不能证明舵机真的到位。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from math import isfinite
from typing import Iterable, Mapping


ARM_SET_PARAMETER_SCALE = 1000
"""`parameter = joint_id * SCALE + angle_deg` 的进位基数（2026-08-18 锁定）。"""

MAX_ANGLE_COMPONENT_DEG = ARM_SET_PARAMETER_SCALE - 1
"""角度分量上限。超过它就会进位、污染关节编号字段。"""

ARM_ERROR_NOT_FROZEN = 9010
"""ARM_SET 被拒：关节范围未冻结（资格不足）。mock 与 real 两侧共用同一码。"""

ARM_ERROR_TARGET_REJECTED = 9011
"""ARM_SET 被拒：目标不合法（未知关节、越界、非整度角、格式错误）。"""

ARM_ERROR_GRIPPER_POLICY = 9012
"""ARM_SET 被拒：目标是爪子，而爪子按 C-2 / C-9 走 GRAB / RELEASE。"""


class ArmJointNotFrozen(PermissionError):
    """关节范围尚未冻结 / 尚未标定，不允许下发真实 ARM_SET。

    这是**资格**问题（“现在还不该发”），不是目标本身不合法。
    """


class ArmTargetRejected(ValueError):
    """目标本身不合法：未知关节、越界、非整度角、编码字段冲突或参数格式错误。"""


class ArmGripperPolicyRejected(ArmTargetRejected):
    """目标是爪子：按 C-2 / C-9，爪子必须走 GRAB / RELEASE，不走 ARM_SET。"""


class ArmJoint(IntEnum):
    """ARM_SET 的关节编号，**逐项复用固件 `arm.h::Arm_Joint` 的取值**。

    两侧零映射：固件可以直接把 `joint` 当 `arm_pulse_us[]` 的数组下标用。
    爪子的编号 4 保留（它是固件的舵机通道），但树莓派策略不通过 ARM_SET
    控制爪子（C-2 / C-9）。
    """

    BASE = 0
    SHOULDER = 1
    ELBOW = 2
    WRIST = 3
    GRIPPER = 4


ARM_SET_JOINTS: tuple[ArmJoint, ...] = (
    ArmJoint.BASE,
    ArmJoint.SHOULDER,
    ArmJoint.ELBOW,
    ArmJoint.WRIST,
)
"""树莓派允许通过 ARM_SET 下发的关节（不含爪子，C-2 / C-9）。"""

ARM_JOINT_PULSE_LIMITS_US: Mapping[ArmJoint, tuple[int, int]] = {
    ArmJoint.BASE: (500, 2500),
    ArmJoint.SHOULDER: (500, 1500),
    ArmJoint.ELBOW: (500, 2500),
    ArmJoint.WRIST: (500, 2500),
    ArmJoint.GRIPPER: (1200, 1540),
}
"""各关节脉宽上下限（µs），抄自 arm.c 的 `ARM_JOINT_PULSE_MIN/MAX`。

肩（80KG，RDS5180）按电机参数文件限制在 500~1500µs；爪的 1200 是开爪限位、
1540 是“正好抓紧 10cm 方块”的闭合限位（超过会堵转）。
"""

ARM_JOINT_ANGLE_RANGE_DEG: Mapping[ArmJoint, float] = {
    ArmJoint.BASE: 270.0,
    ArmJoint.SHOULDER: 270.0,
    ArmJoint.ELBOW: 270.0,
    ArmJoint.WRIST: 270.0,
    ArmJoint.GRIPPER: 270.0,
}
"""各关节总行程（度），抄自 arm.c 的 `ARM_JOINT_ANGLE_RANGE_DEG`。

含义：脉宽下限 ↔ 0°，脉宽上限 ↔ 本值，中间线性——这是 arm.c 自己用来做
“每秒 45°→µs/s”换算的假设。arm.c 注释明确写了肩如果实测是 180°，要把那一项
改成 180；**在实测确认前，这里的 270 只能当作固件当前值，不是机械事实**。
"""


def _as_joint(value: object) -> ArmJoint:
    """把外部输入（int / ArmJoint）收敛成 ArmJoint，未知编号即拒绝。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArmTargetRejected(f"joint must be an ArmJoint or int, got {value!r}")
    try:
        return ArmJoint(value)
    except ValueError as exc:
        allowed = ", ".join(f"{item.value}={item.name}" for item in ArmJoint)
        raise ArmTargetRejected(
            f"unknown ARM_SET joint id {value}; allowed: {allowed}"
        ) from exc


def _as_angle(value: object) -> float:
    """把外部输入收敛成有限浮点角度。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArmTargetRejected(f"angle_deg must be a number, got {value!r}")
    angle = float(value)
    if not isfinite(angle):
        raise ArmTargetRejected("angle_deg must be finite")
    return angle


def _require_range(
    value: object, name: str, *, encoder_limited: bool
) -> tuple[float, float]:
    """校验一个 (low, high) 角度区间，返回浮点元组。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise ArmTargetRejected(f"{name} must be a (low, high) pair, got {value!r}")
    if len(value) != 2:
        raise ArmTargetRejected(f"{name} must have exactly two entries, got {value!r}")
    low, high = (_as_angle(item) for item in value)
    if not low < high:
        raise ArmTargetRejected(f"{name} must satisfy low < high, got {value!r}")
    if encoder_limited and high > MAX_ANGLE_COMPONENT_DEG:
        raise ArmTargetRejected(
            f"{name} upper bound {high} exceeds {MAX_ANGLE_COMPONENT_DEG}: "
            "parameter 用 joint_id*1000 + angle 编码，角度分量超过 999 会进位"
            "污染关节编号字段"
        )
    return (low, high)


@dataclass(frozen=True)
class ArmJointSpec:
    """单个关节的范围与“这个数字从哪来”。

    - `software_range_deg`：软件允许下发的范围。`None` = 未冻结 → 拒绝下发。
    - `design_range_deg`：机构/固件声明的总行程，仅供说明，不用于放行判断。
    - `evidence`：范围来源（会议编号 / 文档 / 源码）。不许留空——防止有人塞进
      一个没有出处的数字。
    """

    joint: ArmJoint
    software_range_deg: tuple[float, float] | None
    evidence: str
    design_range_deg: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        joint = _as_joint(self.joint)
        object.__setattr__(self, "joint", joint)
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ArmTargetRejected(
                f"{joint.name}: evidence must record where the range came from"
            )
        if self.design_range_deg is not None:
            object.__setattr__(
                self,
                "design_range_deg",
                _require_range(
                    self.design_range_deg,
                    f"{joint.name} design_range_deg",
                    encoder_limited=False,
                ),
            )
        if self.software_range_deg is not None:
            object.__setattr__(
                self,
                "software_range_deg",
                _require_range(
                    self.software_range_deg,
                    f"{joint.name} software_range_deg",
                    encoder_limited=True,
                ),
            )

    @property
    def frozen(self) -> bool:
        """软件值域是否已冻结（冻结才允许下发）。"""
        return self.software_range_deg is not None


@dataclass(frozen=True)
class ArmJointTable:
    """全部 `ArmJoint` 成员的冻结状态表。

    构造时**必须**给出每一个成员：漏掉一个关节等于“忘了检查”，宁可在这里报错，
    也不要在运行时悄悄放过。
    """

    specs: Mapping[ArmJoint, ArmJointSpec]

    def __post_init__(self) -> None:
        copied = dict(self.specs)
        missing = [item.name for item in ArmJoint if item not in copied]
        if missing:
            raise ArmTargetRejected(
                f"arm joint table must define every joint; missing {missing}"
            )
        for key, spec in copied.items():
            member = _as_joint(key)
            if spec.joint is not member:
                raise ArmTargetRejected(
                    f"table key {member.name} does not match spec joint "
                    f"{spec.joint.name}"
                )
        # 冻结 dataclass 里放可变 dict 时做一次防御性拷贝，避免调用方事后改表。
        object.__setattr__(self, "specs", copied)

    def spec(self, joint: ArmJoint | int) -> ArmJointSpec:
        return self.specs[_as_joint(joint)]

    @property
    def fully_frozen(self) -> bool:
        return all(spec.frozen for spec in self.specs.values())

    def frozen_joints(self) -> tuple[ArmJoint, ...]:
        return tuple(item for item in ArmJoint if self.specs[item].frozen)

    def unfrozen_joints(self) -> tuple[ArmJoint, ...]:
        return tuple(item for item in ArmJoint if not self.specs[item].frozen)

    def describe(self) -> str:
        """一行式表状态，用于启动日志和现场排查。"""
        parts = []
        for item in ArmJoint:
            spec = self.specs[item]
            limit = spec.software_range_deg
            parts.append(
                f"{item.name}={limit[0]:g}..{limit[1]:g}"
                if limit is not None
                else f"{item.name}=UNFROZEN"
            )
        return "; ".join(parts)


def default_arm_joint_table() -> ArmJointTable:
    """按 2026-08-19 的确认状态构造默认表：**没有任何关节被冻结**。"""
    travel = ARM_JOINT_ANGLE_RANGE_DEG
    return ArmJointTable(
        {
            ArmJoint.BASE: ArmJointSpec(
                joint=ArmJoint.BASE,
                design_range_deg=(0.0, travel[ArmJoint.BASE]),
                software_range_deg=None,
                evidence=(
                    "总行程取 arm.c ARM_JOINT_ANGLE_RANGE_DEG（固件当前值）。C-10"
                    "（2026-08-19）：云盘机械上可转 360°，软件工作弧为「右侧↔车尾」"
                    "约 90°，但弧的绝对角度基准尚未标定，故 software_range_deg "
                    "保持未冻结"
                ),
            ),
            ArmJoint.SHOULDER: ArmJointSpec(
                joint=ArmJoint.SHOULDER,
                design_range_deg=(0.0, travel[ArmJoint.SHOULDER]),
                software_range_deg=None,
                evidence=(
                    "总行程取 arm.c ARM_JOINT_ANGLE_RANGE_DEG=270（arm.c 注释：若实测"
                    "是 180° 应改为 180，尚未实测）。C-11（2026-08-19）：肩的角度范围"
                    "与角度→脉宽映射尚未冻结"
                ),
            ),
            ArmJoint.ELBOW: ArmJointSpec(
                joint=ArmJoint.ELBOW,
                design_range_deg=(0.0, travel[ArmJoint.ELBOW]),
                software_range_deg=None,
                evidence=(
                    "固件 arm.h/arm.c 已有肘（TIM7 第 5 路），但 C-9（2026-08-19）"
                    "机械组把肘列为计划新增、C-11 未冻结其角度范围，故保持未冻结"
                ),
            ),
            ArmJoint.WRIST: ArmJointSpec(
                joint=ArmJoint.WRIST,
                design_range_deg=(0.0, travel[ArmJoint.WRIST]),
                software_range_deg=None,
                evidence="C-11（2026-08-19）：腕的角度范围与角度→脉宽映射尚未冻结",
            ),
            ArmJoint.GRIPPER: ArmJointSpec(
                joint=ArmJoint.GRIPPER,
                design_range_deg=None,
                software_range_deg=None,
                evidence=(
                    "C-2 / C-9（2026-08-19）：爪子不走 ARM_SET，抓放用 GRAB / RELEASE；"
                    "固件爪通道是开合端点 1200/1540µs，不是角度关节"
                ),
            ),
        }
    )


UNFROZEN_ARM_JOINT_TABLE = default_arm_joint_table()
"""默认表：反映 2026-08-19 的确认现状——任何 ARM_SET 都会被拒绝。"""


def arm_joint_table_with_frozen_ranges(
    ranges: Mapping[ArmJoint | int, tuple[float, float]],
    *,
    evidence: str,
) -> ArmJointTable:
    """在默认表基础上冻结指定关节的软件值域，未给出的关节保持未冻结。

    电控/机械冻结角度范围后（C-11），由现场配置调用本函数构造可下发用的表，
    **而不是修改模块里的常量**。`evidence` 必填且非空：冻结数字必须能说出出处。
    """
    if not isinstance(evidence, str) or not evidence.strip():
        raise ArmTargetRejected(
            "evidence must record where the frozen ranges came from"
        )
    specs = dict(default_arm_joint_table().specs)
    for key, value in ranges.items():
        member = _as_joint(key)
        base = specs[member]
        specs[member] = ArmJointSpec(
            joint=member,
            design_range_deg=base.design_range_deg,
            software_range_deg=_require_range(
                value,
                f"{member.name} software_range_deg",
                encoder_limited=True,
            ),
            evidence=f"{base.evidence}；冻结范围来源：{evidence.strip()}",
        )
    return ArmJointTable(specs)


def parse_arm_joint_ranges(text: str) -> dict[ArmJoint, tuple[float, float]]:
    """解析 ROS 参数里的冻结值域字符串，供现场配置使用。

    格式：`"0:0:90;1:0:180;3:0:180"`（关节编号:下限:上限，分号分隔）。
    空串 = 什么都没冻结（默认，拒绝一切 ARM_SET）。
    """
    if not isinstance(text, str):
        raise ArmTargetRejected(f"arm joint ranges must be a string, got {text!r}")
    ranges: dict[ArmJoint, tuple[float, float]] = {}
    for chunk in text.split(";"):
        entry = chunk.strip()
        if not entry:
            continue
        fields = [field.strip() for field in entry.split(":")]
        if len(fields) != 3:
            raise ArmTargetRejected(
                f"malformed arm joint range {entry!r}; expected joint:min:max"
            )
        try:
            joint = _as_joint(int(fields[0]))
            low, high = (float(fields[1]), float(fields[2]))
        except ValueError as exc:
            raise ArmTargetRejected(
                f"malformed arm joint range {entry!r}; expected joint:min:max"
            ) from exc
        if joint in ranges:
            raise ArmTargetRejected(f"duplicate arm joint range for {joint.name}")
        ranges[joint] = _require_range(
            (low, high), f"{joint.name} software_range_deg", encoder_limited=True
        )
    return ranges


def arm_pulse_us_for_angle(joint: ArmJoint | int, angle_deg: float) -> float:
    """按 arm.c 的线性映射，把绝对角度换算成预测脉宽（µs）。

    映射：`0° ↔ 脉宽下限`，`总行程 ↔ 脉宽上限`，中间线性。

    ⚠️ 两点必须记住：

    1. 这是**软件输出指令的预测值**，不是测得的关节角度。机械臂纯开环无位置
       反馈（C-1），本函数无法证明舵机真的转到了那个角度。
    2. 固件侧会把脉宽夹取到上下限；这里**拒绝**越界角度。差异是故意的：越界
       应该在我们的离线核算阶段就暴露，而不是靠固件兜底。
    """
    member = _as_joint(joint)
    angle = _as_angle(angle_deg)
    travel = ARM_JOINT_ANGLE_RANGE_DEG[member]
    low, high = ARM_JOINT_PULSE_LIMITS_US[member]
    if not 0.0 <= angle <= travel:
        raise ArmTargetRejected(
            f"{member.name} angle {angle:g} 超出该关节总行程 [0, {travel:g}]"
        )
    return low + (high - low) * (angle / travel)


@dataclass(frozen=True)
class ArmJointTarget:
    """一个关节的目标绝对角度（度）。"""

    joint: ArmJoint
    angle_deg: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "joint", _as_joint(self.joint))
        object.__setattr__(self, "angle_deg", _as_angle(self.angle_deg))


@dataclass(frozen=True)
class ArmPose:
    """一组关节目标 = 一个姿态。

    **顺序由调用方决定，本模块不重排。** 关节先动哪个是机械问题（碰撞包络、
    负载下垂），C-11 未冻结前不猜；调用方给什么顺序就按什么顺序编码和下发。
    """

    targets: tuple[ArmJointTarget, ...]

    def __post_init__(self) -> None:
        normalized = tuple(self.targets)
        if not normalized:
            raise ArmTargetRejected(
                "an arm pose must contain at least one joint target"
            )
        seen: set[ArmJoint] = set()
        for target in normalized:
            if not isinstance(target, ArmJointTarget):
                raise ArmTargetRejected(
                    f"arm pose entries must be ArmJointTarget, got {target!r}"
                )
            if target.joint in seen:
                raise ArmTargetRejected(
                    f"arm pose repeats joint {target.joint.name}: "
                    "同一关节在一次姿态里只能有一个目标角度"
                )
            seen.add(target.joint)
        object.__setattr__(self, "targets", normalized)

    def joints(self) -> tuple[ArmJoint, ...]:
        return tuple(target.joint for target in self.targets)


def arm_rejection_error_code(error: BaseException) -> int:
    """把一次 ARM_SET 拒绝的理由映射成对外错误码。

    mock 与 real 两侧共用本函数，保证“同一原因、同一错误码”——否则现场只看
    错误码根本分不清是“没冻结”还是“越界”。
    """
    if isinstance(error, ArmGripperPolicyRejected):
        return ARM_ERROR_GRIPPER_POLICY
    if isinstance(error, ArmJointNotFrozen):
        return ARM_ERROR_NOT_FROZEN
    return ARM_ERROR_TARGET_REJECTED


def validate_arm_target(target: ArmJointTarget, *, table: ArmJointTable) -> None:
    """下发资格 + 策略 + 值域校验；不合法就抛异常，绝不静默夹取。"""
    if not isinstance(target, ArmJointTarget):
        raise ArmTargetRejected(f"expected ArmJointTarget, got {target!r}")
    if target.joint is ArmJoint.GRIPPER:
        raise ArmGripperPolicyRejected(
            "爪子不走 ARM_SET：按 C-2 / C-9 抓放必须用 GRAB / RELEASE 开关命令"
        )
    spec = table.spec(target.joint)
    if not spec.frozen:
        raise ArmJointNotFrozen(
            f"{target.joint.name} 未冻结，拒绝下发 ARM_SET（{spec.evidence}）"
        )
    low, high = spec.software_range_deg
    if not low <= target.angle_deg <= high:
        raise ArmTargetRejected(
            f"{target.joint.name} angle {target.angle_deg:g} 超出软件值域 "
            f"[{low:g}, {high:g}]"
        )
    # 整度要求属于“这条命令能不能下发”，不是编码细节：0x20 的 parameter 用
    # joint_id*1000+angle 进位编码，没有小数位，45.5° 只能被写成 45° 或 46°，
    # 两者都是“发出去的姿态与以为的不同”。放在这里而不是只放在
    # `encode_arm_set_parameter`，是为了让 mock 路径也拒绝同样的输入——mock 不
    # 编码，只做策略校验，否则同一个请求会出现“mock 成功 / 真机 9011”的分叉。
    if not target.angle_deg.is_integer():
        raise ArmTargetRejected(
            f"angle_deg must be a whole degree, got {target.angle_deg!r}: "
            "parameter = joint_id*1000 + angle 里没有小数位"
        )


def validate_arm_pose(pose: ArmPose, *, table: ArmJointTable) -> None:
    """校验整个姿态：每个关节都单独过一遍 `validate_arm_target`。"""
    if not isinstance(pose, ArmPose):
        raise ArmTargetRejected(f"expected ArmPose, got {pose!r}")
    for target in pose.targets:
        validate_arm_target(target, table=table)


def encode_arm_set_parameter(target: ArmJointTarget, *, table: ArmJointTable) -> int:
    """把关节目标编码成 0x20 的 `parameter`（`joint_id*1000 + angle_deg`）。

    编码前必须通过 `validate_arm_target`——整度要求与策略/值域检查同在那一处，
    所以 mock 与真机不会对同一个角度给出不同结论。
    """
    validate_arm_target(target, table=table)
    return int(target.joint) * ARM_SET_PARAMETER_SCALE + int(target.angle_deg)


def encode_arm_pose_parameters(
    pose: ArmPose, *, table: ArmJointTable
) -> tuple[int, ...]:
    """把一个姿态编码成按 `pose.targets` 顺序排列的 parameter 序列。"""
    validate_arm_pose(pose, table=table)
    return tuple(
        encode_arm_set_parameter(target, table=table) for target in pose.targets
    )


def decode_arm_set_parameter(
    parameter: int, *, table: ArmJointTable | None = None
) -> ArmJointTarget:
    """解析 0x20 的 `parameter`。

    默认只做**结构**校验（关节编号已知、角度分量落在 0..999），因为“读懂一条
    收到的报文”不该顺带执行下发策略。传入 `table` 时附加“不是爪子 / 已冻结 /
    在值域内”的策略校验——这样回环自检（发出去再解回来）和现场收包诊断可以
    共用同一个函数。
    """
    if isinstance(parameter, bool) or not isinstance(parameter, int):
        raise ArmTargetRejected(f"parameter must be an int, got {parameter!r}")
    if parameter < 0:
        raise ArmTargetRejected(
            f"parameter {parameter} 为负：V1 的绝对角度不允许负值"
        )
    joint_id, angle = divmod(parameter, ARM_SET_PARAMETER_SCALE)
    target = ArmJointTarget(_as_joint(joint_id), float(angle))
    if table is not None:
        validate_arm_target(target, table=table)
    return target


def format_arm_pose(pose: ArmPose) -> str:
    """把姿态格式化成可读字符串，用于日志与现场排查。"""
    return ", ".join(
        f"{target.joint.name}={target.angle_deg:g}°" for target in pose.targets
    )


def iter_arm_joint_targets(
    entries: Iterable[tuple[ArmJoint | int, float]]
) -> tuple[ArmJointTarget, ...]:
    """从 `(joint, angle)` 序列构造目标元组，便于配置解析。"""
    return tuple(ArmJointTarget(_as_joint(joint), angle) for joint, angle in entries)


# ---------------------------------------------------------------------------
# 零位移自检参考（现场联调用）
# ---------------------------------------------------------------------------

ARM_SAFE_REFERENCE_PULSE_US: Mapping[ArmJoint, int] = {
    ArmJoint.BASE: 1474,
    ArmJoint.SHOULDER: 1086,
    ArmJoint.ELBOW: 1500,
    ArmJoint.WRIST: 683,
    ArmJoint.GRIPPER: 1519,
}
"""上电授权后的安全姿态脉宽（µs），抄自 arm.c 的 `ARM_SAFE_PULSE_US`。

arm.c 里这些值是**安装时实测**的脉宽，注释明确写着“实车前必须按机械零点修改”。
它们不是角度，也不代表已经标定。用途只有一个：**零位移自检**——把安全姿态脉宽
换算回角度再下发，机械臂理论上不动，于是可以在几乎零机械风险的前提下验证整条
`ARM_SET` 链路（上位机编码 → 0x20 → 固件解析 → 角度换算 → 舵机输出 → 0x21 回传）。

arm.c 改了这张表，这里必须一起改：`tests/test_arm.py` 会解析 arm.c 逐项比对。
"""


def arm_angle_for_pulse_us(joint: ArmJoint | int, pulse_us: float) -> float:
    """脉宽 → 绝对角度（`arm_pulse_us_for_angle` 的逆运算）。"""
    member = _as_joint(joint)
    if isinstance(pulse_us, bool) or not isinstance(pulse_us, (int, float)):
        raise ArmTargetRejected(f"pulse_us must be a number, got {pulse_us!r}")
    pulse = float(pulse_us)
    if not isfinite(pulse):
        raise ArmTargetRejected("pulse_us must be finite")
    travel = ARM_JOINT_ANGLE_RANGE_DEG[member]
    low, high = ARM_JOINT_PULSE_LIMITS_US[member]
    if not low <= pulse <= high:
        raise ArmTargetRejected(
            f"{member.name} 脉宽 {pulse:g} 超出该关节范围 [{low}, {high}]"
        )
    return (pulse - low) * travel / (high - low)


def arm_zero_displacement_targets() -> tuple[ArmJointTarget, ...]:
    """联调用的“不应该有可见动作”角度目标。

    对每个 `ARM_SET_JOINTS` 成员取安全姿态脉宽，反算角度并**取整到整度**
    （进位编码没有小数位）。取整误差 ≤ 约 3µs 的预测脉宽偏差，远小于肉眼可见
    的位移，所以这些目标可以当作安全的链路自检指令。

    不含爪子：爪子不走 ARM_SET（C-2 / C-9），它的参考动作是 GRAB / RELEASE。
    """
    targets: list[ArmJointTarget] = []
    for joint in ARM_SET_JOINTS:
        angle = arm_angle_for_pulse_us(joint, ARM_SAFE_REFERENCE_PULSE_US[joint])
        targets.append(ArmJointTarget(joint, float(round(angle))))
    return tuple(targets)
