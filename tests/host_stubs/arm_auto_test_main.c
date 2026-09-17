/*
 * 机械臂自动控制的主机验证程序（gcc 编译，不需要硬件）。
 *
 * 编译方式见 tests/test_firmware_arm_auto.py：
 *   gcc -I tests/host_stubs -I Core/Inc arm_auto_test_main.c Core/Src/arm.c
 *
 * 它驱动的是**真实的 arm.c**：arm.h / remote.h 都是 Core/Inc 里的原件，
 * 只有 HAL 层（GPIO/TIM7/RCC/tick/遥控/授权）被替身顶替。
 * 因此这里通过 = 固件逻辑通过；不通过 = 上车前就发现问题。
 *
 * 证据边界：本程序不能证明舵机真的会动、角度标定正确、或 Keil 能编译整个工程。
 * 它只证明 arm.c 的自动控制状态机行为符合设计。
 */
#include <stdio.h>
#include <string.h>

#include "arm.h"
#include "main.h"
#include "remote.h"
#include "safety.h"

/* ---------------- 替身实现 ---------------- */

GPIO_TypeDef host_gpioa;
GPIO_TypeDef host_gpioi;
GPIO_TypeDef host_gpiod;
RCC_TypeDef host_rcc;
TIM_TypeDef host_tim7;

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

/* ---------------- 测试脚手架 ---------------- */

static int failures = 0;

#define CHECK(cond, ...)                                        \
    do {                                                        \
        if (!(cond)) {                                          \
            printf("FAIL line %d: ", __LINE__);                 \
            printf(__VA_ARGS__);                                \
            printf("\n");                                       \
            failures++;                                         \
        }                                                       \
    } while (0)

/* 摇杆中位：右摇杆中心是 127/128（见 arm.c 的 -127 / -128） */
static void rc_centered_offline(void)
{
    memset(&host_remote, 0, sizeof(host_remote));
    host_remote.online = 0U;
    host_remote.left_x = 127U;
    host_remote.left_y = 128U;
    host_remote.right_x = 127U;
    host_remote.right_y = 128U;
}

static void run_ms(uint32_t total_ms, uint32_t step_ms)
{
    uint32_t elapsed;

    for (elapsed = 0U; elapsed < total_ms; elapsed += step_ms)
    {
        host_now_ms += step_ms;
        Arm_Update();
    }
}

/* ---------------- 用例 ---------------- */

static void test_angle_to_pulse(void)
{
    uint16_t pulse = 0U;

    CHECK((Arm_PulseUsFromAngle(ARM_JOINT_BASE, 0U, &pulse) == 1U) &&
          (pulse == 500U), "base 0deg -> 500us, got %u", pulse);
    CHECK((Arm_PulseUsFromAngle(ARM_JOINT_BASE, 135U, &pulse) == 1U) &&
          (pulse == 1500U), "base 135deg -> 1500us, got %u", pulse);
    CHECK((Arm_PulseUsFromAngle(ARM_JOINT_BASE, 270U, &pulse) == 1U) &&
          (pulse == 2500U), "base 270deg -> 2500us, got %u", pulse);

    /* 肩是 80KG，脉宽上限只有 1500µs */
    CHECK((Arm_PulseUsFromAngle(ARM_JOINT_SHOULDER, 270U, &pulse) == 1U) &&
          (pulse == 1500U), "shoulder 270deg -> 1500us, got %u", pulse);

    /* 爪：0° = 张开 1200µs，270° = 闭合 1540µs */
    CHECK((Arm_PulseUsFromAngle(ARM_JOINT_GRIPPER, 0U, &pulse) == 1U) &&
          (pulse == 1200U), "gripper 0deg -> 1200us open, got %u", pulse);
    CHECK((Arm_PulseUsFromAngle(ARM_JOINT_GRIPPER, 270U, &pulse) == 1U) &&
          (pulse == 1540U), "gripper 270deg -> 1540us closed, got %u", pulse);

    /* 越界必须拒绝，不能夹取 */
    CHECK(Arm_PulseUsFromAngle(ARM_JOINT_BASE, 271U, &pulse) == 0U,
          "271deg must be rejected, not clamped");
    CHECK(Arm_PulseUsFromAngle((Arm_Joint)9, 10U, &pulse) == 0U,
          "invalid joint must be rejected");
    CHECK(Arm_PulseUsFromAngle(ARM_JOINT_BASE, 10U, 0) == 0U,
          "null out pointer must be rejected");
}

