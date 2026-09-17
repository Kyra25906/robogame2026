/*
 * usbd_cdc_if.h 的主机替身。
 * 真实头文件会拉进整个 USB 协议栈（usbd_cdc.h / usbd_def.h），
 * 本机只需要协议层用到的那两个东西。
 */
#ifndef __HOST_USBD_CDC_IF_STUB_H
#define __HOST_USBD_CDC_IF_STUB_H

#include <stdint.h>

#define USBD_OK   0U
#define USBD_BUSY 1U
#define USBD_FAIL 2U

uint8_t CDC_Transmit_FS(uint8_t *Buf, uint16_t Len);

#endif /* __HOST_USBD_CDC_IF_STUB_H */
