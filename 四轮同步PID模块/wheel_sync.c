#include "wheel_sync.h"


static float WheelSync_Abs(float value)
{
    return (value >= 0.0f) ? value : -value;
}


static float WheelSync_Sign(float value)
{
    if (value > 0.0f)
    {
        return 1.0f;
    }

    if (value < 0.0f)
    {
        return -1.0f;
    }

    return 0.0f;
}


static float WheelSync_Clamp(float value,
                             float minimum,
                             float maximum)
{
    if (value > maximum)
    {
        return maximum;
    }

    if (value < minimum)
    {
        return minimum;
    }

    return value;
}


void WheelSync_Init(WheelSync_Controller *controller)
{
    if (controller == 0)
    {
        return;
    }

    /* 初始值以稳定、保守为主。 */
    controller->kp = 0.15f;
    controller->max_correction_rpm = 8.0f;
    controller->enable_min_rpm = 10.0f;
    controller->target_tolerance_rpm = 3.0f;
    controller->enabled = 1U;
}


void WheelSync_SetEnabled(WheelSync_Controller *controller,
                          uint8_t enabled)
{
    if (controller == 0)
    {
        return;
    }

    controller->enabled = (enabled != 0U) ? 1U : 0U;
}


uint8_t WheelSync_Update(
    const WheelSync_Controller *controller,
    const volatile float base_target_rpm[WHEEL_SYNC_MOTOR_COUNT],
    const volatile float actual_rpm[WHEEL_SYNC_MOTOR_COUNT],
    volatile float corrected_target_rpm[WHEEL_SYNC_MOTOR_COUNT]
)
{
    float direction[WHEEL_SYNC_MOTOR_COUNT];
    float normalized_actual[WHEEL_SYNC_MOTOR_COUNT];
    float target_abs;
    float target_abs_min;
    float target_abs_max;
    float speed_sum;
    float average_speed;
    float synchronization_error;
    float correction;
    uint8_t i;

    if ((controller == 0) ||
        (base_target_rpm == 0) ||
        (actual_rpm == 0) ||
        (corrected_target_rpm == 0))
    {
        return 0U;
    }

    /* 默认保持运动学给出的原始目标。 */
    for (i = 0U; i < WHEEL_SYNC_MOTOR_COUNT; i++)
    {
        corrected_target_rpm[i] = base_target_rpm[i];
    }

    if (!controller->enabled)
    {
        return 0U;
    }

    target_abs_min = WheelSync_Abs(base_target_rpm[0]);
    target_abs_max = target_abs_min;

    for (i = 0U; i < WHEEL_SYNC_MOTOR_COUNT; i++)
    {
        target_abs = WheelSync_Abs(base_target_rpm[i]);

        /* 停车和极低速时禁止同步，避免零速附近抖动。 */
        if (target_abs < controller->enable_min_rpm)
        {
            return 0U;
        }

        if (target_abs < target_abs_min)
        {
            target_abs_min = target_abs;
        }

        if (target_abs > target_abs_max)
        {
            target_abs_max = target_abs;
        }
    }

    /*
     * 只有四轮理论目标绝对值接近时才做等速同步。
     * 前进、后退、纯横移和原地旋转满足该条件。
     * 组合运动时四轮理论目标可能不同，不能强行同步。
     */
    if ((target_abs_max - target_abs_min) >
        controller->target_tolerance_rpm)
    {
        return 0U;
    }

    speed_sum = 0.0f;

    /*
     * 按各轮目标方向统一实际速度符号。
     * 例如目标和实际均为负：(-RPM) * (-1) = 正速度。
     */
    for (i = 0U; i < WHEEL_SYNC_MOTOR_COUNT; i++)
    {
        direction[i] = WheelSync_Sign(base_target_rpm[i]);
        normalized_actual[i] = actual_rpm[i] * direction[i];
        speed_sum += normalized_actual[i];
    }

    average_speed = speed_sum / (float)WHEEL_SYNC_MOTOR_COUNT;

    for (i = 0U; i < WHEEL_SYNC_MOTOR_COUNT; i++)
    {
        /* 慢轮误差为正，快轮误差为负。 */
        synchronization_error =
            average_speed - normalized_actual[i];

        correction =
            controller->kp * synchronization_error;

        correction = WheelSync_Clamp(
            correction,
            -controller->max_correction_rpm,
            controller->max_correction_rpm
        );

        corrected_target_rpm[i] =
            base_target_rpm[i]
            + direction[i] * correction;
    }

    return 1U;
}
