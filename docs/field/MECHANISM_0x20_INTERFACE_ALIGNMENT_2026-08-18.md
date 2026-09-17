# 机械臂 0x20 接口对齐（算法 ↔ 电控，2026-08-18）

> ⚠️ **本文 §2 的关节表与 operation 编号已被 2026-08-19 修订取代**，见文末
> 「§6 修订记录（2026-08-19）」。修订内容：关节表按机械组 C-9 重定义为
> 云盘/肩/腕（+肘），编号直接复用固件 `arm.h::Arm_Joint`；ARM_SET 由 6 改为 7
> （6 已被 HOME 占用）；角度语义由「归一化 0~180」改为「绝对角度，量程取
> `arm.c` 的 `ARM_JOINT_ANGLE_RANGE_DEG`」。**不要按 §2/§3 的旧编号实现。**

> 状态：**接口结论已锁定（2026-08-18）**。机械臂为**纯开环无位置反馈**，姿态回传（闭环）排除；接口采用**归一化目标**（ARM_SET(6) + 归一化角度），不用标准工作表。待电控按此实现 arm.c 指令通道。
> 关联：`STM32_SERIAL_PROTOCOL_V1.md` 5.8 节（0x20 现有布局）、`docs/field/MANIPULATOR_ACTION_EXTENSION.md`（软件侧扩展约定）。

---

## 0. 接口结论（2026-08-18 锁定）

| 决策 | 结论 | 理由 |
|---|---|---|
| 指令格式 | **归一化目标**（ARM_SET + 归一化角度） | 语义化、与 LIFT「高度 mm」同风格；视觉对准需要细粒度控制，工作表承载不了 |
| 反馈通道 | **姿态回传（闭环）排除** | 机械臂纯开环无位置传感器，STM32 自己也不知道实际角度 |
| 标准工作表 | **不做**（可选后话） | 工作表写死动作序列，无法支持视觉对准的逐帧微调与失败重试的重发 |
| 甲2 性质 | **维持「算法只能重试、不能修正末端精度」** | 无反馈 = 无闭环修正可能；补偿靠视觉对准最后一步 + 失败重试 + 降低层数（P2-4 倾向 2 层） |

> ⚠️ 姿态回传被排除意味着：**若将来机械加装位置传感器（编码器/电位器），本结论需重新评估**——那时闭环修正会成为可能，甲2 从「补不了」变「可补」。

---

## 1. 现状（仓库证据）

现有 0x20 payload（`serial_protocol.py:18`，协议 V1 已冻结）：

```text
MECHANISM_COMMAND_PAYLOAD = struct.Struct("<HBBiI")
偏移  长度  类型   字段
0     2    u16    command_id    机构命令编号（匹配反馈用）
2     1    u8     operation     动作编号（1=GRAB, 2=RELEASE, 3=LIFT, 4=RETREAT, 5=STOP）
3     1    u8     flags         预留（当前恒 0）
4     4    i32    parameter     动作参数（LIFT=高度mm, RETREAT=距离mm, 其余=0）
8     2    u16    timeout_ms    STM32 端动作超时
```

**关键点**：`parameter` 字段**已经存在**，机械臂新动作可以复用，不必改 payload 结构（改结构 = 固件/上位机必须同步升级，风险大）。要约定的是 **operation 编号扩展 + parameter 的语义**。

## 2. 算法组建议的机械臂 operation 扩展（待电控/机械确认）

现有 operation 已有 GRAB(1)/RELEASE(2)/LIFT(3)/RETREAT(4)/STOP(5)。机械臂（臂 + 爪）建议新增：

| operation | 值 | parameter 语义 | 说明 |
|---|---|---|---|
| ARM_SET | 6 | 编码：`关节编号×1000 + 目标值` | 见下 |
| GRAB | 1 | 0（沿用现有） | 夹爪闭合 |
| RELEASE | 2 | 0（沿用现有） | 夹爪张开 |

**ARM_SET 的 parameter 编码（2026-08-18 锁定：方案甲 归一化角度）**：

```text
方案甲（归一化角度，已锁定）:
  parameter = joint_id * 1000 + angle_deg
  joint_id:  1 = 大臂（底臂），2 = 小臂，3 = 爪子
  angle_deg: 0~180（归一化角度，arm.c 内部再映射到 PWM 脉宽）
  例：大臂转到 45° → parameter = 1045

（方案乙「直接 PWM 脉宽」不再考虑——语义化目标更利于视觉对准微调与失败重发）
```

