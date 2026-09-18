# manipulator_client 使用说明

## 1. 这个包负责什么

它是“视觉与机械机构之间的协调员”。抓取时根据视觉距离和左右偏差低速对准，然后请求夹爪抓取；放置时先请求升降机构到指定层高，再请求释放。

> ⚠️ **本车没有升降装置，放置流程的升降这一步在真车上必然失败。**
> `manipulator_client` 会调用 `/lift/set_height`（`manipulator_client/node.py:531-546`），
> `robot_bridge` 在真实模式把它翻成固件的 `LIFT_ABS`（`robot_bridge/node.py:840-860`），
> 而固件对该操作**明确回 `FAILED` + `error_code=3010`**
> （`RPI_ERROR_MECH_NO_LIFT`，`Four_Motor_PID_Test_1/Four_Motor_PID_Test/Core/Src/rpi_protocol.c:217`、`:766-776`）。
> 看到 `3010` 说明“这台车根本没有升降”，**不是客户端 bug，也不是通信问题**——不要再去调客户端超时或重试，
> 应先和机械组确认放置方式（错误码表见 `robot_bridge/README.md` §7）。
> mock 模式下升降是模拟的，所以模拟流程通过**不能**证明真车放置可用。

## 2. 文件分工

- `manipulator_client/node.py`：全部协调逻辑，包括命令检查、视觉对准、服务调用、超时和结果发布。
- `setup.py`：登记程序名 `manipulator_client`。
- `package.xml`：声明 `/cubes`、`Twist` 和自定义服务依赖。

## 3. 推荐启动方式

没有硬件时启动模拟系统：

```bash
ros2 launch robogame_bringup mock_demo.launch.py
```

单独发送抓取命令：

```bash
ros2 topic pub --once /manipulator/command std_msgs/msg/String "{data: PICK_ORANGE}"
```

发送放置命令：

```bash
ros2 topic pub --once /manipulator/command std_msgs/msg/String "{data: PLACE_ORANGE}"
```

查看结果：

```bash
ros2 topic echo /manipulator/result
ros2 topic echo /cmd_vel
```

允许的命令只有 `PICK_ORANGE`、`PICK_PURPLE`、`PLACE_ORANGE`、`PLACE_PURPLE`。

## 4. 接口

- 订阅 `/cubes`：视觉目标。
- 订阅 `/manipulator/command`：上层动作命令。
- 订阅 `/robot/status`：通信、急停和机构故障；不安全时拒绝新动作并终止当前动作。
- 订阅 `/mission/active_source`：任务层广播的底盘授权（`node.py:79-81`）。`require_authorization` 默认 `false`（独立联调时没有任务层也能对准），比赛配置里打开；打开后只有授权给 `manipulator_client` 才允许继续视觉对准（`node.py:268-270`、`node.py:598-605`）。
- 发布 `/cmd_vel`：视觉接近时的低速底盘命令。
- 发布 `/manipulator/result`：动作结果。
- 发布 `/perception/stage`：当前视觉阶段名，如 `SEARCH`、`ACQUIRE`、`VERIFY`（`node.py:551`、`node.py:555`、`node.py:487`）。
- 调用 `/gripper/grab`、`/gripper/release`、`/lift/set_height`（最后这个在真车上必然返回 `3010`，见 §1 的警告）。
- 调用 `/chassis/retreat`：发 `RETREAT` 请求撤退（`node.py:381-388`）。
- 调用 `/chassis/stop`：发 `STOP` 取消当前动作（`node.py:272-280`）。

## 5. 参数

