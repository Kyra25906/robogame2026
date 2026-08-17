# 树莓派—STM32 机械机构通信协议 v1.0

状态：三方签字后冻结  
范围：夹爪抓取/释放、升降绝对定位/回零、机械停止、整车软件急停  
原则：STM32 拥有执行器最终安全控制权；同一时刻只执行一个普通机械动作；STOP 和急停优先。

## 1. 双方职责

树莓派负责发送高层动作、生成命令编号、持续发送心跳、等待终态及处理重试、失败和 STM32 重启；不得直接下发电机 PWM、方向或电流。

STM32 负责检查命令、控制执行器、读取位置/限位/电流/方块传感器、判断动作结果，并独立处理越界、堵转、过流、超时、通信中断和急停。树莓派参数不得覆盖 STM32 本地安全上限。

机械和电控共同冻结行程、速度、电流、位置容差、动作时间及抓取/释放/堵转判据。

## 2. 传输层与通用帧

| 项目 | 规定 |
|---|---|
| 连接 | USB CDC 虚拟串口或 USB 转 UART |
| UART | 115200 bit/s，8N1，无流控 |
| 多字节整数 | 小端；有符号数使用二进制补码 |
| 高度/时间单位 | mm / ms |

| 偏移 | 大小 | 字段 | 规定 |
|---:|---:|---|---|
| 0 | 2 | `sof` | 固定 `AA 55` |
| 2 | 1 | `version` | `0x01` |
| 3 | 1 | `message_type` | 消息类型 |
| 4 | 2 | `frame_sequence` | 发送方逐帧递增，回绕后归零 |
| 6 | 2 | `payload_length` | payload 字节数 |
| 8 | N | `payload` | 消息内容 |
| 8+N | 2 | `crc16` | 低字节在前 |

总帧长为 `10 + payload_length`。

CRC 使用 CRC-16/CCITT-FALSE：`poly=0x1021`、`init=0xFFFF`、不反射、`xorout=0`。覆盖从第一个 `AA` 到 payload 最后一字节，不含 CRC 自身。CRC 错误帧静默丢弃。

`frame_sequence` 只用于通用 ACK 对应。重发同一机械命令时使用新的帧编号，但保持 `command_id` 和 payload 不变。

## 3. 消息类型

| 类型 | 方向 | 含义 |
|---:|---|---|
| `0x02` | 树莓派→STM32 | HEARTBEAT |
| `0x03` | 树莓派→STM32 | HELLO |
| `0x12` | STM32→树莓派 | 整车 STATUS（含现有 `boot_id`） |
| `0x13` | STM32→树莓派 | 通用 ACK |
| `0x20` | 树莓派→STM32 | MECHANISM_COMMAND |
| `0x21` | STM32→树莓派 | MECHANISM_STATUS |
| `0x22` | 树莓派→STM32 | EMERGENCY_STOP |

`0x20` 中的 STOP 只停止机械机构；`0x22` 停止底盘、夹爪和升降；物理急停优先级最高。

## 4. 会话和心跳

1. 打开串口后必须先发送 HELLO。HELLO payload 为一字节 `01`。
2. STM32 回复 `ACK_OK` 后，普通机械命令才允许执行。
3. 每条合法 HELLO 建立新会话，并使旧会话的未完成命令和去重缓存失效。
4. 树莓派每 50 ms 发送 HEARTBEAT，payload 沿用现有 4 字节格式。
5. STM32 超过 150 ms 未收到合法心跳或现有底盘保活帧即判定通信中断。机械命令不代替周期心跳。
6. 通信中断后停止机构，当前命令进入 `FAILED/9003`，不自动续执行。恢复后必须重新 HELLO。
7. 树莓派发现 `0x12` 中 `boot_id` 改变时，废弃所有未完成命令并重新 HELLO；升降视为未回零。

## 5. 通用 ACK（0x13）

payload 固定 4 字节：

| 偏移 | 大小 | 字段 |
|---:|---:|---|
| 0 | 2 | `acknowledged_sequence: uint16` |
| 2 | 1 | `result: uint8` |
| 3 | 1 | `supported_version: uint8` |

`result`：0=OK，1=BAD_VERSION，2=UNKNOWN_TYPE，3=BAD_LENGTH，4=BAD_PARAMETER，5=BAD_STATE。

ACK 只表示协议帧是否被接收，不表示动作成功。对 `0x20`：

