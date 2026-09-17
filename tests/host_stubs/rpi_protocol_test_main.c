/*
 * 0x20 / 0x21 机械臂通道的主机验证程序（gcc，不需要硬件）。
 *
 * 它把**真实的** rpi_protocol.c + arm.c 编译进一个可执行文件，只顶替
 * HAL / USB CDC / 巡线模块，然后用一个很小的文本脚本驱动：
 *
 *   boot                  复位协议层（RPI_Init + boot_id）
 *   armed 0|1             PB2 授权状态（同时同步 RPI_SetPhysicalStart）
 *   rc 0|1                遥控器在线；在线时摇杆取中位
 *   frame <hex>           把一整帧 0xAA55... 交给 RPI_USB_Receive
 *   tick <ms>             按 5ms 步长推进时钟并跑 RPI_Update + Arm_Update
 *   check <joint> <pulse> 断言某关节当前输出脉宽（µs）
 *   check_pending 0|1     断言"还有自动目标在跟踪"
 *   check_closed 0|1      断言 Arm_GripperIsClosed()
 *   check_timedout 0|1    断言自动目标超时锁存
 *   print_tx              把捕获到的所有固件发送帧按 hex 打印成 "TX <hex>" 行
 *
 * 脚本由 tests/test_firmware_rpi_protocol.py 生成：**帧是用树莓派侧真正的
 * encoder（robogame_core.serial_protocol.encode_frame）编出来的**，
 * 而固件发回的帧再由树莓派侧真正的 decoder 解析。于是这个测试同时验证了
 * "树莓派发的固件能读懂" 和 "固件发的树莓派能读懂"。
 *
 * 证据边界：不证明 Keil 能编译整个工程、不证明烧录、不证明舵机真的会动。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "arm.h"
#include "line_sensor.h"
#include "rpi_protocol.h"
#include "remote.h"
#include "safety.h"
#include "stm32f4xx_hal.h"
#include "usbd_cdc_if.h"

/* ---------------- HAL / 其它模块替身 ---------------- */

GPIO_TypeDef host_gpioa;
GPIO_TypeDef host_gpioi;
GPIO_TypeDef host_gpiod;
RCC_TypeDef host_rcc;
TIM_TypeDef host_tim7;

volatile uint8_t line_digital[8];
volatile uint16_t line_analog[8];
volatile uint32_t line_frame_count;
volatile uint32_t line_last_frame_tick;
volatile uint8_t line_online;
volatile uint8_t line_analog_frame_latest;

static uint32_t host_now_ms = 0U;
static uint8_t host_armed = 0U;
static Remote_Data host_remote;

uint32_t host_primask = 0U;

uint32_t HAL_GetTick(void)
{
    return host_now_ms;
}

uint8_t Safety_IsArmed(void)
{
    return host_armed;
}

Remote_Data Remote_GetData(void)
{
    return host_remote;
}

void HAL_GPIO_Init(GPIO_TypeDef *port, GPIO_InitTypeDef *init)
{
    (void)port;
    (void)init;
}

void HAL_GPIO_WritePin(GPIO_TypeDef *port, uint16_t pin, uint32_t state)
{
    (void)port;
    (void)pin;
    (void)state;
}

void HAL_NVIC_SetPriority(int irq, uint32_t preempt, uint32_t sub)
{
    (void)irq;
    (void)preempt;
    (void)sub;
}

void HAL_NVIC_EnableIRQ(int irq)
{
    (void)irq;
}

/* ---------------- USB CDC 抓帧 ---------------- */

#define CAPTURE_SLOTS 2048
#define CAPTURE_BYTES 96

static uint8_t tx_capture[CAPTURE_SLOTS][CAPTURE_BYTES];
static uint16_t tx_capture_len[CAPTURE_SLOTS];
static int tx_capture_count = 0;

uint8_t CDC_IsTxBusy_FS(void)
{
    /* 主机上没有真实 USB：视为上一帧已经发完，让队列继续流动。 */
    return 0U;
}

uint8_t CDC_Transmit_FS(uint8_t *Buf, uint16_t Len)
{
    if ((tx_capture_count < CAPTURE_SLOTS) && (Len <= CAPTURE_BYTES))
    {
        memcpy(tx_capture[tx_capture_count], Buf, Len);
        tx_capture_len[tx_capture_count] = Len;
        tx_capture_count++;
    }

    return USBD_OK;
}

/* ---------------- 脚手架 ---------------- */

static int failures = 0;

static void rc_center(void)
{
    memset(&host_remote, 0, sizeof(host_remote));
    host_remote.left_x = 127U;
    host_remote.left_y = 128U;
    host_remote.right_x = 127U;
    host_remote.right_y = 128U;
}

