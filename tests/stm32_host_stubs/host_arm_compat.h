/*
 * 主机侧（gcc x86）编译 STM32 源码时用的 ARM 垫片 —— 第一步。
 *
 * 用法：`gcc -include host_arm_compat.h`（本目录必须在 -I 里），
 *       且被测源文件在包含完固件头之后 `#include "host_arm_compat_tail.h"`。
 *
 * 问题：`rpi_protocol.c` 的 `RPI_TakeVelocityCommand()` / `RPI_TakeSessionReset()`
 * 用 CMSIS 的 `__get_PRIMASK()` / `__disable_irq()` / `__enable_irq()` 做临界区。
 * 函数体是 ARM 内联汇编（mrs / cpsid i / cpsie i），x86 汇编器不认，
 * 且报错发生在编译之后的汇编阶段，`-w` 压不住。
 *
 * 本文件做两件事（都不能改 vendor 文件）：
 *   1. 把 `__STATIC_FORCEINLINE` 提前定义成普通 `static inline`。
 *      cmsis_gcc.h 里它是 `#ifndef` 保护的，所以能覆盖。去掉 always_inline 后，
 *      CMSIS 里**未被调用**的函数（`__get_CONTROL` 等十几个含内联汇编的函数）
 *      不再生成机器码——这是主要的报错来源。
 *   2. 把真正被调用的那三个名字用对象式宏改写掉，它们的函数体同样不再生成；
 *      空实现由 `host_arm_compat_tail.h` 在 CM4 头之后补上。
 *
 * 语义影响：主机上单线程执行，临界区退化为空操作。
 * 被测编码逻辑（封帧 / CRC16 / 20ms 调度判断）不涉及临界区，结论不受影响。
 * 真机（Keil / armcc / arm-none-eabi-gcc）不加载本文件，行为不变。
 */
#ifndef HOST_ARM_COMPAT_H
#define HOST_ARM_COMPAT_H

#if !defined(__arm__) && !defined(__ARM_ARCH)

#include <stdint.h>

/* 1. 未调用的 CMSIS 函数不再生成代码。 */
#ifndef __STATIC_FORCEINLINE
#define __STATIC_FORCEINLINE static inline
#endif

/*
 * 2. 被调用的三个名字改写掉（cmsis_gcc.h 是 #ifndef 保护的，能覆盖）。
 *
 * 注意：这里**不能**带括号（`#define __enable_irq()`）。
 * cmsis_gcc.h 用 `void __enable_irq(void)` 声明，带括号的宏会在定义处
 * 被预处理器当成"0 参数宏传了 1 个参数"而直接编译失败。
 * 不带括号则 CMSIS 的声明与函数体都被改写成占位名，可以编译通过。
 */
#define __get_PRIMASK          host_arm_get_primask_placeholder
#define __disable_irq          host_arm_disable_irq_placeholder
#define __enable_irq           host_arm_enable_irq_placeholder

#endif /* 非 ARM 编译器 */

#endif /* HOST_ARM_COMPAT_H */
