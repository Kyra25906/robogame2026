# robot_bridge：树莓派串口与机械臂控制说明

## 1. 功能边界

`robot_bridge` 负责在 ROS2 与 STM32 串口协议 V1 之间转换命令和状态。树莓派侧已实现机械命令状态机，STM32 侧也已实现 0x20/0x21 通道（2026-08-19，见 §8）；但这不代表真实机械臂已经可动——`LIFT_ABS` 会被固件以 `3010` 拒绝，真实夹取仍须按 §6 安全门逐项满足后才可在真车联调。不接真实 STM32 时，可用 PTY 模拟验收（§9）。

机械命令链路：

```text
ROS2服务 → 安全检查 → 0x20命令 → ACK → 0x21状态 → ROS2结果
```

只有匹配命令的 `SUCCEEDED` 且 `error_code=0` 才返回成功。

## 2. ROS2接口

| 服务 | 类型 | 命令 |
|---|---|---|
| `/gripper/grab` | `ExecuteMechanism` | `GRAB` |
| `/gripper/release` | `ExecuteMechanism` | `RELEASE` |
| `/mechanism/home` | `ExecuteMechanism` | `HOME` |
| `/mechanism/stop` | `ExecuteMechanism` | `STOP` |
| `/lift/set_height` | `SetLiftHeight` | 绝对高度，单位米 |
| `/arm/set_joint` | `SetArmJoint` | 机械臂单关节绝对角度，单位度（见 §9） |
| `/chassis/stop` | `ExecuteMechanism` | 全局底盘急停，不等同于机构STOP |

订阅 `/cmd_vel`，发布 `/wheel_odom`、`/imu/data` 和 `/robot/status`。

## 3. 机械传输参数

`robot_bridge` 节点内默认值（`node.py:128-130`；`hardware.yaml` 里也有一份同值副本，但那个文件是 legacy、不被加载）：

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `mechanism_ack_timeout_s` | `0.1` | 每次发送后等待帧ACK的时间 |
| `mechanism_status_timeout_s` | `0.3` | ACK或中间状态后等待下一状态的时间 |
| `mechanism_max_attempts` | `3` | 同一命令最多发送次数，包含首次发送 |

重试时串口帧 `sequence` 更新，但业务 `command_id` 保持不变。超时参数必须是有限正数，最大尝试次数必须为正整数，否则节点拒绝启动。

服务请求中的 `timeout_s` 是整条业务命令的上层期限，不应小于单次协议等待时间。LIFT请求会把米转换为整数毫米。

机械臂关节冻结参数：

| 参数 | 默认值 | 含义 |
|---|---|---|
| `arm_joint_ranges` | `""` | 允许下发的关节值域，格式 `"关节编号:下限:上限;..."`，如 `"0:0:90;1:0:180"` |
| `arm_joint_ranges_evidence` | `""` | 值域出处（会议编号/文档）。非空 `arm_joint_ranges` 时必须同时给出，否则拒绝启动 |
| `mock_arm_success` | `true` | 模拟模式注入 ARM_SET 失败 |

**默认值为空串 = 一个关节都没冻结 = 拒绝一切 `/arm/set_joint` 调用**（错误码 `9010`）。这是刻意的：肩/肘/腕的角度范围尚未由电控与机械冻结（现场问答表 C-11），此时下发任何角度都是猜。冻结后只改参数，不改代码。

## 4. Ubuntu启动

> 树莓派上的仓库路径是 `~/robogame`（用户 `rg26`）；`~/robogame2026-integration`
> 是另一台 Ubuntu 虚拟机（用户 `panwenhui`）的路径，不要混用。

```bash
cd ~/robogame/ros2_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 run robot_bridge robot_bridge --ros-args \
  --params-file src/robogame_bringup/config/robot.yaml \
  --params-file src/robogame_bringup/config/robot_field.yaml
```
（`hardware.yaml` 是 legacy，已不被任何启动入口加载；现场模式靠 `robot_field.yaml` 的 `mock_mode: false` 生效。）

确认串口权限：

```bash
ls -l /dev/serial/by-id/
groups
```

用户通常需要属于 `dialout` 组。修改用户组后需要注销并重新登录。

## 5. 服务调用示例

```bash
ros2 service call /gripper/grab robogame_interfaces/srv/ExecuteMechanism \
  "{command: GRAB, timeout_s: 3.0}"

ros2 service call /gripper/release robogame_interfaces/srv/ExecuteMechanism \
  "{command: RELEASE, timeout_s: 3.0}"

ros2 service call /mechanism/home robogame_interfaces/srv/ExecuteMechanism \
  "{command: HOME, timeout_s: 5.0}"

ros2 service call /mechanism/stop robogame_interfaces/srv/ExecuteMechanism \
  "{command: STOP, timeout_s: 1.0}"

ros2 service call /lift/set_height robogame_interfaces/srv/SetLiftHeight \
  "{height_m: 0.123, timeout_s: 5.0}"

# 关节编号逐项复用固件 arm.h 的 Arm_Joint（0=腰/云盘, 1=肩, 2=肘, 3=腕, 4=爪）。
# 爪子编号存在，但策略上禁止走 ARM_SET（抓放用 GRAB / RELEASE），调用会得到 9012。
ros2 service call /arm/set_joint robogame_interfaces/srv/SetArmJoint \
  "{joint: 0, angle_deg: 90.0, timeout_s: 5.0}"
```