**纯开环约束**：arm.c 按角度→PWM 映射执行时，无位置反馈可确认到位——动作完成判据只能是「时间到/限位触发」，由树莓派按动作耗时（TIME_BUDGET）超时管理。视觉对准的最后一步收敛不依赖臂的重复精度。

## 3. 待电控确认的问题（2026-08-18 收敛后剩余）

1. arm.c 现有接口：臂/爪各几个关节？角度→PWM 的映射表（电控已给范围：臂底 500~1500、爪子 1200~1540）？
2. operation 用**新增 ARM_SET(6)**——算法组已定，请电控确认固件侧接受新编号；
3. 仲裁规则：**遥控在线时遥控优先**（与底盘一致，树莓派命令被丢弃）——请电控确认实现；
4. 安全：机械臂命令同样要求 HELLO + 看门狗 + PB2 授权（与底盘一致，80KG 臂不能裸控）——请电控确认；
5. 爪子：GRAB/RELEASE 沿用现有编号（1/2），**不走 ARM_SET**——抓放用封装动作，转臂用 ARM_SET。

## 4. 电控实现后算法侧要做的（等 arm.c 就位）

- 按结论扩展 `serial_protocol.py`：`MechanismOperation` 加 `ARM_SET`、`parameter` 编码/解码 + 单测
- `robot_bridge` 真实分支加 `ARM_SET` 映射（当前只映射 GRAB/RELEASE/HOME，`node.py:524-535`）
- `manipulator_client` 增加臂动作流程（复用 `MANIPULATOR_ACTION_EXTENSION.md` 的扩展步骤）
- fake 模式同步支持（A0.4 mock/real 契约——**✅ 2026-08-19 已落地**：`robot_bridge/node.py` 模块级声明 `REAL_MOCK_MECHANISM_COMMAND_DIFFERENCES` + `tests/test_contract_mock_real.py` 断言两侧命令集合对称差恰好等于声明；未来加 ARM_SET 时两侧必须同步或更新声明）
- **纯开环补偿策略**（甲2 维持「只能重试」）：
  - 视觉对准最后一步收敛（`calculate_alignment_command` + cube_perception）——不依赖臂的重复精度
  - 抓取失败重试 + 记账回滚（G3.1）
  - 降低层数（P2-4 倾向 2 层）

## 5. 一句话

> **机械臂纯开环无反馈（已确认）→ 姿态回传排除。接口锁定：ARM_SET(6) + 归一化角度（parameter = joint_id×1000 + angle_deg），GRAB/RELEASE 沿用现有编号，不用工作表。甲2 维持「算法只能重试、不能修正末端精度」，补偿靠视觉对准最后一步 + 失败重试 + 降层。请电控按此实现 arm.c 通道，回上面 5 个确认问题。**

---

## 6. 修订记录（2026-08-19）

> 触发：① 机械组现场确认（`给机械组现场问答表_2026-08-19.md` C-9~C-11）推翻了 §2
> 的关节表；② 电控侧 arm.c 已进仓库（`Four_Motor_PID_Test_1/.../Core/{Inc/arm.h,Src/arm.c}`），
> 实际关节数与编号与本文 §2 不同；③ 树莓派侧 `MechanismOperation` 已新增 `HOME = 6`。
> **§2、§3 的编号与角度语义作废，以下为准。** 原文保留不删，便于追溯当时的判断依据。

### 6.1 三处必须改的地方

| 项 | 旧（08-18） | 新（08-19） | 依据 |
|---|---|---|---|
| 关节表 | 大臂/小臂/爪子（3 个，1-based） | **腰（云盘）/肩/肘/腕（+爪为固件通道）** | 机械组 C-9；机械组明确“旧文档写的是大臂/小臂/爪子”需重定义 |
| 关节编号 | 提案 1/2/3 | **直接复用固件 `arm.h::Arm_Joint`：0=腰/云盘, 1=肩, 2=肘, 3=腕, 4=爪** | 与固件源码一致，两侧零映射 |
| ARM_SET operation | 6 | **7** | 6 已被 `HOME` 占用；4 在协议 V1 文档里留给 RETREAT |
| angle 语义 | 归一化 0~180 | **绝对角度，量程 = `arm.c` 的 `ARM_JOINT_ANGLE_RANGE_DEG`（当前各关节 270°）** | 与固件现有表一一对应；180 与 arm.c 的 270 不一致 |

