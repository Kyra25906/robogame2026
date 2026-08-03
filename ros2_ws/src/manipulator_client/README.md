# manipulator_client 使用说明

## 1. 这个包负责什么

它是“视觉与机械机构之间的协调员”。抓取时根据视觉距离和左右偏差低速对准，然后请求夹爪抓取；放置时先请求升降机构到指定层高，再请求释放。

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
- 发布 `/cmd_vel`：视觉接近时的低速底盘命令。
- 发布 `/manipulator/result`：动作结果。
- 调用 `/gripper/grab`、`/gripper/release`、`/lift/set_height`。

## 5. 参数

- `target_distance_m`：抓取前希望与方块保持的距离。
- `distance_tolerance_m`、`lateral_tolerance_m`：允许误差。
- `kp_distance、kp_lateral`：对准速度增益。
- `max_speed`：视觉对准最大速度。
- `target_stale_s`：视觉结果过期时间。
- `action_timeout_s`：整个动作超时。
- `place_heights_m`：第一、二、三层放置高度。

## 6. 当前流程和缺口

当前抓取流程是：收到命令 → 等目标 → 距离/横向 P 控制 → 进入容差 → 调用抓取服务 → 返回结果。放置流程是：选层高 → 升降 → 释放 → 层数加一。

尚未实现完整的 `SEARCH` 旋转搜索、目标角度 `wz` 控制、抓取后的双证据验证、运输掉块检测、放置后视觉稳定性验证、机构取消命令和精细重试。目标一直看不到时当前节点先停车，最终由总动作超时返回失败。

## 7. 必须和机械、电控确认

- 抓取、释放、升降是否允许同时动作。
- 每条命令的完成反馈、错误码、极限位置和最长耗时。
- 三层真实高度是夹爪高度、方块底面高度还是机构编码器高度。
- 夹爪如何判断“真的抓到”：限位、电流、位置或独立传感器。
- 掉块如何检测，急停后机构处于什么状态。

真实接口未冻结前只能使用 `robot_bridge` 的模拟服务，不能宣称真实抓放已经完成。
