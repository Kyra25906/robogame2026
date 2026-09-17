/*
 * STM32 侧 0x14 巡线遥测编码的主机侧（gcc x86）验证 harness。
 *
 * 目的：把**没有改动过**的固件源码 `Core/Src/rpi_protocol.c` 原样编译进来，
 * 真实调用 RPI_SendLineTelemetry()，再把 CDC_Transmit_FS() 收到的字节
 * 转成十六进制打印。这样 Python 侧就能拿它和冻结的线上向量逐字节比对。
 *
 * 覆盖的真源码：RPI_SendLineTelemetry() 的 21 字节拼接 → RPI_SendFrame()
 * 的帧头/序号/长度/CRC16-CCITT-FALSE → RPI_TxPump() 的 USB 出队路径。
 * 被替身顶掉的只有硬件：huart7、CDC_Transmit_FS()、HAL_GetTick()、
 * 以及中断相关的 HAL 宏（见 HAL_Delay / __disable_irq 等实现）。
 *
 * 能证明：编码逻辑在 PC 上产出与协议规定完全一致的字节。
 * 不能证明：真机 USB CDC 实传、时序、丢帧，以及任何硬件行为。
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "main.h"
#include "line_sensor.h"
#include "rpi_protocol.h"
#include "usbd_cdc_if.h"

/*
 * CMSIS 头已经处理完，此时把阶段一改写的三个名字换成主机上的空实现。
 * 顺序不能颠倒（见 host_arm_compat_tail.h 的说明）。
 */
#include "host_arm_compat_tail.h"

UART_HandleTypeDef huart7;

#define RECORDER_CAPACITY 16U

static uint8_t recorded[RECORDER_CAPACITY][256];
static uint16_t recorded_length[RECORDER_CAPACITY];
static uint16_t recorded_count = 0U;

/* 调度路径用：最近一帧 0x14 的内容（0x12 与 0x14 会交替出现）。 */
static uint8_t last_type_14_frame[256];
static uint16_t last_type_14_length = 0U;
static uint8_t recorded_type_14 = 0U;

static uint32_t fake_tick_ms = 0U;

uint8_t CDC_Transmit_FS(uint8_t *Buf, uint16_t Len);
uint8_t CDC_IsTxBusy_FS(void);


/* ---------- 被顶掉的硬件 ---------- */

/*
 * 机械臂链接期桩。
 *
 * 为什么需要：`-O0` 会把 `rpi_protocol.c` 里**未被调用**的静态函数
 * `RPI_HandleMechanismCommand()` 也生成出来，它引用 `Arm_Auto*`。
 * 这些符号在本测试的调用路径上永远不会被执行（被测函数是
 * `RPI_SendLineTelemetry()` 与 20ms 调度），这里只提供链接所需的最小定义。
 *
 * 按固件 `arm.h` 的签名声明；返回 0 表示"未接受"，不会掩盖任何编码问题。
 */
void Arm_AutoAbort(void);
void Arm_AutoClearTimeout(void);
uint8_t Arm_AutoSetJointUs(int joint, uint16_t pulse_us, uint32_t timeout_ms);
uint8_t Arm_AutoSetJointAngle(int joint, float angle_deg, uint32_t timeout_ms);
uint8_t Arm_AutoGoHome(uint32_t timeout_ms);
uint8_t Arm_AutoAnyTarget(void);
uint8_t Arm_AutoTimedOut(void);
uint8_t Arm_GripperIsClosed(void);

void Arm_AutoAbort(void) { }
void Arm_AutoClearTimeout(void) { }
uint8_t Arm_AutoSetJointUs(int joint, uint16_t pulse_us, uint32_t timeout_ms)
{
    (void)joint; (void)pulse_us; (void)timeout_ms;
    return 0U;
}
uint8_t Arm_AutoSetJointAngle(int joint, float angle_deg, uint32_t timeout_ms)
{
    (void)joint; (void)angle_deg; (void)timeout_ms;
    return 0U;
}
uint8_t Arm_AutoGoHome(uint32_t timeout_ms) { (void)timeout_ms; return 0U; }
uint8_t Arm_AutoAnyTarget(void) { return 0U; }
uint8_t Arm_AutoTimedOut(void) { return 0U; }
uint8_t Arm_GripperIsClosed(void) { return 0U; }


/*
 * HAL 链接期桩：`line_sensor.c` 的初始化/中断回调在 -O0 下也会被生成，
 * 它们引用这些 HAL 函数。本测试不会调用 LineSensor_Init()，
 * 因此这些桩只为实现链接存在。
 */
HAL_StatusTypeDef HAL_UART_Transmit(UART_HandleTypeDef *huart,
                                    const uint8_t *data,
                                    uint16_t length,
                                    uint32_t timeout)
{
    (void)huart; (void)data; (void)length; (void)timeout;
    return HAL_OK;
}

