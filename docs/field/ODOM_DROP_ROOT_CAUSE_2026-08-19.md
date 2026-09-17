# ODOM 跳变根因分析：为什么只在树莓派控制时发生（2026-08-19）

> 关联：`RASPBERRY_PI_DEPLOYMENT_LOG_2026-08-18.md`（7.2 三个软件问题、7.3 待电控）。
> 结论一句话：**固件把 ODOM 帧扔了（TX 队列满静默丢弃），树莓派只是更严格地发现了这一点；扔帧只在树莓派控制时才发生，因为那时 USB 双向流量 + 树莓派读取延迟才同时存在。遥控器走 USART6 单向链路，无回程、无拥塞、且遥控优先级屏蔽树莓派命令，所以 odom 正常。**

---

## 1. 现象

- 固件侧：`rejected ODOM gap delta_ms=500`、落地 `/pose` 断流 1.6s（部署日志 7.3）。
- 触发条件：**只在树莓派控制时**。遥控器控制时 odom 正常。
- 同一时段 STATUS 看起来「50Hz 稳定」（min 17ms / max 22ms / std 1ms）。

## 2. 根因（代码链）

### 2.1 固件：ODOM/STATUS 共用 8 深 TX 队列，满则静默丢弃

`rpi_protocol.c`：

```c
#define RPI_TX_QUEUE_DEPTH  8U
static uint8_t RPI_SendFrame(...) {
    if (rpi_tx_count >= RPI_TX_QUEUE_DEPTH) return 0U;  // 队列满 → 直接扔
    ...
}
// 调用方全部忽略返回值：
(void)RPI_SendFrame(RPI_MSG_ODOM, ...);    // RPI_SendOdom
(void)RPI_SendFrame(RPI_MSG_STATUS, ...);  // RPI_SendStatus
```

### 2.2 固件：USB CDC 单缓冲异步发送，一次只飞一帧

`usbd_cdc_if.c`：

```c
uint8_t CDC_Transmit_FS(...) {
    if (hcdc->TxState != 0U) return USBD_BUSY;   // 上一帧没发完 → 忙
    ...
}
```

`TxState` 要等树莓派取走数据、USB IN 端点传输完成中断后才清零。**树莓派不读 → STM32 永远「忙」→ 新帧发不出去。**

`RPI_TxPump` 每主循环轮最多推进一帧，CDC 忙（BUSY）就放弃等下一轮：

```c
static void RPI_TxPump(void) {
    if (rpi_tx_active) {
        if (CDC_IsTxBusy_FS()) return;   // 树莓派没读 → 放弃这轮
        ...
    }
    ...
}
```

### 2.3 丢帧量级与观测吻合

- 树莓派控制时：STATUS 50Hz + ODOM 50Hz = **100 帧/秒** 灌向 USB（~2400 B/s）。
- 树莓派 4B 高负载（~200% CPU）→ `_tick` 读串口偶发延迟 → USB 主机缓冲满 → `TxState` 一直 busy → 8 深队列 **80ms 即积满**。
- 积满后每 20ms 新生成的 ODOM/STATUS 全被 `return 0U` 静默丢弃：
  - 丢 500ms ≈ **25 帧** → 对应 `rejected ODOM gap delta_ms=500`
  - 丢 1.6s ≈ **80 帧** → 对应 `/pose 断流 1.6s`

### 2.4 为什么只看到 ODOM 跳、STATUS 看着稳定

树莓派侧校验方式不同（`robot_bridge/node.py` + `mcu_time.py`）：

| 帧 | 校验方式 | 丢帧后果 |
|---|---|---|
| ODOM | 相邻帧 `mcu_tick_ms` 增量 >250ms 即 reject（`validate_mcu_tick`），**reject 时不更新 `last_decoded_odom_rx`** | 新鲜度断 → `/wheel_odom` 协方差置 unavailable → localization pose 断 → `LOCALIZATION_ERROR` 停车 |
| STATUS | 只看 0.3s 窗口内「最近收到时间」（`robot_status_communication_ok`） | 丢几帧不致命，下一帧到就刷新 → 看着「50Hz 稳定」 |

即：**STATUS 也在丢，只是树莓派查它查得不严；ODOM 查得严，一丢就现形。**

### 2.5 为什么遥控器控制时完全不跳

| | 遥控器（USART6） | 树莓派（USB CDC） |
|---|---|---|
| 物理链路 | 串口 UART，**单向收**（9600 波特） | USB 虚拟串口，**双向**（115200） |
| 回程 | **无**（STM32 从不往遥控器发）→ 无 TX 拥塞可能 | STATUS/ODOM 下行 ~2400 B/s → 有拥塞窗口 |
| 固件接收 | 单字节中断，收完即用，无积压 | USB 中断回调 → 环形缓冲 → 主循环批量解析 |
| 控制优先级 | `Remote_IsOnline()` → `REMOTE_MANUAL` 分支，**树莓派命令被丢弃** | `RPI_AUTO` 分支 |

