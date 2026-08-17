#ifndef __WHEEL_SYNC_H
#define __WHEEL_SYNC_H

#include <stdint.h>

#define WHEEL_SYNC_MOTOR_COUNT 4U

typedef struct
{
    float kp;
    float max_correction_rpm;
    float enable_min_rpm;
    float target_tolerance_rpm;
    uint8_t enabled;
} WheelSync_Controller;

void WheelSync_Init(WheelSync_Controller *controller);

void WheelSync_SetEnabled(WheelSync_Controller *controller,
                          uint8_t enabled);

/*
 * base_target_rpm：麦轮运动学产生的原始四轮目标。
 * actual_rpm：编码器测得的四轮实际速度。
 * corrected_target_rpm：送给四个单轮PID的修正目标。
 *
 * 返回值：
 * 1：本周期启用了四轮同步修正。
 * 0：本周期未启用，修正目标等于原始目标。
 */
uint8_t WheelSync_Update(
    const WheelSync_Controller *controller,
    const volatile float base_target_rpm[WHEEL_SYNC_MOTOR_COUNT],
    const volatile float actual_rpm[WHEEL_SYNC_MOTOR_COUNT],
    volatile float corrected_target_rpm[WHEEL_SYNC_MOTOR_COUNT]
);

#endif