static void test_disarmed_never_outputs(void)
{
    uint16_t i;

    host_armed = 0U;
    rc_centered_offline();

    Arm_Init();
    run_ms(100U, 5U);

    for (i = 0U; i < ARM_JOINT_COUNT; i++)
    {
        CHECK(Arm_GetPulseUs((Arm_Joint)i) == 0U,
              "disarmed joint %u must not output PWM", i);
    }
    CHECK(Arm_GripperIsClosed() == 0U,
          "gripper flag must be 0 while disarmed");

    /* 未授权时设的目标会被清掉，且不产生任何输出 */
    CHECK(Arm_AutoSetJointAngle(ARM_JOINT_BASE, 270U, 3000U) == 1U,
          "target may be registered while disarmed");
    run_ms(50U, 5U);
    CHECK(Arm_AutoAnyTarget() == 0U, "target must be dropped while disarmed");
    CHECK(Arm_GetPulseUs(ARM_JOINT_BASE) == 0U,
          "still no PWM while disarmed");
}

static void test_armed_starts_safe_and_slews(void)
{
    uint16_t after_one;
    uint16_t start_pulse;

    host_armed = 1U;
    rc_centered_offline();
    run_ms(20U, 5U);

    start_pulse = Arm_GetPulseUs(ARM_JOINT_BASE);
    CHECK(start_pulse != 0U, "armed must start PWM (safe pose)");

    /* 限速：一拍只能走 ARM_MAX_STEP_US，不能瞬跳到目标 */
    CHECK(Arm_AutoSetJointAngle(ARM_JOINT_BASE, 270U, 8000U) == 1U,
          "ARM_SET accepted");

    host_now_ms += 5U;
    Arm_Update();
    after_one = Arm_GetPulseUs(ARM_JOINT_BASE);

    CHECK(after_one != 2500U,
          "must not jump straight to the target, got %u", after_one);
    CHECK(after_one >= start_pulse,
          "must move toward the target, got %u from %u", after_one, start_pulse);
    CHECK((uint32_t)(after_one - start_pulse) <= 200U,
          "one tick must move at most ARM_MAX_STEP_US, moved %u",
          (unsigned)(after_one - start_pulse));
    CHECK(Arm_AutoAnyTarget() == 1U, "target must still be pending");

    /* 跑到位（2500-1474=1026µs，约 3.1s） */
    run_ms(20000U, 5U);
    CHECK(Arm_GetPulseUs(ARM_JOINT_BASE) == 2500U,
          "base must reach 2500us, got %u", Arm_GetPulseUs(ARM_JOINT_BASE));
    CHECK(Arm_AutoAnyTarget() == 0U, "target must clear once reached");
    CHECK(Arm_AutoTimedOut() == 0U, "no timeout expected on a 8s budget");
}

static void test_target_is_clamped_to_joint_limits(void)
{
    /* 肩上限 1500µs：给 2400µs 应被夹到 1500，而不是发出超限脉宽 */
    CHECK(Arm_AutoSetJointUs(ARM_JOINT_SHOULDER, 2400U, 8000U) == 1U,
          "over-range pulse accepted but must be clamped");
    run_ms(12000U, 5U);
    CHECK(Arm_GetPulseUs(ARM_JOINT_SHOULDER) == 1500U,
          "shoulder must stop at 1500us, got %u",
          Arm_GetPulseUs(ARM_JOINT_SHOULDER));
}

