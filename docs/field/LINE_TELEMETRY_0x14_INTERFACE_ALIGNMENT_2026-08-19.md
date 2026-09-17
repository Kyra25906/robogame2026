# 巡线遥测 0x14 接口对齐（算法 ↔ 电控，2026-08-19）

> 状态：**草案，待电控确认**。冻结前不实现固件上送、不写上位机解码（项目红线：不猜测载荷）。
> 关联：`STM32_SERIAL_PROTOCOL_V1.md`（V1 协议，0x14 尚不存在）、`docs/line_follow/README.md` 与 `CALIBRATION_AND_HARDWARE.md`（上位机巡线算法与标定）、`docs/TODO_AND_ISSUES.md`「巡线链路核对记录」。
> 分工（已定）：巡线 = 上位机决策与控制，**下位机只做八路灰度采集上送**。偏差计算、PD 纠偏、状态机都在上位机 `robogame_core/line_follow.py`（已实现，31 项测试通过）。

---

## 0. 接口结论（建议，待电控确认）

| 决策 | 建议 | 理由 |
|---|---|---|
| 上报内容 | **8 路 12bit 模拟原始值**（0~4095），**不上报数字 0/1** | 标定需要原始值；上位机加权偏差算法需要原始值；阈值调参在上位机配置文件，不重新烧录 |
| 消息类型 | 新增 `0x14 LINE_TELEMETRY`（STM32 → 树莓派） | 0x13 之后 0x14~0x1F 空闲，与 0x10/0x11/0x12 遥测同族 |
| 上报频率 | **50 Hz**（20 ms 一次，与 STATUS 同周期） | 对齐上位机 `line_follow.py` 控制周期 `dt=0.02` |
| 掉线行为 | **继续发 0x14，但 `flags.bit0=0` 显式标不可信** | 让上位机区分「模块掉线」与「树莓派收不到帧」；避免把旧模拟值误当新值 |
| 是否上送数字值 | **不上送**（保留 `line_digital[8]` 作调试/冗余，Keil Watch 用） | 数字值 = 原始值 × 阈值，上位机自己算，多上送浪费带宽 |

---

## 1. 现状（仓库证据）

- 下位机采集已就绪：工作树路径 `Four_Motor_PID_Test_1/Four_Motor_PID_Test/Core/Src/line_sensor.c`（⚠️ `Four_Motor_PID_Test_1/` 被 `.gitignore:46` 忽略，Git 里查不到这个目录，只有本机磁盘上有；旧文写的 `Four_Motor_PID_Test (3)/` 在本机不存在）
  - UART7（PE8=TX / PE7=RX，115200），发 `$0,1,1#` 使能模块持续回传；
  - 解析 `$A,x1:4096,x2:4096,...#`（12bit 模拟值 0~4095）与 `$D,x1:0,x2:0,...#`（数字 0/1）帧；
  - 已接入 `main.c`（`LineSensor_Init()` / 主循环 `LineSensor_Update()` / UART 回调）与 `stm32f4xx_it.c` UART7 中断；
  - `line_sensor.h` 明说「仅 Keil Watch 观察，不接入任何控制」——**只采集，未上送**。
- `rpi_protocol.c` 现有消息：0x01/02/03（下行）、0x10/11/12/13（遥测/ACK）、0x20/21/22（机构），**无巡线消息**；`STM32_SERIAL_PROTOCOL_V1.md` 无巡线遥测定义。
- 上位机 `line_follow.py` 接口：`compute_deviation_weighted(raw_values, threshold)` 吃 **0..1 归一化原始值**，归一化公式 `(raw - white_min)/(black_max - white_min)` 需要每路黑白原始参考值——都由上位机用本遥测通道的数据标定，电控无需上报标定值。

## 2. 建议的 0x14 载荷定义（21 字节）

消息：`0x14 LINE_TELEMETRY`，方向 STM32 → 树莓派，建议 50 Hz。帧结构沿用 V1 通用帧（`AA 55 | v1 | 0x14 | seq:u16 | len:u16 | payload | crc16`），所有多字节小端序、1 字节对齐。

