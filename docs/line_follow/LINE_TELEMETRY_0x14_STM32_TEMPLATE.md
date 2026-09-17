# STM32 侧 0x14 发送模板

电控在 `rpi_protocol.c` 中增加消息类型 `0x14`，payload 固定 21 字节，随后交给现有 `RPI_SendFrame()`。所有多字节字段小端序。

```c
#define RPI_MSG_LINE_TELEMETRY 0x14U

/* analog_valid=1 仅表示最近 500 ms 收到合法 $A 帧。 */
uint8_t RPI_SendLineTelemetry(uint32_t tick_ms,
                              const uint16_t analog[8],
                              uint8_t analog_valid)
{
    uint8_t payload[21];
    uint8_t i;

    payload[0] = (uint8_t)(tick_ms);
    payload[1] = (uint8_t)(tick_ms >> 8);
    payload[2] = (uint8_t)(tick_ms >> 16);
    payload[3] = (uint8_t)(tick_ms >> 24);
    for (i = 0U; i < 8U; ++i) {
        uint16_t value = (analog[i] <= 4095U) ? analog[i] : 4095U;
        payload[4U + 2U * i] = (uint8_t)value;
        payload[5U + 2U * i] = (uint8_t)(value >> 8);
    }
    payload[20] = (analog_valid != 0U) ? 1U : 0U;
    return RPI_SendFrame(RPI_MSG_LINE_TELEMETRY, payload, sizeof(payload));
}
```

每 20 ms 调用一次。掉线时仍发送，`analog_valid=0`；通道值保留上次采样值。不要在 STM32 侧计算偏差、PD 或巡线状态。

固定测试帧（序号 5）见 `docs/field/LINE_TELEMETRY_0x14_INTERFACE_ALIGNMENT_2026-08-19.md` 及 Python 协议测试。