static void test_timeout_abandons_and_holds(void)
{
    uint16_t frozen;

    Arm_AutoClearTimeout();
    CHECK(Arm_AutoSetJointAngle(ARM_JOINT_BASE, 0U, 200U) == 1U,
          "short timeout accepted");

    run_ms(400U, 5U);
    CHECK(Arm_AutoTimedOut() == 1U, "timeout must latch");
    CHECK(Arm_AutoAnyTarget() == 0U, "timed-out target must be cleared");

    frozen = Arm_GetPulseUs(ARM_JOINT_BASE);
    CHECK(frozen < 2500U, "must not have reached the far target in 200ms");

    run_ms(500U, 5U);
    CHECK(Arm_GetPulseUs(ARM_JOINT_BASE) == frozen,
          "pose must hold after a timeout (%u vs %u)",
          Arm_GetPulseUs(ARM_JOINT_BASE), frozen);
}

static void test_rc_takes_over(void)
{
    Arm_AutoClearTimeout();
    rc_centered_offline();
    host_remote.online = 1U;
    host_remote.buttons = ARM_BASE_RIGHT_BUTTON;

    CHECK(Arm_AutoSetJointAngle(ARM_JOINT_WRIST, 270U, 8000U) == 1U,
          "target pending before takeover");
    CHECK(Arm_AutoAnyTarget() == 1U, "target registered");

    host_now_ms += 5U;
    Arm_Update();

    CHECK(Arm_AutoAnyTarget() == 0U,
          "any RC arm input must clear every auto target");
    CHECK(Arm_GetPulseUs(ARM_JOINT_WRIST) < 2500U,
          "wrist must not keep running toward the auto target");

    /* 只有底盘左摇杆动作时不许夺权 */
    rc_centered_offline();
    host_remote.online = 1U;
    host_remote.left_x = 255U;
    host_remote.left_y = 255U;
    Arm_AutoSetJointAngle(ARM_JOINT_WRIST, 270U, 8000U);
    host_now_ms += 5U;
    Arm_Update();
    CHECK(Arm_AutoAnyTarget() == 1U,
          "chassis stick must NOT cancel the arm auto target");
    Arm_AutoAbort();
}

static void test_disarm_holds_pose(void)
{
    uint16_t before;
    uint16_t i;

    host_armed = 1U;
    rc_centered_offline();
    run_ms(20U, 5U);

    CHECK(Arm_AutoSetJointAngle(ARM_JOINT_BASE, 200U, 8000U) == 1U,
          "move base away from the safe pose");
    run_ms(3000U, 5U);

    before = Arm_GetPulseUs(ARM_JOINT_BASE);
    CHECK(before != 1474U, "base should have moved from the safe pose");

    host_armed = 0U;
    run_ms(200U, 5U);

    CHECK(Arm_GetPulseUs(ARM_JOINT_BASE) == before,
          "disarm must hold the pose (%u vs %u)",
          Arm_GetPulseUs(ARM_JOINT_BASE), before);
    for (i = 0U; i < ARM_JOINT_COUNT; i++)
    {
        CHECK(Arm_GetPulseUs((Arm_Joint)i) != 0U,
              "disarm must not drop joint %u to 0 (80KG arm would fall)", i);
    }
}