| 偏移 | 长度 | 类型 | 字段 | 说明 |
|---:|---:|---|---|---|
| 0 | 4 | u32 | mcu_tick_ms | STM32 单调毫秒计数（`HAL_GetTick()`），与 0x10/0x11/0x12 一致；上位机 `max_mcu_sample_gap_ms` 连续性检查直接复用 |
| 4 | 16 | u16×8 | ch0..ch7 | 8 路 12bit 原始值 0..4095，直接转发 `line_analog[0..7]`；本帧无效时保持上次值且 `flags.bit0=0` |
| 20 | 1 | u8 | flags | 见下 |

flags：

| 位 | 名称 | 1 的含义 |
|---:|---|---|
| 0 | analog_valid | 本帧模拟值有效：最近 500 ms 内收到过合法 `$A` 帧（即 `line_online=1` 且最近帧为 `$A`） |
| 1~7 | reserved | V1 必须为 0 |

> 说明：模块掉线时 `line_analog[]` 保留旧值（`line_sensor.c` 解析失败不更新），因此**必须靠 flags.bit0 表示有效性**，上位机不得在 `flags.bit0=0` 时使用本帧 ch 值。

**电控可直接从 `line_sensor.c` 取数**：payload[4..19] = `line_analog[0..7]` 原样拷贝，flags.bit0 = `line_online && 最近收到的是 $A 帧`。建议固件侧把「最近一帧类型」记下来（`line_sensor.c` 当前只存值不存帧类型，需加 1 个变量或复用 `line_frame_count` 判定）。

## 3. 电控侧实现清单（待确认后执行）

1. 确认模块已装车、供电/电平正常，`line_online=1`、8 路值随位置变化；
2. `rpi_protocol.c` 新增 `RPI_MSG_LINE_TELEMETRY 0x14` + `RPI_SendLineTelemetry(...)`，用现有 `RPI_SendFrame()` 入队（TX 队列/CRC/seq 全复用，payload 21 字节 << 64 字节帧上限）；
3. 主循环按 20 ms 周期调用（与 STATUS 同周期调度）；
4. 保留 `line_digital[8]` 不上送（调试用）。

## 4. 待电控确认的问题

1. 八路巡线模块**是否已装车**？供电/接口电平（3.3V / 5V）？
2. 模块实际回传频率多少 Hz？（`$A` 帧多久一帧，确认能否支撑 50 Hz 上送）
3. 模拟值方向：黑线时接近 0 还是接近 4095？（不阻塞冻结，标定流程可解，但确认可减少现场调试）
4. 消息编号 `0x14` 是否可接受？（当前协议 0x13 之后空闲）
5. 模块使能命令 `$0,1,1#` 是否需周期性重发？（`line_sensor.c` 已有 1s 超时重发逻辑，请确认与模块行为一致）
6. `$D`（数字）帧是否继续解析并保留？（建议保留作调试冗余，不上送）

## 5. 电控实现后算法侧要做的（对齐确认）

- `robot_bridge` StreamDecoder 增加 `0x14` 路由与解码 + 固定十六进制测试向量（参照 0x10/0x11 的既有测试风格）；
- 发布 `/line_sensor` 话题（8×u16 原始值 + flags + mcu_tick_ms），复用现有 `max_mcu_sample_gap_ms` 新鲜度检查；`flags.bit0=0` 时标记数据不可信；
- `line_follow.py` 接入 `/line_sensor`（标定归一化 → 偏差 → PD 纠偏输出），再接入 `motion_control` 巡线路段类型（`SegmentKind.LINE_FOLLOW`，已有枚举未接逻辑）；
- 真车贴线联调（低速贴线、出线恢复、交叉口行为验收）。

## 6. 一句话

> **下位机只把 8 路 12bit 模拟原始值 + `mcu_tick_ms` + 1 字节有效标志按 0x14 以 50 Hz 上送（21 字节 payload），不上报数字值、不判断黑白、不算偏差。标定与全部算法在上位机。请电控确认第 4 节 6 个问题，确认后固件照此实现，上位机同步写解码。**