- 长度正确且能完整解析：先回 `ACK_OK`；语义错误通过 `0x21 REJECTED` 表示。
- payload 长度错误：回 `ACK_BAD_LENGTH`，不回 `0x21`。
- CRC 错误：静默丢弃。
- 收到 ACK_OK 但未收到 `0x21 SUCCEEDED`，不得认为动作成功。

## 6. 动作定义

| operation | 名称 | parameter | 成功判据 |
|---:|---|---|---|
| 1 | GRAB | 必须为 0 | 夹爪达到抓取条件，且方块存在判据成立 |
| 2 | RELEASE | 必须为 0 | 夹爪达到打开条件，且方块已离开 |
| 3 | LIFT_ABS | 目标高度 mm | 位置进入本地容差并稳定规定时间 |
| 4 | 保留 | — | v1.0 禁止使用 |
| 5 | STOP | 必须为 0 | 机构已安全停止 |
| 6 | HOME | 必须为 0 | 底部回零条件成立，高度设为 0 mm |

GRAB/RELEASE 只控制夹爪，不隐含升降；底盘后退不属于本协议。

## 7. MECHANISM_COMMAND（0x20）

payload 固定 12 字节：

| 偏移 | 大小 | 类型 | 字段 | 规定 |
|---:|---:|---|---|---|
| 0 | 2 | `uint16` | `command_id` | 会话内逻辑命令编号 |
| 2 | 1 | `uint8` | `operation` | 见第 6 节 |
| 3 | 1 | `uint8` | `flags` | v1.0 必须为 0 |
| 4 | 4 | `int32` | `parameter` | LIFT_ABS 必须非负 |
| 8 | 4 | `uint32` | `timeout_ms` | 0 表示使用 STM32 默认值 |

有效超时：

```text
timeout_ms == 0:
    effective_timeout = local_default_timeout(operation)
otherwise:
    effective_timeout = min(timeout_ms, local_safe_max_timeout(operation))
```

超时从进入 RUNNING 时开始。STM32 必须在驱动执行器前完成全部参数和安全检查。

## 8. MECHANISM_STATUS（0x21）

payload 固定 10 字节：

| 偏移 | 大小 | 类型 | 字段 |
|---:|---:|---|---|
| 0 | 2 | `uint16` | `command_id` |
| 2 | 1 | `uint8` | `operation` |
| 3 | 1 | `uint8` | `state` |
| 4 | 2 | `uint16` | `error_code` |
| 6 | 4 | `uint32` | `duration_ms` |

`duration_ms` 从进入 RUNNING 起计时；ACCEPTED 和 REJECTED 时为 0。

| state | 名称 | 终态 | 含义 |
|---:|---|---|---|
| 1 | ACCEPTED | 否 | 语义和安全前置检查通过 |
| 2 | RUNNING | 否 | 执行器已开始动作 |
| 3 | SUCCEEDED | 是 | 成功判据满足 |
| 4 | FAILED | 是 | 开始执行后失败 |
| 5 | CANCELLED | 是 | 被 STOP、急停或会话替换终止 |
| 6 | REJECTED | 是 | 未驱动执行器即拒绝 |

合法转换：

```text
ACCEPTED -> RUNNING -> SUCCEEDED | FAILED | CANCELLED
```

拒绝命令直接进入 REJECTED。终态不可改变。SUCCEEDED 的错误码必须为 0；其他终态必须携带相符的非零错误码。

## 9. 时序、超时和重试

正常时序：

```text
树莓派 -> 0x20
STM32    -> 0x13 ACK_OK
STM32    -> 0x21 ACCEPTED
STM32    -> 0x21 RUNNING
STM32    -> 0x21 SUCCEEDED / FAILED / CANCELLED
```

STM32 在状态变化时立即发送 `0x21`，RUNNING 期间每 100 ms 报告一次。终态主动发送一次，并保存在命令缓存中。

树莓派规则：

- 发送后 100 ms 未收到 ACK，可用新 `frame_sequence` 重发，最多 3 次。
- 收到 ACK_OK 后 100 ms 未收到 `0x21`，重发完全相同的原命令查询状态。
- RUNNING 期间 300 ms 未收到任何对应 `0x21`，进入通信异常处理，不得假设成功。
- 本地最终等待上限建议为 `effective_timeout + 500 ms`。

