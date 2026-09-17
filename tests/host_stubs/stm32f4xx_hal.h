/*
 * HAL 层的主机替身（只用于 Windows/Linux 上的 gcc 编译验证）。
 *
 * 为什么替换的是 stm32f4xx_hal.h 而不是 main.h：
 * 引号 include 会先按"包含者所在目录"查找。Core/Inc/main.h 里写的是
 * `#include "stm32f4xx_hal.h"`，Core/Inc 下没有这个文件，于是落到 -I 列表，
 * 命中本文件。这样一来 **main.h / arm.h / remote.h / safety.h 全都是真实原件**，
 * 只有 HAL 类型和函数被顶替 —— 测试跑的是真固件代码，不是复制品。
 *
 * 本文件只声明，不定义；定义在 arm_auto_test_main.c 里。
 */
#ifndef __HOST_STM32F4XX_HAL_STUB_H
#define __HOST_STM32F4XX_HAL_STUB_H

#include <stdint.h>
#include <stddef.h>

/* ---- GPIO ---- */
typedef struct
{
    volatile uint32_t MODER;
    volatile uint32_t BSRR;
} GPIO_TypeDef;

extern GPIO_TypeDef host_gpioa;
extern GPIO_TypeDef host_gpioi;
extern GPIO_TypeDef host_gpiod;

#define GPIOA (&host_gpioa)
#define GPIOI (&host_gpioi)
#define GPIOD (&host_gpiod)

#define GPIO_PIN_0   ((uint16_t)0x0001U)
#define GPIO_PIN_2   ((uint16_t)0x0004U)
#define GPIO_PIN_3   ((uint16_t)0x0008U)
#define GPIO_PIN_7   ((uint16_t)0x0080U)
#define GPIO_PIN_14  ((uint16_t)0x4000U)
#define GPIO_PIN_15  ((uint16_t)0x8000U)

#define GPIO_PIN_RESET 0U
#define GPIO_PIN_SET   1U

#define GPIO_MODE_INPUT         0U
#define GPIO_MODE_OUTPUT_PP     1U
#define GPIO_NOPULL             0U
#define GPIO_SPEED_FREQ_LOW     0U
#define GPIO_SPEED_FREQ_VERY_HIGH 3U

typedef struct
{
    uint32_t Pin;
    uint32_t Mode;
    uint32_t Pull;
    uint32_t Speed;
} GPIO_InitTypeDef;

void HAL_GPIO_Init(GPIO_TypeDef *port, GPIO_InitTypeDef *init);
void HAL_GPIO_WritePin(GPIO_TypeDef *port, uint16_t pin, uint32_t state);

/* ---- RCC ---- */
typedef struct
{
    volatile uint32_t AHB1ENR;
    volatile uint32_t CSR;
} RCC_TypeDef;

extern RCC_TypeDef host_rcc;
#define RCC (&host_rcc)

#define RCC_AHB1ENR_GPIOAEN ((uint32_t)0x00000001U)
#define RCC_AHB1ENR_GPIOIEN ((uint32_t)0x00000200U)
#define RCC_AHB1ENR_GPIODEN ((uint32_t)0x00000008U)
#define RCC_CSR_LSION       ((uint32_t)0x00000001U)
#define RCC_CSR_LSIRDY      ((uint32_t)0x00000002U)

#define __HAL_RCC_GPIOA_CLK_ENABLE()  do { RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN; } while (0)
#define __HAL_RCC_GPIOI_CLK_ENABLE()  do { RCC->AHB1ENR |= RCC_AHB1ENR_GPIOIEN; } while (0)
#define __HAL_RCC_GPIOD_CLK_ENABLE()  do { RCC->AHB1ENR |= RCC_AHB1ENR_GPIODEN; } while (0)
#define __HAL_RCC_TIM7_CLK_ENABLE()   do { } while (0)

/* ---- TIM7 ---- */
typedef struct
{
    volatile uint32_t CR1;
    volatile uint32_t DIER;
    volatile uint32_t SR;
    volatile uint32_t EGR;
    volatile uint32_t PSC;
    volatile uint32_t ARR;
} TIM_TypeDef;

extern TIM_TypeDef host_tim7;
#define TIM7 (&host_tim7)

#define TIM_EGR_UG  ((uint32_t)0x00000001U)
#define TIM_DIER_UIE ((uint32_t)0x00000001U)
#define TIM_CR1_CEN ((uint32_t)0x00000001U)

/* ---- NVIC ---- */
#define TIM7_IRQn 55
void HAL_NVIC_SetPriority(int irq, uint32_t preempt, uint32_t sub);
void HAL_NVIC_EnableIRQ(int irq);

/* ---- 其他模块替身（arm.c 只用到这些） ---- */
typedef struct { int unused; } UART_HandleTypeDef;

uint32_t HAL_GetTick(void);

void Error_Handler(void);

/* ---- CMSIS 内核内建（主机上没有中断，用普通变量模拟 PRIMASK） ---- */
extern uint32_t host_primask;

#define __get_PRIMASK() (host_primask)
#define __disable_irq() do { host_primask = 1U; } while (0)
#define __enable_irq()  do { host_primask = 0U; } while (0)

#endif /* __HOST_STM32F4XX_HAL_STUB_H */
