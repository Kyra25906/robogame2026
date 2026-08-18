# 树莓派部署与真车联调记录（2026-08-18）

> 记录规则：只记录已确认的事实；推测/待确认标注「⚠️待确认」。密码等敏感信息不记录。
> 关联：`RASPBERRY_PI_DEPLOYMENT_LOG_2026-08-10.md`（08-10 基础部署）、`RASPBERRY_PI_REAL_CAR_AGENT_HANDOFF_2026-08-12.md`（08-12 交接）。

## 1. 部署进展（已确认）

- **代码集成**：`feature/line_follow`（第二位同学 XuLingfeng）已 cherry-pick 并入 `integration/line-follow-t26` 分支（HEAD `b2f7557`），Windows 全量 **499 tests OK**（原 468 + line_follow 31 项）。
- **树莓派部署**：GitHub clone `integration/line-follow-t26` → `~/robogame`，`rosdep install` 装齐依赖 → `colcon build` 9 包成功 → 树莓派单测 **499 tests OK（skipped=54）**，与 Windows/Ubuntu 一致。
- **环境确认**：Ubuntu Server 24.04 arm64、ROS 2 Jazzy、`rg26@robogame-t26-rpi4`（SSH 经手机热点，IP 动态）。

## 2. 真车通信联调（已确认）

- **串口**：`/dev/serial/by-id/usb-STMicroelectronics_STM32_Virtual_ComPort_307A39653433-if00` 与 `robot_field.yaml` 配置一致。
- **STATUS**：`communication_ok=true`、`detail="decoded MCU V1 STATUS"`、**50Hz 稳定**（min 17ms / max 22ms / std 1ms）。
- **握手**：HELLO→ACK→READY 正常（`validated type=0x13`）。
- **ODOM**：`/wheel_odom` 正常发布，姿态/线速度/角速度在测试中持续变化。
- **授权**：长按 PB2 → `physical_start=true` 成功。

## 3. 底盘运动验证（已确认）

- **E2 方向**（架空，O 型麦轮，50Hz 发送）：
  - 前进（+vx）、后退（-vx）：四轮方向正确 ✅
  - 左移（+vy）、右移（-vy）：O 型麦轮转向组合正确 ✅
  - 旋转（+wz）：ODOM `angular.z≈+0.2` 与命令 `+0.3` 同向（方向正确 ✅）；轮子转向「左前/左后向前、右前/右后向后」待落地复核
- **死区边界**：0.02 m/s 仍能转动 → 树莓派速度命令层面无明显死区（电控提及 PWM 40-50 死区为电机驱动器物理特性，⚠️待电控确认换算关系）。
- **命令帧率教训**：**10Hz 发送会抖动/一段一段**（robot_bridge `command_timeout_s=0.15` 边缘，偶发插零速）；**50Hz 发送完全正常**。真实运行时 motion_control 20Hz + robot_bridge 心跳，不受影响——但测试脚本必须用 50Hz。

## 4. 问题与待确认（⚠️）

### 4.1 STM32 偶发重启（boot_id 递增）
- 现象：boot_id 从 1 → 4 → 6 → 9 → 12，偶发递增（非持续，稳定后可长时间不变）。
- 触发：曾出现在旋转大电流测试后、授权撤销伴随出现；非稳定复现。
- 影响：重启清掉 `physical_start` 授权 → 命令被固件拒绝（表现为「命令没反应」）。
- ⚠️ 待电控：查复位源（RCC_CSR）、与供电/大电流关系。

### 4.2 M2（右前轮）编码器/实际转速为 0
- 现象：电控 Keil 观测 M2 `actual_rpm` 恒 0；曾出现 M2「完全不转」后恢复（疑似接线接触不良）。
- ⚠️ 待电控/机械：M2 编码器接线、编码器本身、固件 ODOM 是否上送 M2 计数。

### 4.3 battery_voltage 恒 0.0
- 现象：`battery_voltage` 一直 0.0。
- ⚠️ 待电控：电池电压检测接线/固件 ADC 是否上送。

### 4.4 机械臂偶发乱动（小幅度）
- 现象：联调中机械臂偶发小幅乱动。
- 说明：我们从未发送机械臂命令（ARM_SET/GRAB 均未发）；新固件含 `arm.c`（上电不输出 PWM、授权才动、遥控速率控制）——乱动可能来自遥控误触/电控调试。
- ⚠️ 待确认：遥控器状态、电控是否在 Keil 手动操作。

## 5. 电控沟通要点（可转发）

> 树莓派联调测试汇总（2026-08-18）：
> 1. ✅ 通信/握手/授权正常，STATUS 50Hz 稳定
> 2. ✅ 前进/后退/左移/右移 方向全部正确（O 型麦轮）
> 3. ✅ 旋转方向正确（ODOM 与命令同向）
> 4. ⚠️ STM32 偶发重启（boot_id 递增），重启清授权 → 命令被拒；请查复位源
> 5. ❌ M2（右前）编码器/实际转速恒 0（Keil 确认）；请查接线/编码器
> 6. ⚠️ battery_voltage 恒 0.0；请查电压检测
> 7. 机械臂未发任何命令，偶发乱动待确认（遥控/调试？）

## 6. 下一步（建议）

1. 电控修 M2 编码器 + 电压检测 + 查重启源
2. 修复后重测旋转方向（落地低速）+ E3 落地标定（直行1m/横移1m/转360° ×10）
3. 机械臂接口（ARM_SET 归一化角度）等电控实现后接入（见 `MECHANISM_0x20_INTERFACE_ALIGNMENT_2026-08-18.md`）

