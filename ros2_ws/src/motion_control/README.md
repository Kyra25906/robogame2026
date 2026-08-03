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
- 输出 `/cmd_vel`：`vx、vy、wz`，交给 `robot_bridge`。
- 输出 `/motion/result`：`SUCCESS`、`TIMEOUT`、`LOCALIZATION_ERROR` 或 `SAFETY_STOP`。

## 5. 参数

- `kx、ky、kyaw`：三个方向的比例增益。
- `max_vx、max_vy、max_wz`：速度上限。
- `position_tolerance、yaw_tolerance`：多近算到达。
- `goal_timeout_s`：到点最长允许时间。
- `pose_stale_s`：多久没收到新位姿就停车。
- `min_x、max_x、min_y、max_y`：允许目标区域。

这些参数集中在 `robogame_bringup/config/robot.yaml`。

## 6. 当前限制和现场待办

当前是适合固定路点的 P 控制，不是 Nav2。它没有障碍物规划、避障和完整加速度限制。模拟中的位姿由命令速度积分，因此成功只证明接口和数学流程连通，不代表真车精度。

现场先把速度上限降到安全值，依次调旋转、前后、横移；每次只改一个增益。还需要确认电控对 `Twist` 三个方向的定义、底盘饱和限制、刹车行为和通信丢失停车。

## 7. 自动测试

```bash
cd ~/robogame
python3 -m unittest tests.test_navigation -v
```