static void test_gripper_grab_release(void)
{
    host_armed = 1U;
    rc_centered_offline();
    run_ms(20U, 5U);
    Arm_AutoClearTimeout();

    CHECK(Arm_AutoSetJointUs(ARM_JOINT_GRIPPER,
                             ARM_GRIPPER_CLOSE_PULSE_US, 5000U) == 1U,
          "GRAB accepted");
    run_ms(6000U, 5U);
    CHECK(Arm_GetPulseUs(ARM_JOINT_GRIPPER) == ARM_GRIPPER_CLOSE_PULSE_US,
          "GRAB must reach 1540us, got %u",
          Arm_GetPulseUs(ARM_JOINT_GRIPPER));
    CHECK(Arm_GripperIsClosed() == 1U, "closed flag after GRAB");

    CHECK(Arm_AutoSetJointUs(ARM_JOINT_GRIPPER,
                             ARM_GRIPPER_OPEN_PULSE_US, 5000U) == 1U,
          "RELEASE accepted");
    run_ms(6000U, 5U);
    CHECK(Arm_GetPulseUs(ARM_JOINT_GRIPPER) == ARM_GRIPPER_OPEN_PULSE_US,
          "RELEASE must reach 1200us, got %u",
          Arm_GetPulseUs(ARM_JOINT_GRIPPER));
    CHECK(Arm_GripperIsClosed() == 0U, "closed flag must clear after RELEASE");
}

static void test_home_moves_every_joint(void)
{
    uint16_t i;

    host_armed = 1U;
    rc_centered_offline();
    run_ms(20U, 5U);
    Arm_AutoClearTimeout();

    /* 先把两个关节推到极端，再 HOME */
    CHECK(Arm_AutoSetJointAngle(ARM_JOINT_BASE, 270U, 8000U) == 1U,
          "push base to 270deg");
    CHECK(Arm_AutoSetJointAngle(ARM_JOINT_WRIST, 0U, 8000U) == 1U,
          "push wrist to 0deg");
    run_ms(10000U, 5U);

    CHECK(Arm_AutoGoHome(8000U) == 1U, "HOME accepted");
    CHECK(Arm_AutoAnyTarget() == 1U, "HOME must register targets");

    run_ms(15000U, 5U);
    CHECK(Arm_AutoAnyTarget() == 0U, "HOME must finish");
    CHECK(Arm_AutoTimedOut() == 0U, "HOME must finish inside its 8s budget");

    for (i = 0U; i < ARM_JOINT_COUNT; i++)
    {
        CHECK(Arm_GetPulseUs((Arm_Joint)i) != 0U,
              "HOME joint %u lost its pulse", i);
    }
}

static void test_argument_validation(void)
{
    Arm_AutoAbort();

    CHECK(Arm_AutoSetJointUs((Arm_Joint)7, 1000U, 3000U) == 0U,
          "invalid joint must be rejected");
    CHECK(Arm_AutoSetJointUs(ARM_JOINT_BASE, 1500U, 0U) == 0U,
          "zero timeout must be rejected");
    CHECK(Arm_AutoSetJointAngle(ARM_JOINT_BASE, 400U, 3000U) == 0U,
          "angle beyond total travel must be rejected");
    CHECK(Arm_AutoSetAllUs(0, 3000U) == 0U, "null array must be rejected");

    /* 超长 timeout 被夹到 ARM_AUTO_TIMEOUT_MAX_MS，不会产生溢出跨度 */
    CHECK(Arm_AutoSetJointUs(ARM_JOINT_BASE, 1500U, 0xFFFFFFFFU) == 1U,
          "huge timeout accepted after clamping");
    CHECK(Arm_AutoAnyTarget() == 1U, "target registered");
    run_ms(200U, 5U);
    CHECK(Arm_AutoTimedOut() == 0U, "clamped timeout must not fire early");
    Arm_AutoAbort();
}

int main(void)
{
    test_angle_to_pulse();
    test_disarmed_never_outputs();
    test_armed_starts_safe_and_slews();
    test_target_is_clamped_to_joint_limits();
    test_timeout_abandons_and_holds();
    test_rc_takes_over();
    test_disarm_holds_pose();
    test_gripper_grab_release();
    test_home_moves_every_joint();
    test_argument_validation();

    if (failures != 0)
    {
        printf("ARM AUTO HOST TEST: %d FAILURE(S)\n", failures);
        return 1;
    }

    printf("ARM AUTO HOST TEST: all checks passed\n");
    return 0;
}