---

## 7. 夜间联调补充（2026-08-18 19:00 后，含电控联调）

### 7.1 完整链路首次打通（已确认）

- **完整闭环验证成功**：goal（/motion/goal）→ motion_control → localization(/pose) → /cmd_vel → robot_bridge → 固件 → 电机 → 到点 → **`/motion/result: SUCCESS`**。
- **前提**：三个节点必须按依赖顺序启动（robot_bridge → localization → motion_control），且**单实例**（双实例会互相打架，本次多次踩坑：motion_control/robot_bridge 都出现过双实例）。

### 7.2 三个软件问题（已定位根因，均已修复/缓解）

| # | 问题 | 根因 | 修复 | 状态 |
|---|---|---|---|---|
| 1 | **看门狗误触发**（error_code 偶发 4001，车走走停停） | robot_bridge 心跳 `_send_heartbeat` 依赖 `communication_ok`——odom 跳变导致 communication_ok 抖动 → 心跳断 → 固件 150ms 看门狗触发 | **心跳独立于 communication_ok**（commit `801bc44`）：心跳只要握手完成+串口在就一直 50Hz 发 | ✅ 已验证（error_code 全 0） |
| 2 | **零速插入**（电控观测：CMD_VEL 80 条里 5 条停车，~300ms 一次顿挫） | `command_timeout_s=0.15` 太紧——motion_control 发 /cmd_vel 偶发延迟（树莓派 4B 负载高 ~200% CPU）→ robot_bridge `_send_zero_velocity` 插零速 | **command_timeout_s 0.15→0.5**（commit `d5cca56`） | ✅ 配置生效（本地 sed，`ros2 param get` 确认 0.5） |
| 3 | **LOCALIZATION_ERROR**（pose stale，车走几步停） | 固件 odom 偶发跳变（`rejected ODOM gap delta_ms=500`，落地仍 /pose 断流 1.6s）→ localization 无输入 → pose 断 → motion_control 判 stale | **pose_stale_s 0.25→1.5**（本地 sed 缓解） | ⚠️ 缓解生效，但根治需电控修固件 odom |

### 7.3 未解决/待电控（⚠️）

1. **固件 odom 偶发跳变（500ms~1.6s）**：落地仍复现，/pose 断流导致 LOCALIZATION_ERROR——**根治需电控查固件 odom 采样/上送为何被阻塞**（中断/调度/看门狗交互）。
2. **里程计 10 倍偏差**：命令 1m 实际只走 ~10cm——**怀疑固件轮径/编码器换算参数错一个数量级**（`chassis.h` 的 `WHEEL_RADIUS_M 0.06` 等需实测），需电控确认轮径/PPR/减速比。
3. **DDS 消息投递偶发失败**：`ros2 topic pub --once` 发 goal，motion_control 订阅存在（count 1）但**消息不投递**（debug 无新增）——疑似 FastDDS 在树莓派多节点下的已知问题；**待验证换 CycloneDDS**（`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`）。
4. **M2 编码器**：曾出现实际转速恒 0（Keil 确认），后恢复（疑似接线接触不良）——待电控查。
5. **battery_voltage 恒 0.0**：待电控查电压检测。
6. **机械臂偶发乱动**：未发任何机械臂命令，疑遥控误触/电控调试——待确认。

### 7.4 操作教训（避免下次踩坑）

1. **节点必须单实例**：启动前 `ps aux | grep <节点>` 确认无残留；双实例会互相打架（本次多次踩坑）。
2. **启动顺序**：robot_bridge → localization → motion_control（依赖顺序），后启动的依赖先启动的。
3. **真车测试脚本必须 50Hz 发送**（`time.sleep(0.02)`），10Hz 会触发 command_timeout 插零速。
4. **测试命令给足时长**（20 秒+），否则人看不过来。
5. **配置改动（robot.yaml）不需编译，只需重启节点**；代码改动（node.py）需 colcon build + 重启。

### 7.5 电控沟通要点（已发）

> 确认：error_code 偶发 4001（看门狗）；CMD_VEL 80 条 5 条停车；HELLO=2 正常（排除握手）。
> 请电控：①修固件 odom 跳变（根治）②放宽看门狗 150→250ms（治标，已同意）③暴露 rpi_watchdog_stop 到 Watch（诊断）④确认轮径/编码器参数（10 倍偏差）。

### 7.6 DDS 投递问题（待 2026-08-19 验证，已记录）

**现象**：`ros2 topic pub` 发 goal，motion_control 订阅存在（`/motion/goal` Subscription count: 1）但**消息不投递**（debug 日志无新增）——`--once` 和 `--rate` 都试过，均未到回调。

**待验证顺序（2026-08-19）**：
1. **方案 A（零成本优先）**：Python 直接发 goal（绕开 ros2 CLI）——排除「ros2 topic pub CLI 的 DDS 问题」。若 Python 能到 → 真实运行时（mission_manager 常驻节点发）不受影响，只是测试工具问题。
2. **方案 B（根治）**：若 Python 也到不了 → FastDDS 投递 bug → 换 CycloneDDS（`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`，装 `ros-jazzy-rmw-cyclonedds-cpp`），三个节点带环境变量重启 + 重测。
3. **关键参考**：真实比赛 goal 由 mission_manager（常驻节点）发，不是 ros2 CLI——**需验证 mission_manager 发 goal 能否到 motion_control**（更接近比赛场景）。

**影响评估**：若只是 CLI 问题（方案 A 解决），真实运行不受影响；若常驻节点也投递失败（方案 B 场景），则 DDS 是比赛隐患，必须换 RMW。
