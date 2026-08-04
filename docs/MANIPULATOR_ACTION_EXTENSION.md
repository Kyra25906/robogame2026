# 机械动作确认后的软件扩展说明

本文说明机械、电控确认新动作或修改现有动作后，算法代码应怎样扩展。目标是让流程状态与机构操作保持分离，并确保接口、模拟、测试和安全行为同步更新。

## 1. 两组概念

`ManipulatorState` 描述软件流程所在阶段：

- `IDLE`：没有活动任务；
- `WAITING_TARGET`：等待视觉目标；
- `ALIGNING`：底盘正在视觉对准；
- `WAITING_SERVICE`：等待机构服务可用；
- `EXECUTING`：机构服务正在执行；
- `VERIFYING`：等待传感器证据确认结果。

`MechanismOperation` 描述机构当前执行的动作：

- `NONE`：没有机构动作；
- `GRAB`：抓取；
- `LIFT`：升降至目标高度；
- `RELEASE`：释放。

例如“等待释放服务”应表示为：

```text
workflow_state = WAITING_SERVICE
operation = RELEASE
```

不要新增 `WAITING_RELEASE` 这类把两个概念重新拼接起来的状态。

## 2. 机械确认前必须获得的信息

新增或修改动作前，与机械、电控共同确认：

1. 动作名称和实际机械含义；
2. 请求参数、单位、范围和坐标方向；
3. 正常完成的反馈及最长耗时；
4. 错误码、错误详情和可否重试；
5. 动作能否取消，以及急停时的硬件行为；
6. 可否与夹爪、升降或底盘同时执行；
7. 成功证据来自限位、电流、编码器还是独立传感器；
8. 通信中断后机构应保持、回零还是停止。

没有确认的信息应保留为配置或待确认项，不要写死为“真实行为”。

## 3. 新增动作的代码步骤

假设机械确认新增 `HOME` 回零动作：

1. 在 `MechanismOperation` 中新增 `HOME`，不要新增 `HOMING` 流程状态。
2. 确认现有 `ExecuteMechanism.srv` 是否能表达请求；不能时再修改接口。
3. 在 `manipulator_client` 中定义允许从哪个流程状态进入 `HOME`。
4. 等待服务时使用 `WAITING_SERVICE + HOME`。
5. 服务调用期间使用 `EXECUTING + HOME`。
6. 如需传感器确认，服务成功后进入 `VERIFYING + HOME`。
7. 为成功、失败、超时、异常、取消和急停分别定义转移结果。
8. 更新模拟服务，使其可以产生同样的成功与失败反馈。
9. 先增加纯 Python 状态测试，再增加 ROS 2 节点级测试。
10. 更新接口文档、启动配置和现场验收清单。

## 4. 修改已有动作的规则

如果只是修改超时、目标高度或验证策略，优先修改参数配置，不要改变状态枚举。

如果请求或响应字段改变，必须同步检查：

- `robogame_interfaces` 中的 `.srv` 或 `.msg`；
- `robot_bridge` 与 MCU 协议转换；
- `manipulator_client` 请求和响应处理；
- 模拟机构服务；
- 自动测试和接口文档；
- 旧日志、旧配置和兼容策略。

接口变更应由算法、机械、电控共同确认后再合并。

## 5. 安全与测试最低要求

每个机构动作至少测试：

- 服务立即可用并成功；
- 服务暂时不可用后恢复；
- 服务等待超时；
- 服务返回失败和错误码；
- 异步调用异常；
- 动作总超时；
- 通信中断、急停和机构故障；
- 动作结束后的状态清理；
- 失败后载荷计数不被错误修改。

远程模拟通过只代表软件状态转换正确。机构方向、动作时间、成功证据和安全停止仍需现场实机确认。

## 6. 抓取成功证据策略

抓取服务返回 `success=true` 后，流程进入 `VERIFYING + GRAB`。参数 `grab_verification_policy` 决定成功证据：

- `service_only`：只使用服务结果，保持机械判据确认前的兼容行为；
- `cube_present`：要求 `RobotStatus.cube_present=true`；
- `gripper_and_cube`：要求 `gripper_closed=true` 且 `cube_present=true`。