`parameter = joint_id × 1000 + angle_deg` **保持不变**（编码方案本身没问题）；
0x20 payload 布局 `"<HBBiI"` 也**不变**，机械臂通道继续复用已有的 i32 `parameter` 字段。

### 6.2 固件侧现成的事实（抄自 arm.h / arm.c，非推测）

| arm.h `Arm_Joint` | 引脚 | 固件注释 | `ARM_JOINT_PULSE_MIN/MAX` | `ARM_JOINT_ANGLE_RANGE_DEG` |
|---|---|---|---|---|
| 0 `ARM_JOINT_BASE` | PA2 | 腰（基座）20KG | 500–2500 µs | 270° |
| 1 `ARM_JOINT_SHOULDER` | PA3 | 肩（臂底）80KG | 500–**1500** µs | 270°（注释：实测若为 180° 应改 180U） |
| 2 `ARM_JOINT_ELBOW` | PI7 | 肘（新增）20KG，TIM7 第 5 路 | 500–2500 µs | 270° |
| 3 `ARM_JOINT_WRIST` | PD14 | 腕 20KG | 500–2500 µs | 270° |
| 4 `ARM_JOINT_GRIPPER` | PD15 | 爪 20KG（开 1200 / 闭 1540 µs） | 1200–1540 µs | 270°（仅用于速率换算） |

注意 **固件的腰（基座）= 机械组说的云盘**（同一物理关节）；**固件的肘已经存在**，
而机械组 C-9 把肘列为“计划加”，两侧说法需要对齐（见 6.4）。

### 6.3 树莓派侧实现状态（2026-08-19 落地）

- `robogame_core/arm.py`：关节表 + 冻结状态 + 值域/策略校验 + parameter 编解码。
  关节编号、脉宽上下限、总行程**从固件源码抄来**，并由
  `tests/test_arm_firmware_sync.py` 解析 `arm.h`/`arm.c` 逐项比对（固件改了没跟就红）。
- `robogame_core/serial_protocol.py`：`MechanismOperation.ARM_SET = 7`，payload 不变。
- `robogame_core/mock_arm.py`：mock 路径的关节状态与拒绝逻辑，与 real 共用同一张表
  和同一组错误码（`9010` 未冻结 / `9011` 目标非法 / `9012` 爪子策略）。
- `robogame_interfaces/srv/SetArmJoint.srv` + `robot_bridge` 的 `/arm/set_joint`。
- **默认拒绝**：`arm_joint_ranges` 为空时任何关节都“未冻结”，`/arm/set_joint` 一律返回
  `9010`。冻结后只改 ROS 参数，不改代码。

### 6.4 仍未确认（阻塞真实 ARM_SET 下发）

1. **C-11：肩 / 肘 / 腕的角度范围与“角度→脉宽”映射未冻结** → 树莓派默认拒绝。
2. **云盘工作弧的绝对角度基准未标定**（C-10 要求“越界拒绝”，但没有基准就没有界）。
3. arm.c 注释自述：肩的总行程可能是 180° 而非 270°，**待实测**。
4. 肘的机构是否已装车（固件有通道、机械组说“计划加”）。
5. 电控是否接受 `ARM_SET = 7`，以及固件侧是否按 6.1 的编号实现。

以上任一项冻结后，只需改 `hardware.yaml` 的 `arm_joint_ranges` / 本文档，不需要改代码。

---

## 7. 固件侧实现状态（2026-08-19，电控通道落地）

> 触发：今晚要上车联调，树莓派侧通道已就绪（§6.3），固件侧此前对一切 0x20 回
> `RPI_ACK_BAD_STATE`，两侧对不上。本轮把固件侧补齐。

### 7.1 改了什么

| 文件 | 改动 |
|---|---|
| `Core/Inc/arm.h` | 新增自动控制接口：`Arm_AutoSetJointUs/Angle`、`Arm_AutoSetAllUs`、`Arm_AutoGoHome`、`Arm_AutoAbort`、`Arm_AutoAnyTarget/TimedOut/ClearTimeout`、`Arm_PulseUsFromAngle`、`Arm_GripperIsClosed` |
| `Core/Src/arm.c` | 自动目标状态机：逐关节目标 + 截止时间 + 限速推进；遥控优先夺权；撤权/看门狗/急停保持姿态 |
| `Core/Src/rpi_protocol.c` | 0x20 处理（GRAB/RELEASE/LIFT_ABS/STOP/HOME/ARM_SET）、0x21 状态上报、重发幂等、STATUS bit4 |