## 10. 并发、STOP 和命令幂等

- 同时只允许一条普通命令处于 ACCEPTED/RUNNING。
- 忙时收到新的 GRAB、RELEASE、LIFT_ABS 或 HOME，返回 `REJECTED/6`。
- STOP 可打断任何机械动作，不受忙状态限制。
- 收到 STOP 后，STM32 在一个机械控制周期内停止升降运动并进入制动/保持，停止夹爪开合但维持安全夹持。
- 原动作先进入 `CANCELLED/8`；STOP 自身按 `ACCEPTED -> RUNNING -> SUCCEEDED` 返回。空闲时 STOP 也成功。

`command_id` 仅在当前 HELLO 会话内有效：

- 新逻辑动作使用新编号；重试保持编号和 payload 完全一致。
- STM32 至少缓存当前命令和最近 16 条终态，或保留终态 60 s，以先到条件为准。
- 相同编号且 payload 相同：不得重复驱动，返回缓存的当前/最终状态。
- 相同编号但 payload 不同：新命令返回 `REJECTED/7`，原命令不受影响。
- 树莓派在缓存窗口内不得复用编号。

## 11. 回零和高度定位

STM32 上电或复位后升降为“未回零”。未回零时允许 STOP、HOME、GRAB、RELEASE；LIFT_ABS 返回 `REJECTED/2001`。

HOME 流程：

```text
低速向下
-> 检测底部回零条件
-> 立即停止并制动
-> 当前高度设为 0 mm
-> SUCCEEDED
```

回零超时、方向与限位矛盾、上下限位同时有效或堵转时立即停止并失败。

LIFT_ABS 必须与 STM32 本地最小/最大高度比较。越界命令在电机通电前拒绝。触发运动方向上的硬限位时立即停止，不得继续顶压等待超时。位置容差和稳定时间使用本地冻结参数。

## 12. 停止与急停策略

| 事件 | 升降 | 夹爪 | 锁存 |
|---|---|---|---|
| 机械 STOP | 停止并制动/保持 | 停止开合，维持当前安全夹持 | 否 |
| 通信中断 | 停止并制动/保持 | 保持夹持，不主动释放负载 | 需重新 HELLO |
| 软件急停 | 立即停止驱动并使用制动/自锁 | 停止运动；仅在安全电流内保持 | 是 |
| 物理急停 | 按硬件安全回路执行 | 按硬件安全回路执行 | 是，最高优先级 |

机械设计必须保证停止时不会主动掉落负载；若长时保持需要大电流，应采用机械自锁、制动或受限保持电流。

## 13. EMERGENCY_STOP（0x22）

payload 长度固定为 0。有效急停不依赖 HELLO 已建立，重复发送必须幂等安全。

STM32 收到后：

1. 锁存软件急停；
2. 清除底盘速度指令；
3. 安全停止升降和夹爪；
4. 当前机械命令进入 `CANCELLED/9001`；
5. 回复 `ACK_OK`。

v1.0 不定义远程急停解除消息。软件和物理急停只能通过本地明确的人工复位流程解除，HELLO、心跳和普通命令均不得自动解除。解除后必须重新 HELLO，升降仍按未回零处理。

## 14. 错误码

### 通用命令

| 码 | 含义 | 终态 |
|---:|---|---|
| 0 | 无错误 | SUCCEEDED |
| 4 | 参数越界或保留位非零 | REJECTED |
| 5 | 当前状态不允许 | REJECTED |
| 6 | 机构忙 | REJECTED |
| 7 | command_id 相同但 payload 不同 | REJECTED |
| 8 | 被机械 STOP 取消 | CANCELLED |
| 9 | 未知/保留动作 | REJECTED |

协议版本、消息类型和长度错误使用通用 ACK，不使用 `0x21`。

### 夹爪

| 码 | 含义 |
|---:|---|
| 1001 | 未抓到方块 |
| 1002 | 夹爪堵转 |
| 1003 | 夹爪过流 |
| 1004 | 夹爪位置/限位异常 |
| 1005 | 释放后方块仍未离开 |
| 1006 | 夹爪动作超时 |

### 升降

| 码 | 含义 |
|---:|---|
| 2001 | 尚未回零 |
| 2002 | 动作超时 |
| 2003 | 堵转 |
| 2004 | 过流 |
| 2005 | 限位信号异常 |
| 2006 | 目标高度超过本地范围 |
| 2007 | 回零失败 |
| 2008 | 位置反馈异常 |