/*
 * 心跳：树莓派 robot_bridge 以 50Hz 发 0x02 HEARTBEAT，
 * 固件看门狗 250ms 没收到控制帧就会停自动动作。
 * 这里的 run_ms() 模拟同样的行为，否则长 tick 会把链路"走断"。
 *
 * CRC 算法与 rpi_protocol.c 的 RPI_CRC16_CCITT_FALSE 相同。
 * 它只影响"测试帧能不能被固件接收"：写错了会变成测试失败，
 * 不会造成假通过。
 */
static uint16_t host_crc16(const uint8_t *data, uint16_t length)
{
    uint16_t crc = 0xFFFFU;
    uint16_t i;
    uint8_t bit;

    for (i = 0U; i < length; i++)
    {
        crc ^= (uint16_t)data[i] << 8;

        for (bit = 0U; bit < 8U; bit++)
        {
            if ((crc & 0x8000U) != 0U)
            {
                crc = (uint16_t)((crc << 1) ^ 0x1021U);
            }
            else
            {
                crc = (uint16_t)(crc << 1);
            }
        }
    }

    return crc;
}

static uint16_t host_heartbeat_sequence = 1000U;
static uint8_t host_heartbeat_enabled = 1U;

static void host_send_heartbeat(void)
{
    uint8_t frame[14];
    uint16_t crc;

    frame[0] = 0xAAU;
    frame[1] = 0x55U;
    frame[2] = 1U;
    frame[3] = 0x02U;
    frame[4] = (uint8_t)(host_heartbeat_sequence & 0xFFU);
    frame[5] = (uint8_t)(host_heartbeat_sequence >> 8);
    frame[6] = 4U;
    frame[7] = 0U;
    frame[8] = (uint8_t)(host_now_ms & 0xFFU);
    frame[9] = (uint8_t)((host_now_ms >> 8) & 0xFFU);
    frame[10] = (uint8_t)((host_now_ms >> 16) & 0xFFU);
    frame[11] = (uint8_t)((host_now_ms >> 24) & 0xFFU);

    host_heartbeat_sequence++;

    crc = host_crc16(frame, 12U);
    frame[12] = (uint8_t)(crc & 0xFFU);
    frame[13] = (uint8_t)(crc >> 8);

    RPI_USB_Receive(frame, 14U);
}

static void run_ms(uint32_t total_ms)
{
    uint32_t elapsed;

    for (elapsed = 0U; elapsed < total_ms; elapsed += 5U)
    {
        host_now_ms += 5U;

        /* 每 100ms 一次心跳，远小于 250ms 看门狗窗口 */
        if (host_heartbeat_enabled && ((host_now_ms % 100U) < 5U))
        {
            host_send_heartbeat();
        }

        RPI_Update();
        Arm_Update();
    }
}

static int hex_value(char c)
{
    if ((c >= '0') && (c <= '9')) return c - '0';
    if ((c >= 'a') && (c <= 'f')) return c - 'a' + 10;
    if ((c >= 'A') && (c <= 'F')) return c - 'A' + 10;
    return -1;
}

static int parse_hex(const char *text, uint8_t *out, int max_len)
{
    int len = 0;
    int high = -1;

    while (*text != '\0')
    {
        int value = hex_value(*text);

        if (value < 0)
        {
            if ((*text == ' ') || (*text == '\t') || (*text == '\n') ||
                (*text == '\r'))
            {
                text++;
                continue;
            }
            return -1;
        }

        if (high < 0)
        {
            high = value;
        }
        else
        {
            if (len >= max_len)
            {
                return -1;
            }
            out[len] = (uint8_t)((high << 4) | value);
            len++;
            high = -1;
        }

        text++;
    }

    return (high < 0) ? len : -1;
}

static void print_tx(void)
{
    int i;
    int j;

    for (i = 0; i < tx_capture_count; i++)
    {
        printf("TX ");
        for (j = 0; j < (int)tx_capture_len[i]; j++)
        {
            printf("%02X", tx_capture[i][j]);
        }
        printf("\n");
    }
}

static int check_pulse(const char *arg)
{
    unsigned int joint = 0U;
    unsigned int expected = 0U;
    uint16_t actual;

    if (sscanf(arg, "%u %u", &joint, &expected) != 2)
    {
        printf("FAIL bad check_pulse argument: %s\n", arg);
        return 1;
    }

    if (joint >= (unsigned int)ARM_JOINT_COUNT)
    {
        printf("FAIL check_pulse joint out of range: %u\n", joint);
        return 1;
    }

    actual = Arm_GetPulseUs((Arm_Joint)joint);

    if (actual != (uint16_t)expected)
    {
        printf("FAIL joint %u pulse = %u, expected %u\n",
               joint, actual, expected);
        return 1;
    }

    return 0;
}