遥控器模式下三个条件（USB 双向流量、树莓派读取延迟、严格增量校验）**一个都不存在**，所以 odom 正常。

## 3. 为什么不是「改协议」能解决的

- 帧**格式**不是根因：队列满丢帧与帧大小/格式无关；加序号只能「发现丢帧」不能「阻止丢帧」；改波特率对 USB CDC 无意义（实际速率由 USB 全速 12Mbps 决定）。
- 协议 V1 有逐字节冻结测试（`tests/test_serial_v1_acceptance.py`、`test_v1_payload_lengths_are_frozen`），改格式成本极高、收益为零。
- 值得动的是**发送机制**（可靠性策略），不是帧格式：丢帧优先级保护、泵出节奏、独立读线程——都不触碰 payload 布局。

## 4. 验证与修复计划

### 4.1 实锤（一次联调）

- **固件侧（电控）**：`RPI_SendFrame` 队列满处加丢帧计数器暴露到 Keil Watch：
  ```c
  if (rpi_tx_count >= RPI_TX_QUEUE_DEPTH) {
      rpi_tx_drop_count++;   // 新增
      return 0U;
  }
  ```
- **树莓派侧（我们）**：`cmd_vel_tx_log: true`（已配好）同时录 `rejected ODOM` 日志。
- 判定：丢帧计数 + `rejected ODOM` 时间对表 → 实锤「TX 队列静默丢帧」。对不上则转查主循环阻塞（TIM7 10µs 中断抢占等）。

### 4.2 树莓派侧缓解（G5.5 / G5.6，今天可做）

1. **G5.5 串口读取独立线程**：`_safe_serial_read` 挪出 `_tick`，独立线程读 + 内部缓冲，消除「读取延迟 = CPU 负载」的耦合。**最有效。**
2. **G5.6 `max_mcu_sample_gap_ms` 250→1000 A/B**：偶发 500ms 丢帧从 reject 变容忍，立消 LOCALIZATION_ERROR 停车；根治后调回。

### 4.3 固件侧根治（电控，不动协议格式）

1. **丢帧优先级保护**：队列满时保 STATUS/ODOM，优先丢心跳/ACK（心跳丢了无所谓，下一帧又来）。
2. **ODOM 发送挪进 TIM6 1ms 节拍中断**：与主循环轮询 + `RPI_TxPump` 每轮一帧解耦。
3. **`RPI_TxPump` 泵到 CDC busy 为止**（当前每轮最多一帧）。
4. 可选：TX 队列 8→32。

## 5. 给电控的反馈（可直接转发）

> 关于「odom 跳变」的结论：
> 1. **不是树莓派读慢了，是 STM32 把 ODOM 帧扔了**——USB 发送队列（8 深）满时 `RPI_SendFrame` 静默丢弃，树莓派只是按相邻帧时间戳（>250ms）更严格地发现了它。STATUS 也在丢，只是树莓派按 0.3s 窗口看，看不出来。
> 2. **只有树莓派控制时才丢**：树莓派控制时 STATUS+ODOM 各 50Hz 灌向 USB（~2400 B/s），加上树莓派 4B 高负载读串口偶发延迟 → USB 缓冲满 → CDC `TxState` 一直 busy → 队列积满 → 丢帧。遥控器走 USART6 单向串口，没有回程，不可能发生。
> 3. **请求电控配合实锤 + 根治**：
>    - 实锤：`RPI_SendFrame` 队列满处加 `rpi_tx_drop_count++` 暴露到 Watch，与树莓派 `rejected ODOM` 日志对时间；
>    - 根治（按收益排序）：① 队列满时保 STATUS/ODOM、丢心跳/ACK；② ODOM 发送挪进 TIM6 1ms 中断；③ `RPI_TxPump` 泵到 busy 为止；④ TX 队列 8→32。
> 4. **不需要改协议**：帧格式没问题（V1 已冻结），问题在发送机制。

## 6. 判别实验（把「命令流」和「USB 链路」分开）

1. **遥控器控制 + 树莓派同时开着录 odom**：odom 稳定 → 证明「树莓派在控制（发命令）」是触发条件；odom 也跳 → 说明只要 USB 双向流量在就会跳。
2. **树莓派只发心跳不发 CMD_VEL**：心跳仍占 USB 上行，命令停了。odom 不跳 → 拥塞主要来自下行回程 + 命令流量；还跳 → 心跳流量就够触发。