**没有新增 .c 文件**——不需要改 Keil 工程文件（`.uvprojx`）。

### 7.2 固件侧的行为约定（与树莓派侧对齐）

- 安全门与底盘**同一条**：`RPI_IsReady()` = HELLO 会话 + PB2 物理授权 + 无急停/协议故障/
  底盘故障 + 看门狗正常。不满足时 0x20 回 `ACK=5`（BAD_STATE）。
- `parameter = joint_id * 1000 + angle_deg`；关节编号复用 `arm.h::Arm_Joint`；
  **固件接受全部 5 个关节（含爪）**，爪子是否允许由上位机策略决定（上层拦，下层执行）。
- 角度是该关节的**绝对角度**：0° ↔ 脉宽下限，总行程 ↔ 脉宽上限，线性；超出行程**拒绝**
  （回 `ACK=4`），不夹取。
- 自动动作**限速**推进（`ARM_AUTO_RATE_SCALE × 45°/s`），不瞬跳。
- 完成判据只有"输出脉宽走到目标"或"截止时间到"：
  - 走到 → 0x21 `SUCCEEDED`；
  - 到点没走到 → 0x21 `FAILED` + `error_code=3020`（`RPI_ERROR_MECH_TIMEOUT`），
    该关节停在当前位置不动。
- **重发幂等**：上位机丢 ACK 会重发同一条命令（`command_id` 不变、串口 `sequence` 变）。
  固件识别为重发 → 回 `ACK=0` + 当前状态，**不重新执行**。否则一次丢 ACK 会让整条命令
  被 tracker 判死。
- **遥控优先**：遥控在线且任一机械臂通道（右摇杆 X/Y、D-pad 上/下、L1/L2、R1/R2）有动作时，
  全部自动目标立即清除并交回遥控。左摇杆是底盘，不参与夺权。
- `STOP`(5) 任何时候都接受，清目标并保持姿态。
- `LIFT_ABS`(3) 明确回 `FAILED` + `error_code=3010`（真车无升降装置，C-3），
  不假装接受后超时。

### 7.3 固件新增错误码

| 错误码 | 含义 |
|---:|---|
| `3010` | 真车没有升降装置（`LIFT_ABS` 被明确拒绝） |
| `3020` | 自动动作超过截止时间仍未到位 |
| `3021` | 预留：机械臂执行异常 |
| `3022` | 预留：机械臂参数非法（当前统一走 ACK=4） |

### 7.4 STATUS 标志位的诚实边界（重要）

- `bit4 GRIPPER_CLOSED`：爪的**输出脉宽**已到闭合端点附近。**命令推算值，不是测量值**
  ——爪没有位置反馈，也没有到位微动开关。
- `bit5 CUBE_PRESENT`：**恒为 0**。本车没有任何方块存在传感器，固件不把没测到的东西
  报成测到了。后果：`grab_verification_policy = cube_present / gripper_and_cube`
  在本车**永远无法通过**（只会超时），真车抓取只能用 `service_only`；
  放置可用 `gripper_open_and_cube_absent`（靠 bit4）。
- `bit6 MECHANISM_FAULT`：**目前恒为 0**。真正的机构故障检测（电流/限位/编码器）本车没有，
  不能凭空造一个；因此自动动作超时**不被报成机构故障**——如果报成机构故障，
  上位机会锁死在 `MECHANISM_ERROR: 9006`，必须按 PB2 物理重新授权才能继续，
  现场会因为一次正常超时而无法恢复。

### 7.5 仍未做的固件项（诚实清单）

1. **底盘-机械臂互锁**（C-4：车走时爪子不要动；C-8：GRAB 期间底盘必须静止）——
   固件侧尚未读取底盘运动状态做互锁，目前靠上位机保证。
2. **机构故障检测**（堵转/超温/限位）——需要电流或限位信号，本车没有。
3. **角度标定**：`0° ↔ 脉宽下限` 只是线性约定，机械零点尚未实测（C-11）。