static int check_range(const char *arg)
{
    unsigned int joint = 0U;
    unsigned int low = 0U;
    unsigned int high = 0U;
    uint16_t actual;

    if (sscanf(arg, "%u %u %u", &joint, &low, &high) != 3)
    {
        printf("FAIL bad check_range argument: %s\n", arg);
        return 1;
    }

    if (joint >= (unsigned int)ARM_JOINT_COUNT)
    {
        printf("FAIL check_range joint out of range: %u\n", joint);
        return 1;
    }

    actual = Arm_GetPulseUs((Arm_Joint)joint);

    if ((actual < (uint16_t)low) || (actual > (uint16_t)high))
    {
        printf("FAIL joint %u pulse = %u, expected within [%u, %u]\n",
               joint, actual, low, high);
        return 1;
    }

    return 0;
}

static int run_script(const char *path)
{
    FILE *file;
    char line[512];

    file = fopen(path, "r");

    if (file == NULL)
    {
        printf("FAIL cannot open script %s\n", path);
        return 1;
    }

    while (fgets(line, sizeof(line), file) != NULL)
    {
        char *command = line;
        char *argument;

        while ((*command == ' ') || (*command == '\t'))
        {
            command++;
        }

        if ((*command == '\0') || (*command == '\n') || (*command == '\r') ||
            (*command == '#'))
        {
            continue;
        }

        argument = strchr(command, ' ');

        if (argument != NULL)
        {
            *argument = '\0';
            argument++;

            while (*argument == ' ') argument++;
        }
        else
        {
            argument = command + strlen(command);
        }

        /* 去掉行尾换行 */
        {
            char *nl = strpbrk(command, "\r\n");
            if (nl != NULL) *nl = '\0';
            nl = strpbrk(argument, "\r\n");
            if (nl != NULL) *nl = '\0';
        }

        if (strcmp(command, "boot") == 0)
        {
            RPI_Init();
            RPI_SetBootId(1U);
            RPI_SetPhysicalStart(0U);
        }
        else if (strcmp(command, "armed") == 0)
        {
            host_armed = (argument[0] == '1') ? 1U : 0U;
            RPI_SetPhysicalStart(host_armed);
        }
        else if (strcmp(command, "rc") == 0)
        {
            rc_center();
            host_remote.online = (argument[0] == '1') ? 1U : 0U;
        }
        else if (strcmp(command, "frame") == 0)
        {
            uint8_t bytes[128];
            int len = parse_hex(argument, bytes, (int)sizeof(bytes));

            if (len <= 0)
            {
                printf("FAIL bad frame hex: %s\n", argument);
                failures++;
                continue;
            }

            RPI_USB_Receive(bytes, (uint32_t)len);
        }
        else if (strcmp(command, "tick") == 0)
        {
            run_ms((uint32_t)strtoul(argument, NULL, 10));
        }
        else if (strcmp(command, "check") == 0)
        {
            failures += check_pulse(argument);
        }
        else if (strcmp(command, "check_range") == 0)
        {
            failures += check_range(argument);
        }
        else if (strcmp(command, "heartbeat") == 0)
        {
            host_heartbeat_enabled = (argument[0] == '0') ? 0U : 1U;
        }
        else if (strcmp(command, "check_pending") == 0)
        {
            uint8_t want = (argument[0] == '1') ? 1U : 0U;
            if (Arm_AutoAnyTarget() != want)
            {
                printf("FAIL pending = %u, expected %u\n",
                       Arm_AutoAnyTarget(), want);
                failures++;
            }
        }
        else if (strcmp(command, "check_closed") == 0)
        {
            uint8_t want = (argument[0] == '1') ? 1U : 0U;
            if (Arm_GripperIsClosed() != want)
            {
                printf("FAIL gripper closed = %u, expected %u\n",
                       Arm_GripperIsClosed(), want);
                failures++;
            }
        }
        else if (strcmp(command, "check_timedout") == 0)
        {
            uint8_t want = (argument[0] == '1') ? 1U : 0U;
            if (Arm_AutoTimedOut() != want)
            {
                printf("FAIL timed out = %u, expected %u\n",
                       Arm_AutoTimedOut(), want);
                failures++;
            }
        }
        else if (strcmp(command, "print_tx") == 0)
        {
            print_tx();
        }
        else
        {
            printf("FAIL unknown script command: %s\n", command);
            failures++;
        }
    }

    fclose(file);

    return failures;
}

int main(int argc, char **argv)
{
    if (argc < 2)
    {
        printf("usage: %s <script-file>\n", argv[0]);
        return 2;
    }

    rc_center();
    Arm_Init();

    if (run_script(argv[1]) != 0)
    {
        printf("RPI PROTOCOL HOST TEST: %d FAILURE(S)\n", failures);
        return 1;
    }

    printf("RPI PROTOCOL HOST TEST: all checks passed\n");
    return 0;
}