机械确认后，应根据传感器的真实含义选择策略。例如“夹住方块时夹爪无法完全闭合”，就不能使用 `gripper_and_cube`。如果现有三种策略都不符合实际，应先在本文记录新的证据真值表，再扩展 `GrabVerificationPolicy` 和纯函数测试，最后修改现场配置。

必须同时确认 `grab_verification_timeout_s`：它应大于状态反馈的正常延迟，但小于允许的任务阻塞时间。验证超时只能说明证据不足，不能自动断言方块一定没有被抓住。

除 `service_only` 外，证据必须来自抓取服务完成后更新的 `RobotStatus`。抓取前遗留的 `cube_present=true` 或 `gripper_closed=true` 不得作为本次抓取的成功证据。若未来消息链路无法保证状态更新时间，应在 `RobotStatus` 中增加动作序号或证据时间戳，而不是放宽这一时序要求。

## 7. 动作取消与硬件停止

`/manipulator/command` 支持 `CANCEL`，用于停止本地软件流程、发布底盘零速度并阻止后续抓放步骤。它适合任务管理器、安全逻辑和调试流程调用。

当前机构服务没有确认取消协议。因此：

- 在等待目标、视觉对准、等待服务或验证阶段，软件可以完整停止后续流程；
- 在 `EXECUTING` 阶段，软件只能放弃等待和后续步骤，已经发出的 `GRAB`、`LIFT` 或 `RELEASE` 仍可能在服务端完成；
- 硬件急停必须由电控直接停止执行器，不能依赖本地 `CANCEL`。

机械、电控确认真实取消方式后，应按以下步骤升级：

1. 确认是独立 `CancelMechanism` 服务、统一 `STOP` 命令，还是 ROS 2 Action 取消；
2. 明确取消响应、最长停止时间、无法取消时的错误码和最终机构状态；
3. 在 `robot_bridge` 和模拟机构中同时实现；
4. `CANCEL` 到来时先请求硬件取消，再等待停止证据；
5. 增加 `CANCELLING` 或复用合适流程状态，并保持 `operation` 为被取消的动作；
6. 测试取消成功、取消超时、服务失联、急停和取消后重新接受命令；
7. 实机验证执行器确实停止后，才允许去掉“机构动作可能仍会完成”的警告。

比赛启动后禁止无线人为干预机器人，`CANCEL` 不应被用作比赛中的远程遥控操作。

## 8. 放置成功证据策略

释放服务返回 `success=true` 后，流程进入 `VERIFYING + RELEASE`。参数 `place_verification_policy` 决定成功证据：

- `service_only`：只使用服务结果，保持机械判据确认前的兼容行为；
- `cube_absent`：要求释放后的新 `RobotStatus` 显示 `cube_present=false`；
- `gripper_open_and_cube_absent`：同时要求 `gripper_closed=false` 和 `cube_present=false`。

只有验证通过后才能增加 `placed_layers`。等待证据、验证超时、服务失败或取消都不得增加层数。

`gripper_closed=false` 是否等价于“夹爪已经完全打开”必须由机械确认。如果该字段只表示“未到闭合限位”，不能据此判断释放成功。与抓取验证相同，严格策略只接受释放服务完成后更新的状态。

## 9. 模拟机构状态

`robot_bridge` 的模拟模式会维护独立的机构状态：

- 模拟抓取成功后发布 `cube_present=true`、`gripper_closed=true`；
- 模拟释放成功后发布 `cube_present=false`、`gripper_closed=false`；
- 模拟升降成功后记录目标高度，但当前 `RobotStatus` 尚无升降高度字段；
- 模拟失败不会修改机构状态。

参数 `mock_grab_success`、`mock_release_success`、`mock_lift_success` 可用于复现失败。模拟信号只是为了验证软件闭环，不代表真实传感器一定具有相同真值关系。

专用严格闭环验收入口：

```bash
cd ros2_ws
colcon build --symlink-install
source install/setup.bash
ros2 launch robogame_bringup manipulator_mock_smoke.launch.py
```

该启动文件只在验收进程中启用严格抓取和放置策略，不修改正式配置。它自动执行一次 `PICK_ORANGE` 和 `PLACE_ORANGE`，要求观察到抓取后的载荷证据以及释放后的无载荷证据，并以进程退出码表示 PASS 或 FAIL。
