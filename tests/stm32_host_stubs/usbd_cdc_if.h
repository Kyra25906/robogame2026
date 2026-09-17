/*
 * 主机侧（gcc x86）编译 rpi_protocol.c 用的替身头。
 *
 * 真机 `usbd_cdc_if.h` 依赖整个 USB Device 中间件（usbd_cdc.h / usbd_def.h），
 * 为了把 STM32 侧的 0x14 编码在 PC 上跑起来，只保留 rpi_protocol.c 用到的
 * CDC_Transmit_FS() / USBD_* 返回码；实现在测试 harness 的 recorder 里。
 */
#ifndef __USBD_CDC_IF_H
#define __USBD_CDC_IF_H

#include <stdint.h>

#define USBD_OK   0U
#define USBD_BUSY 1U
#define USBD_FAIL 2U

uint8_t CDC_Transmit_FS(uint8_t *Buf, uint16_t Len);

#endif /* __USBD_CDC_IF_H */
