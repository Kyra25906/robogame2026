# motion_control 使用说明

## 1. 这个包负责什么

它接收一个目标位置，比较“当前位置”和“目标位置”，计算底盘应该前后、横向和旋转多快。到达后发布 `SUCCESS`；位姿丢失、超时或目标越界时停止。

## 2. 文件分工

- `motion_control/node.py`：ROS2 节点、目标接收、安全检查、20 ms 控制循环。
- `robogame_core/navigation.py`：真正的到点误差计算、比例控制、限速和接近减速。
- `setup.py`：登记程序名 `motion_controller`。
- `package.xml`：声明消息和核心库依赖。

## 3. 启动和手动发目标

先启动模拟底盘、定位和控制器：

```bash
ros2 launch robogame_bringup mock_demo.launch.py
```

另开终端发送目标：

```bash
source /opt/ros/jazzy/setup.bash
source ~/robogame/ros2_ws/install/setup.bash
ros2 topic pub --once /motion/goal geometry_msgs/msg/Pose2D \
  "{x: 1.0, y: 0.5, theta: 0.0}"
```

观察：

```bash
ros2 topic echo /motion/result
ros2 topic echo /cmd_vel
ros2 topic echo /pose --once
```

`theta` 单位是弧度：90° 约等于 `1.5708`。

## 4. 输入和输出

- 输入 `/pose`：当前位置，由 `localization` 发布。
- 输入 `/motion/goal`：目标 `x、y、theta`，由人工或 `mission_manager` 发布。
- 输入 `/robot/status`：通信和急停状态；状态缺失、通信故障或急停时拒绝新目标并终止当前导航。
- 输出 `/cmd_vel`：`vx、vy、wz`，交给 `robot_bridge`。
- 输出 `/motion/result`：`SUCCESS`、`TIMEOUT`、`LOCALIZATION_ERROR` 或 `SAFETY_STOP`。

目标中的 `x、y、theta` 如果包含 `NaN` 或无穷大，会立即返回 `SAFETY_STOP`。输入位姿出现异常数字时，该帧会被忽略；如果有效位姿没有及时恢复，现有位姿过期保护会停车。

## 5. 参数

- `kx、ky、kyaw`：三个方向的比例增益。
- `max_vx、max_vy、max_wz`：速度上限。
- `slow_radius`：距离目标多近时开始逐步降低平移速度上限。
- `max_ax、max_ay、max_awz`：三个方向每秒允许的最大速度变化。
- `max_control_dt_s`：控制循环偶发卡顿时，用于限速计算的最大时间间隔，防止恢复后速度突跳。
- `position_tolerance、yaw_tolerance`：多近算到达。
- `goal_timeout_s`：到点最长允许时间。
- `pose_stale_s`：多久没收到新位姿就停车。
- `status_stale_s`：多久没收到新的机器人状态就终止导航，默认0.30秒。
- `min_x、max_x、min_y、max_y`：允许目标区域。

这些参数集中在 `robogame_bringup/config/robot.yaml`。

控制增益必须是有限的非负数；速度上限、容差、减速半径和速度变化上限必须是有限正数。错误配置会在节点启动时明确失败，避免带着无效参数运行真车。

## 6. 当前限制和现场待办

当前是适合固定路点的 P 控制，不是 Nav2。它没有障碍物规划和避障；已经具备三轴速度变化限制，但默认加速度只是安全起点，必须依据真车能力调小后逐步验证。模拟中的位姿由命令速度积分，因此成功只证明接口和数学流程连通，不代表真车精度。

现场先把速度上限降到安全值，依次调旋转、前后、横移；每次只改一个增益。还需要确认电控对 `Twist` 三个方向的定义、底盘饱和限制、刹车行为和通信丢失停车。

## 7. 自动测试

```bash
cd ~/robogame
python3 -m unittest tests.test_navigation -v
```