### 安全和会话

| 码 | 含义 |
|---:|---|
| 9001 | 急停导致终止 |
| 9002 | 电池电压过低 |
| 9003 | 通信中断 |
| 9004 | STM32 重启，旧命令作废 |
| 9005 | 新 HELLO 取消旧命令 |
| 9006 | 其他安全条件不满足 |

未知非零错误码一律按失败处理，并保留原始值用于日志。

## 15. 发送优先级与队列

- STOP、急停和机械终态优先于 ODOM、IMU、通用 STATUS 和周期 RUNNING 报告。
- 队列满时可丢弃周期 RUNNING 报告，不得静默丢弃终态、急停 ACK 或安全故障。
- 最新终态必须保留在命令缓存中，首次发送失败后仍可由重复命令查询。
- CRC 错误、半帧、粘包、拆包、解析失步和接收缓冲区溢出不得触发执行器动作。

## 16. 参考载荷定义

```c
#pragma pack(push, 1)
typedef struct {
    uint16_t command_id;
    uint8_t  operation;
    uint8_t  flags;
    int32_t  parameter;
    uint32_t timeout_ms;
} RpiMechanismCommandPayload; /* 12 bytes */

typedef struct {
    uint16_t command_id;
    uint8_t  operation;
    uint8_t  state;
    uint16_t error_code;
    uint32_t duration_ms;
} RpiMechanismStatusPayload; /* 10 bytes */
#pragma pack(pop)
```

正式实现应使用显式 LE 读写函数逐字段编解码，不得依赖结构体对齐或本机字节序。

升降到 120 mm 的 payload 示例：

```text
command_id = 2, operation = 3, flags = 0
parameter = 120, timeout_ms = 4000

02 00 03 00 78 00 00 00 A0 0F 00 00
```

若 `frame_sequence=0x0065`，含 CRC 的完整帧为：

```text
AA 55 01 20 65 00 0C 00 02 00 03 00 78 00 00 00 A0 0F 00 00 90 1C
```

成功、耗时 850 ms 的状态 payload：

```text
02 00 03 03 00 00 52 03 00 00
```

## 17. 上电测试前必须冻结的本地参数

这些参数不通过串口逐次下发，须固化在 STM32 配置中：

| 参数 | 冻结值 |
|---|---|
| 升降最低/最高高度 | ____ / ____ mm |
| 位置容差/稳定时间 | ____ mm / ____ ms |
| 回零速度/最长时间 | ____ / ____ ms |
| 升降速度/最长时间 | ____ / ____ ms |
| 升降堵转和过流判据 | ____ |
| 夹爪打开/闭合最长时间 | ____ / ____ ms |
| 夹爪打开、抓取成功判据 | ____ |
| 夹爪堵转和过流判据 | ____ |
| 通信中断后的安全保持电流/时间 | ____ |
| 机械控制周期 | ____ ms |
| 物理急停切断范围和制动方式 | ____ |

未冻结前只允许无动力协议联调，不允许自动实物抓取。

## 18. 最低验收用例

1. 正常执行 GRAB、RELEASE、HOME 和 LIFT_ABS。
2. 未回零拒绝 LIFT_ABS；高度越界时电机不动并返回 `REJECTED/2006`。
3. 忙时拒绝新普通命令。
4. 相同命令重发不重复动作；相同编号不同内容被拒绝。
5. STOP 可在各动作阶段打断，原命令返回 CANCELLED。
6. 停止心跳后 150 ms 内进入安全状态，且不主动掉落负载。
7. 软件急停停止全部执行器，重复急停安全；HELLO 和普通命令不能解除急停。
8. STM32 重启后旧命令作废，升降重新回零。
9. CRC 错误、半帧、粘包、拆包和缓冲溢出不引起误动作。
10. 堵转、过流、限位矛盾及位置反馈丢失均可独立安全失败。
11. 发送队列高负载时，STOP、急停和终态不被周期状态挤掉。

## 19. 冻结规则

双方应共享消息常量和固定黄金帧测试，覆盖 CRC、大小端、有符号参数、状态转换、命令去重、心跳中断、STOP、急停和重启。

任何字段大小、偏移、枚举、CRC、安全语义或状态机变更都必须升级协议文档并重新执行双方固定帧测试。
