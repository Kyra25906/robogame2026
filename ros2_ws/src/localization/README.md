# localization 使用说明

## 1. 这个包负责什么

它把底盘给出的轮式里程计整理成算法统一使用的位姿 `/pose`，同时广播 `map -> base_link` 坐标变换。可以把它理解为“告诉其他模块小车现在在哪里、朝向哪里”。

## 2. 文件分工

- `localization/node.py`：唯一功能文件，接收 `/wheel_odom` 和 `/imu/data`，发布 `/pose` 与 TF。
- `setup.py`：登记可执行程序 `localization_node`。
- `package.xml`：声明 ROS2 消息和 TF 依赖。
- `resource/localization`、`__init__.py`：ROS2 Python 包识别所需的标准文件，通常不用修改。

## 3. 启动

```bash
cd ~/robogame/ros2_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 run localization localization_node
```

另开终端查看：

```bash
source /opt/ros/jazzy/setup.bash
source ~/robogame/ros2_ws/install/setup.bash
ros2 topic echo /pose --once
ros2 topic hz /pose
ros2 run tf2_ros tf2_echo map base_link
```

最方便的模拟测试是启动整套系统：

```bash
ros2 launch robogame_bringup mock_demo.launch.py
```

## 4. 输入和输出

- 输入 `/wheel_odom`：`nav_msgs/Odometry`，底盘累计位置和速度。
- 输入 `/imu/data`：`sensor_msgs/Imu`，当前只取 `angular_velocity.z`。
- 输出 `/pose`：`nav_msgs/Odometry`，供 `motion_control` 使用。
- 输出 TF：`map -> base_link`，供坐标关系和可视化使用。

## 5. 当前代码实际做到了什么

当前版本是冲刺期的简化适配层，不是真正的传感器融合：它复制 `/wheel_odom` 的位置和姿态，把 frame 改成 `map`，并用 IMU 的 z 轴角速度替换输出速度中的角速度。它没有用 IMU 修正累计朝向，也没有处理漂移。

参数 `imu_stale_s` 已在 `robot.yaml` 中配置（默认 0.2 秒）。当 IMU 数据年龄超过该阈值时，自动回退到轮式里程计角速度，并打印一次警告日志；IMU 恢复后自动重新采用并打印恢复信息。同时检查位姿数据是否为有限数值（NaN/inf 被拒绝），全零四元数也被拒绝，异常时不发布 `/pose` 和 TF。

运行定位测试：

```bash
cd ~/robogame_git
python3 -m unittest discover -s tests -v
```

## 6. 接硬件后必须完成

- 和电控确认轮速/里程计单位、正方向、时间戳和坐标系。
- 测量轮径、轮距或麦轮几何参数，做直行、横移、原地旋转标定。
- 检查 IMU 安装方向、零偏和静止噪声。
- 处理 IMU 过期、轮速跳变、时间倒退和启动复位。
- 根据实测决定使用互补滤波或 `robot_localization` EKF。
- AprilTag 只作为后续绝对位置修正，不要把当前 `/pose` 当成高精度真实定位。

## 7. 验收建议

让小车分别直行 1 m、横移 1 m、旋转 360°，每项做 10 次，对比 `/pose` 与卷尺/角度标记。先记录系统偏差，再修改参数；不要只看 RViz 中轨迹“像是正确”。

纯算法测试：

```bash
cd ~/robogame
python3 -m unittest tests.test_navigation -v
```