## 6. 动作安全门

真实模式发送机械命令前必须同时满足：

- 串口已打开；
- HELLO/ACK握手完成；
- 最新STATUS有效且通信未超时；
- 没有急停；
- 没有机构故障；
- 物理启动授权有效；
- 没有另一条普通机械命令正在运行。

`/mechanism/stop` 可以取消活动命令并建立自己的STOP状态机。串口断开、MCU重启、急停或机构故障都会取消活动命令。

## 7. 错误码

| 错误码 | 含义 |
|---:|---|
| `0` | 成功 |
| `4` | 参数或超时非法 |
| `5` | 没有物理启动授权 |
| `6` | 已有机械命令正在运行 |
| `7` | 示例ACK拒绝码；实际非零ACK码原样返回 |
| `8` | 活动命令被机构STOP取消 |
| `9` | 不支持的真实机械命令 |
| `42` | 示例MCU执行错误；实际MCU终态错误码原样返回 |
| `2002` | 整条机械命令超过业务期限 |
| `9001` | 急停或全局STOP中断命令 |
| `9003` | 串口不可用、写失败或协议响应超时 |
| `9004` | MCU `boot_id`变化，旧会话命令作废 |
| `9006` | 机构故障或非法状态转换 |
| `9010` | ARM_SET 被拒：该关节值域尚未冻结（资格不足） |
| `9011` | ARM_SET 被拒：目标不合法（未知关节、越界、非整度角） |
| `9012` | ARM_SET 被拒：目标是爪子，按 C-2/C-9 必须走 GRAB / RELEASE |
| `1104` | 模拟模式注入的 ARM_SET 失败 |
| `3010` | **固件**返回：真车没有升降装置（`LIFT_ABS` 被明确拒绝，C-3） |
| `3020` | **固件**返回：自动动作超过截止时间仍未到位（该关节停在当前位置） |

> `3010` / `3020` 来自 STM32 的 0x21 `error_code`，由本节点原样透传。
> `3020` 只表示"命令没在预算内到位"，**不是机构故障**：固件不会因此置
> `STATUS_MECHANISM_FAULT`，所以不需要按 PB2 物理重新授权就能发下一条命令。
| `1104` | 模拟模式注入的 ARM_SET 失败 |

## 8. 机械臂通道（ARM_SET）

```text
/arm/set_joint → 关节冻结/值域/策略校验 → 0x20 operation=7, parameter=joint_id*1000+angle_deg
```

- **关节编号与固件一致**：直接复用 `Core/Inc/arm.h` 的 `Arm_Joint` 取值
  （0=腰/云盘、1=肩、2=肘、3=腕、4=爪），固件可把 `joint` 直接当数组下标用。
  `tests/test_arm_firmware_sync.py` 会解析固件源码逐项比对编号、脉宽上下限和总行程。
- **角度是绝对角度**，量程取 `arm.c` 的 `ARM_JOINT_ANGLE_RANGE_DEG`（当前各关节
  270°；arm.c 注释写明肩若实测为 180° 应改表，**未实测前这不是机械事实**）。
  编码为整数度，非整度调用直接拒绝（进位编码没有小数位）。
- **爪子不走 ARM_SET**：编号 4 保留给固件的舵机通道，但树莓派策略拒绝对它下发角度。
- **纯开环（C-1）**：机械臂没有位置反馈，服务成功只代表命令被接受并走完流程，
  **不代表舵机真的到位**。`robogame_core.arm.arm_pulse_us_for_angle` 只是输出指令的
  预测值，同样不能作为到位证据。
- mock 与 real 共用同一张关节表与同一组错误码（`arm_rejection_error_code`），
  因此两侧拒绝理由一致，只是 mock 不写串口。
- **固件侧已实现该通道**（2026-08-19）：0x20 的 GRAB/RELEASE/STOP/HOME/ARM_SET 会真正
  驱动舵机，`LIFT_ABS` 明确回 `3010`。约定与错误码见
  `docs/field/MECHANISM_0x20_INTERFACE_ALIGNMENT_2026-08-18.md` §7。
- **`cube_present` 恒为 0**：本车没有方块传感器。因此
  `grab_verification_policy` 真车只能用 `service_only`（`cube_present` /
  `gripper_and_cube` 永远无法通过）；放置可用 `gripper_open_and_cube_absent`
  （靠固件 `STATUS_GRIPPER_CLOSED`，而那是命令推算值、不是测量值）。

## 9. 树莓派侧验收

不连接真实STM32时，在Ubuntu隔离环境运行：

```bash
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
python3 -m unittest tests.test_robot_bridge_mechanism_integration -v
```

PTY套件覆盖正常操作、ACK/状态超时、重试、错误终态、急停、MCU重启、命令互斥、STOP抢占以及串口断线重连。固件侧 0x20/0x21 已实现（§8），真实机械臂联调现在只等真车硬件就位并经过单独授权。
