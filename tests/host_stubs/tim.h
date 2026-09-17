/*
 * tim.h 的主机替身：arm.c 会 `#include "tim.h"`，但它只用寄存器直接操作 TIM7，
 * 不需要 tim.h 里的任何句柄。真实 tim.h 会拉进一堆 HAL 类型，因此用这个空壳顶替
 * （本目录在 -I 顺序里排在 Core/Inc 前面）。
 */
#ifndef __HOST_TIM_STUB_H
#define __HOST_TIM_STUB_H

#endif /* __HOST_TIM_STUB_H */