HAL_StatusTypeDef HAL_UART_Receive_IT(UART_HandleTypeDef *huart,
                                      uint8_t *data,
                                      uint16_t length)
{
    (void)huart; (void)data; (void)length;
    return HAL_OK;
}

HAL_StatusTypeDef HAL_UART_Init(UART_HandleTypeDef *huart)
{
    (void)huart;
    return HAL_OK;
}

void HAL_GPIO_Init(GPIO_TypeDef *port, GPIO_InitTypeDef *init)
{
    (void)port; (void)init;
}

void HAL_NVIC_SetPriority(IRQn_Type irqn, uint32_t priority, uint32_t subpriority)
{
    (void)irqn; (void)priority; (void)subpriority;
}

void HAL_NVIC_EnableIRQ(IRQn_Type irqn)
{
    (void)irqn;
}

uint8_t CDC_Transmit_FS(uint8_t *Buf, uint16_t Len)
{
    if ((Buf == NULL) || (Len > 256U) || (recorded_count >= RECORDER_CAPACITY))
    {
        return USBD_FAIL;
    }

    memcpy(recorded[recorded_count], Buf, Len);
    recorded_length[recorded_count] = Len;
    recorded_count++;

    /* 额外记住"最近一帧 0x14"，供调度路径的测试读取。 */
    if ((Len >= 4U) && (Buf[3] == 0x14U))
    {
        memcpy(last_type_14_frame, Buf, Len);
        last_type_14_length = Len;
        recorded_type_14 = 1U;
    }

    return USBD_OK;
}


uint8_t CDC_IsTxBusy_FS(void)
{
    return 0U;
}


uint32_t HAL_GetTick(void)
{
    return ++fake_tick_ms;
}


/* 主机上没有中断屏蔽；IWDG 等其他外设在此路径上不被引用。 */
void Error_Handler(void)
{
    fprintf(stderr, "Error_Handler called\n");
    exit(2);
}


/* ---------- 打印 ---------- */

static void print_frame(const uint8_t *frame, uint16_t length)
{
    uint16_t i;

    for (i = 0U; i < length; i++)
    {
        printf("%02X", frame[i]);
    }

    printf("\n");
}


int main(int argc, char **argv)
{
    uint16_t analog[8];
    int i;

    if (argc < 2)
    {
        fprintf(stderr,
                "usage: %s frame tick ch0..ch7 analog_valid | sched | sched-invalid\n",
                argv[0]);
        return 2;
    }

    /*
     * 三种模式：
     *   frame tick ch0..ch7 valid  直接调 RPI_SendLineTelemetry()（验编码）
     *   sched                      走 RPI_Update() 的 20ms 巡线调度（验接线）
     *   sched-invalid              同上，但模拟探头上报但最近一帧是 $D
     */
    if (strcmp(argv[1], "sched") == 0 || strcmp(argv[1], "sched-invalid") == 0)
    {
        int j;

        for (j = 0; j < 8; j++)
        {
            line_analog[j] = (uint16_t)(1000 + 10 * j);
        }

        line_online = 1U;
        line_analog_frame_latest =
            (strcmp(argv[1], "sched") == 0) ? 1U : 0U;

        /*
         * 遥测是 10ms 交替调度：奇偶两拍分别是 STATUS 与巡线。
         * 因此要推进到**出现 0x14** 那一拍，不能只看"收到一帧"。
         * 帧类型在帧头第 4 字节（索引 3）。
         */
        for (i = 0; (i < 5000) && (recorded_type_14 == 0U); i++)
        {
            RPI_Update();
        }

        if (recorded_type_14 == 0U)
        {
            fprintf(stderr, "no 0x14 frame was produced by the scheduler\n");
            return 1;
        }

        print_frame(last_type_14_frame, last_type_14_length);
        return 0;
    }

    if (argc != 12)
    {
        fprintf(stderr,
                "usage: %s frame tick ch0..ch7 analog_valid | sched | sched-invalid (argc=%d)\n",
                argv[0], argc);
        return 2;
    }

    for (i = 0; i < 8; i++)
    {
        long value = strtol(argv[3 + i], NULL, 0);

        if ((value < 0) || (value > 65535))
        {
            fprintf(stderr, "channel %d out of range\n", i);
            return 2;
        }

        analog[i] = (uint16_t)value;
    }

    RPI_Init();
    RPI_SetBootId(0U);

    (void)RPI_SendLineTelemetry(
        (uint32_t)strtoul(argv[2], NULL, 0),
        analog,
        (uint8_t)(strtoul(argv[11], NULL, 0) != 0U)
    );

    /* 出队：把排好的帧交给 recorder（计数达到 1 后循环自然退出）。 */
    for (i = 0; (i < 200) && (recorded_count == 0U); i++)
    {
        RPI_Update();
    }

    if (recorded_count != 1U)
    {
        fprintf(stderr, "expected 1 frame, got %u\n", (unsigned)recorded_count);
        return 1;
    }

    print_frame(recorded[0], recorded_length[0]);

    return 0;
}