- `target_distance_m`：抓取前希望与方块保持的距离。
- `distance_tolerance_m`、`cross_tolerance_m`：允许误差（前后距离、横向）。
- `kp_distance`、`kp_bearing`：对准速度增益（距离方向、朝向方向）。
- `max_speed`：视觉对准最大速度。
- `max_turn_rate`、`min_turn_rate`：朝向角速度上限与下限，默认 `0.6` / `0.0`。
- `camera_yaw_offset_rad`：相机相对车头的朝向偏置，默认 `0.0`。
- `target_stale_s`：视觉结果过期时间。
- `status_stale_s`：机器人状态话题的过期时间，过期后停止当前动作。
- `action_timeout_s`：整个动作超时。
- `service_wait_timeout_s`：等待服务可用的最长时间，默认 `1.0` 秒。
- `place_heights_m`：第一、二、三层放置高度。**依赖升降机构，而本车没有升降装置**：真车调用 `/lift/set_height` 只会拿到固件错误码 `3010`，这三层高度目前只是 mock 流程用的值。
- `grab_verification_policy`、`grab_verification_timeout_s`：抓取成功后用什么证据确认（`service_only` / `cube_present` / `gripper_and_cube`）以及等待多久。
- `place_verification_policy`、`place_verification_timeout_s`：放置成功后用什么证据确认（`service_only` / `cube_absent` / `gripper_open_and_cube_absent`）以及等待多久。
- `placement_evidence_policy`、`placement_stable_duration_s`、`placement_observation_timeout_s`、`placement_max_unavailable_gap_s`：放置后的观察口径与时长（`robot_mock.yaml` 用 `mock_qualified`，`robot_field.yaml` 用 `unavailable`）。
- `require_authorization`：默认 `false`（作用见 §4）。
- `authorization_stale_s`：授权多久没刷新就失效，默认 `0.5` 秒。

上面这些参数的默认值集中在 `node.py:53-60`（`max_turn_rate`、`min_turn_rate`、`camera_yaw_offset_rad`、`service_wait_timeout_s` 在 `node.py:49-52`，两个授权参数在 `node.py:64-65`）；环境层覆盖见 `robogame_bringup/config/robot_mock.yaml` 与 `robot_field.yaml`。

## 6. 当前流程和缺口

当前抓取流程是：收到命令 → 等目标 → 距离/横向 P 控制 → 进入容差 → 调用抓取服务 → 返回结果。放置流程是：选层高 → 升降 → 释放 → 层数加一。**其中“升降”这一步在真车上会被固件拒绝（`3010`，见 §1）**，所以真车放置目前走不通，需要机械组给出无升降的放置方案。

已经实现的部分：抓取后的多证据验证（`_verify_grab`，`node.py:308-326`；策略与超时见 `grab_verification_policy`、`grab_verification_timeout_s`）、放置后的验证与稳定性观察（`_verify_place`，`node.py:328-360`；`PlacementStabilityObserver` 在 `robogame_core/robogame_core/manipulator.py:75`；策略见 `place_verification_policy`、`placement_evidence_policy`）、取消命令（`_request_stop` 调 `/chassis/stop`，`node.py:272-280`）、以及朝向闭环把对准角速度下发为 `cmd_vel.angular.z`（`node.py:606-611`，增益 `kp_bearing` / `max_turn_rate` 见 `node.py:575-577`）。

尚未实现完整的 `SEARCH` 旋转搜索、运输掉块检测和精细重试。目标一直看不到时当前节点先停车，最终由总动作超时返回失败。

当前已增加下层安全联锁：没有收到机器人状态、通信异常、急停或机构故障时不会继续视觉对准，也不会接受新的抓放命令。硬件急停仍必须由电控直接切断执行器，软件联锁只是第二道保护。

## 7. 必须和机械、电控确认

- 抓取、释放、升降是否允许同时动作。
- 每条命令的完成反馈、错误码、极限位置和最长耗时。
- 三层真实高度是夹爪高度、方块底面高度还是机构编码器高度。
- 夹爪如何判断“真的抓到”：限位、电流、位置或独立传感器。
- 掉块如何检测，急停后机构处于什么状态。

真实接口未冻结前只能使用 `robot_bridge` 的模拟服务，不能宣称真实抓放已经完成。
