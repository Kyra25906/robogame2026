# 底盘自动运动使能条件清单（2026-08-18，电控确认）

> 用途：算法组在真车测试前的检查单。电控确认：以下 6 条**缺一不可**，任何一条不满足，树莓派的 CMD_VEL 都不会执行。
> 关键风险：**命令超过限幅不是「削顶」，而是触发协议故障锁存（rpi_protocol_fault），必须本地长按 PB2 重新授权才能恢复。** 限幅是硬门，不是软目标。

---

## 使能链（6 条，缺一不可）

| # | 条件 | 谁负责 | 怎么确认 |
|---|---|---|---|
| 1 | **本地长按 PB2 授权**（physical_start=1） | 现场操作员 | 长按 PB2 ~1.5s；看 `/robot/status` 的 `physical_start=true` |
| 2 | **树莓派发 HELLO 建立会话**（handshake READY） | 算法（robot_bridge） | 日志 `handshake complete`；`_handshake_state=READY` |
| 3 | **每 150ms 内有 HEARTBEAT 或 CMD_VEL**（看门狗） | 算法（robot_bridge） | `_send_heartbeat` 50Hz 自动发；确认 `/cmd_vel` 或心跳持续 |
| 4 | **无急停 / 协议故障 / 底盘故障** | 电控 + 现场 | `/robot/status`：`emergency_stop=false`、`mechanism_fault=false`、`error_code=0` |
| 5 | **遥控器离线**（⚠️ 最容易踩的坑） | 现场操作员 | **手柄必须关机/离线**——手柄在线时底盘切人工接管，树莓派 CMD_VEL 被丢弃，车只听手柄的 |
| 6 | **限幅非零**（本次电控改动） | 电控 | `chassis.h` 三个宏非 0 + 烧录后确认 |

## 数值约定（电控建议，算法已对齐）

| 量 | 值 | 依据 |
|---|---|---|
| 固件限幅 vx / vy | 0.3 / 0.3 m/s | 电控建议（80 RPM ≈ 0.5 m/s 的保守首值） |
| 固件限幅 wz | 1.0 rad/s | 电控建议（80 RPM ≈ 1.07 rad/s 的保守首值） |
| motion_control max_vx/vy | 0.3 / 0.3 | ✅ 已同步（`robot.yaml`，必须 ≤ 固件限幅） |
| motion_control max_wz | 1.0 | ✅ 已同步 |
| fake 模式同值 | 同上 | 电控要求「两边行为一致」；对应 A0.7 假固件限幅拒绝 |

> 即使限幅设高，代码最后一层 `Chassis_LimitWheelRPM` 也会把四轮按比例限到 80 RPM——轮速封顶是双重保险。

## 测试纪律（电控要求）

1. **先架空**：轮子离地，完整链路（握手→心跳→授权→CMD_VEL）先跑一遍；
2. **落地低速**：正式值 20-30%（即 ~0.1 m/s）起步；
3. **RPi 测试时关手柄**：手柄在线 = 自动命令无效；
4. **有人守急停**：任何自动运动测试必须有人手持急停跟在车旁；
5. **保留的其它保护**：堵转/超速/方向异常/控制超时/急停都还在，不受本次改动影响。

## 对应代码位置（算法侧）

- 握手：`robot_bridge/node.py` `_handshake_state`（READY 才收 CMD_VEL，`:321`）
- 心跳：`_send_heartbeat`（50Hz，`:342` 区域）
- communication_ok 门控：`_on_cmd_vel` 检查 `_communication_ok`（`:328`）
- physical_start：`_real_mechanism_ready` 检查 `STATUS_PHYSICAL_START`（`:447`）
- 限速：`motion_control` `max_vx/max_vy/max_wz`（`robot.yaml`，已对齐 0.3/0.3/1.0）
- 假固件限幅拒绝：`hardware_readiness.mock_communication_ok` + A0.7（待补行为测试）
