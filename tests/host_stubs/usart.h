/*
 * usart.h 的主机替身。
 * rpi_protocol.c 只在 UART7 链路（RPI_LINK_USE_UART7=1）时才用它，
 * 当前配置走 USB CDC，因此这里可以是空的。
 */
#ifndef __HOST_USART_STUB_H
#define __HOST_USART_STUB_H

#include "stm32f4xx_hal.h"

#endif /* __HOST_USART_STUB_H */
