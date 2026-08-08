# localization

轮式里程计 + IMU 融合定位节点，发布机器人统一位姿 `/pose` 和 TF 变换。

## 话题

| 方向 | 话题 | 类型 | 说明 |
|------|------|------|------|
| 订阅 | `/wheel_odom` | `nav_msgs/Odometry` | 底盘轮式里程计 |
| 订阅 | `/imu/data` | `sensor_msgs/Imu` | IMU 角速度（用于偏航观测） |
| 发布 | `/pose` | `nav_msgs/Odometry` | 融合后统一位姿（map 坐标系） |
| 广播 | `map -> base_link` | TF | 机器人位姿变换 |

## 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `imu_stale_s` | float | 0.2 | IMU 数据过期阈值（秒），超过则回退到轮式里程计角速度 |
| `max_speed_mps` | float | 3.0 | 物理最大速度（m/s），用于位姿跳变检测，超过此值视为异常 |
| `divergence_threshold` | float | 0.5 | 轮速与 IMU 角速度分歧阈值（rad/s），超过则打印警告 |

参数配置在 `robogame_bringup/config/robot.yaml`：

```yaml
localization:
  ros__parameters:
    imu_stale_s: 0.2
    max_speed_mps: 3.0
    divergence_threshold: 0.5
```

## 质量检查

节点在发布 `/pose` 前依次执行以下检查，任一检查失败都会拒绝该帧（不发布、不广播 TF）：

| 检查项 | 触发条件 | 行为 |
|--------|----------|------|
| IMU 过期回退 | IMU 年龄 > `imu_stale_s` | 角速度回退到轮式里程计，打印一次 warn |
| NaN/Inf 拦截 | 位置、姿态、速度任一值非有限 | 拒绝发布，打印 warn |
| 全零四元数 | qx=qy=qz=qw=0 | 拒绝发布，打印 warn |
| 角速度分歧 | abs(轮速角速度 - IMU角速度) > `divergence_threshold` | 打印 warn（不拒绝，仅提示可能打滑） |
| 位姿跳变 | 帧间速度 > `max_speed_mps` | 拒绝发布，打印 warn |

## 运行

```bash
cd ~/robogame_git/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
ros2 run localization localization_node --ros-args --params-file ~/robogame_git/ros2_ws/src/robogame_bringup/config/robot.yaml
```

## 测试

```bash
cd ~/robogame_git
python3 -m unittest tests.test_localization -v
```

## 实时监控工具

```bash
python3 tools/pose_monitor.py
```

订阅 `/pose`、`/wheel_odom`、`/imu/data`、`/cmd_vel`，实时显示位姿、速度、IMU 状态、角速度分歧和跳变状态。

## 后续计划

- 替换为 `robot_localization`（EKF）当单块任务通过后
- AprilTag 修正作为可配置输入接入
- 真车标定后调整 `max_speed_mps` 和 `divergence_threshold`
