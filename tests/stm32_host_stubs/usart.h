/*
 * 主机侧（gcc x86）编译 rpi_protocol.c 用的替身头。
 *
 * 为什么需要：真机 `usart.h` 里是 CubeMX 生成的 `extern UART_HandleTypeDef huart7;`
 * 以及 MX_USART*_Init() 声明，那些需要完整的 HAL .c 才能链接。
 * 这里只保留 rpi_protocol.c 引用到的 huart7 声明（定义在测试 harness 里），
 * 从而让“封帧 → CRC → 入队”这段真源码可以在 PC 上被逐字节验证。
 *
 * 它不替代真机头文件，也不改变固件工程：只在 gcc 命令行里用 -I 排在前面。
 */
#ifndef __USART_H
#define __USART_H

#include "main.h"

extern UART_HandleTypeDef huart7;

#endif /* __USART_H */
