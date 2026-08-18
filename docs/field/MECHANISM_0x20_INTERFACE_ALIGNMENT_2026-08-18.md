# 机械臂 0x20 接口对齐（算法 ↔ 电控，2026-08-18）

> 状态：**草案，待电控确认**。电控指出：机械臂当前没有树莓派控制通道（0x20 收到即回 `ACK(BAD_STATE)`，`rpi_protocol.c:560-562`），需要新实现。电控列的三件事里，第一件「算法组给出 MECHANISM_COMMAND 的 payload 定义」是本文件。
> 关联：`STM32_SERIAL_PROTOCOL_V1.md` 5.8 节（0x20 现有布局）、`docs/field/MANIPULATOR_ACTION_EXTENSION.md`（软件侧扩展约定）。

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

**ARM_SET 的 parameter 编码建议**（二选一，**需电控确认 arm.c 用什么**）：

```text
方案甲（归一化角度，推荐）:
  parameter = joint_id * 1000 + angle_deg
  joint_id:  1 = 大臂（底臂），2 = 小臂，3 = 爪子
  angle_deg: 0~180（归一化角度，arm.c 内部再映射到 PWM 脉宽）
  例：大臂转到 45° → parameter = 1045

方案乙（直接 PWM 脉宽，若电控想复用遥控的脉宽表）:
  parameter = joint_id * 10000 + pwm_us
  joint_id:  1 = 大臂，2 = 小臂，3 = 爪子
  pwm_us:    臂底 500~1500、爪子 1200~1540（电控提供的实测范围）
  例：爪子开到 1400us → parameter = 31400
```

**算法组倾向方案甲（归一化角度）**：和 LIFT 的「高度 mm」同风格（语义化、非底层），且 arm.c 内部的脉宽映射/限幅/授权保护不被绕过（电控明确要求）。但最终**以电控的 arm.c 接口为准**。

## 3. 需要电控确认的问题（返回后算法组冻结）

1. arm.c 现有接口：臂/爪各自几个关节？用 PWM 脉宽还是角度？PWM 范围（电控已给：臂底 500~1500、爪子 1200~1540）？
2. operation 用**新增 ARM_SET(6)** 还是**复用现有编号**（比如 LIFT 承载臂、GRAB 承载爪）？
3. 仲裁规则：**遥控在线时谁优先？**（建议与底盘一致：遥控优先，RPi 命令被丢弃；协议里 0x20 在遥控在线时回 ACK_BAD_STATE）
4. 是否同样要求 HELLO + 看门狗 + PB2 授权？（建议与底盘一致——80KG 臂不能裸控）
5. 爪子是否已有独立 operation（GRAB/RELEASE 已映射）？还是也要走 ARM_SET？

## 4. 电控实现后算法侧要做的（等 arm.c 就位）

- 按确认结果扩展 `serial_protocol.py`：`MechanismOperation` 加 `ARM_SET`、`parameter` 编码/解码 + 单测
- `robot_bridge` 真实分支加 `ARM_SET` 映射（当前只映射 GRAB/RELEASE/HOME，`node.py:524-535`）
- `manipulator_client` 增加臂动作流程（复用 `MANIPULATOR_ACTION_EXTENSION.md` 的扩展步骤）
- fake 模式同步支持（A0.4 mock/real 契约：两侧命令集合必须一致或显式声明差异）

## 5. 一句话

> **0x20 结构不用改，改的是「operation 编号 + parameter 语义」。算法组建议新增 ARM_SET(6) + 归一化角度（方案甲），但以电控 arm.c 接口为准——请电控确认上面 5 个问题。**
