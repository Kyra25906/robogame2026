/*
 * 主机侧 ARM 垫片 —— 第二步（必须在固件头之后包含）。
 *
 * 阶段一把 __get_PRIMASK / __disable_irq / __enable_irq 改写成占位名，
 * 使 CMSIS 的内联汇编函数体不再生成；这里提供同名**函数式宏**空实现。
 *
 * 必须先 #undef：占位名是对象式宏，直接定义同名函数式宏会被预处理器
 * 判为“给对象式宏传参”而报错。
 *
 * 若把本文件放在 CMSIS 头之前包含，`__disable_irq(void)` 这类声明会被
 * 当成“给 0 参数宏传了 1 个参数”——顺序不能颠倒。
 */
#ifndef HOST_ARM_COMPAT_TAIL_H
#define HOST_ARM_COMPAT_TAIL_H

#if !defined(__arm__) && !defined(__ARM_ARCH)

#include <stdint.h>

#undef __get_PRIMASK
#undef __disable_irq
#undef __enable_irq

#define __get_PRIMASK()   (0U)
#define __disable_irq()   ((void)0)
#define __enable_irq()    ((void)0)

#endif /* 非 ARM 编译器 */

#endif /* HOST_ARM_COMPAT_TAIL_H */
