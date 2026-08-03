# robot_bridge 使用说明

## 1. 这个包负责什么

它是上位机 ROS2 与下位机电控之间的“翻译员”。上层发 `/cmd_vel` 和机构请求；它应转换为串口数据。电控返回轮速、IMU、机构和故障数据后，它应转换为 ROS2 消息。

当前模拟模式可完整演示软件流程；真实硬件接收部分仍是明确的待开发接口。

## 2. 文件分工

- `robot_bridge/node.py`：打开串口、接收速度、发布模拟里程计/IMU/状态、提供模拟机构服务。
- `robogame_core/serial_protocol.py`：帧头、版本、序号、长度、CRC16、速度载荷和流解码。
- `setup.py`：登记程序名 `robot_bridge`，声明 Python `pyserial`。
- `package.xml`：声明 ROS2 消息依赖。
- `robogame_bringup/config/robot.yaml`：模拟模式参数。
- `robogame_bringup/config/hardware.yaml`：真实串口覆盖参数。

## 3. 模拟模式启动和测试

```bash
ros2 run robot_bridge robot_bridge --ros-args \
  --params-file ~/robogame/ros2_ws/src/robogame_bringup/config/robot.yaml
```

发送速度：

```bash
ros2 topic pub --rate 10 /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.1, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

按 `Ctrl+C` 停止发送，观察：

```bash
ros2 topic echo /wheel_odom --once
ros2 topic echo /imu/data --once
ros2 topic echo /robot/status
```

调用模拟夹爪：

```bash
ros2 service call /gripper/grab robogame_interfaces/srv/ExecuteMechanism \
  "{command: GRAB, timeout_s: 3.0}"
```

调用模拟升降：

```bash
ros2 service call /lift/set_height robogame_interfaces/srv/SetLiftHeight \
  "{height_m: 0.1, timeout_s: 3.0}"
```

## 4. 接口

- 订阅 `/cmd_vel`。
- 发布 `/wheel_odom`、`/imu/data`、`/robot/status`。
- 提供 `/gripper/grab`、`/gripper/release`、`/lift/set_height`、`/chassis/stop`。
- 参数：`mock_mode`、`serial_port`、`baud_rate`、`command_timeout_s`、`mock_start_after_s`。

## 5. 真实串口入口

```bash
ros2 run robot_bridge robot_bridge --ros-args \
  --params-file ~/robogame/ros2_ws/src/robogame_bringup/config/robot.yaml \
  --params-file ~/robogame/ros2_ws/src/robogame_bringup/config/hardware.yaml
```

Ubuntu 串口权限可能需要：

```bash
sudo usermod -aG dialout $USER
```

执行后注销并重新登录。

## 6. 当前真实模式的关键限制

- 已能把 `vx、vy、wz` 编成 `0x01` 速度帧并写入串口。
- 能按帧格式和 CRC 从字节流提取回传帧，但没有解析任何 MCU 状态载荷。
- 真实夹爪、释放和升降服务会主动返回错误 `2001`，避免在协议未冻结时误动作。
- 当前 `/wheel_odom` 是用“上位机发出的命令速度”积分出来的，不是真实编码器反馈。
- `command_timeout_s` 目前只把程序内部速度置零，没有在定时器中主动向 MCU 补发零速度帧，因此不能代替电控端 100～200 ms 硬件超时停车。

在这些项目完成前，不能把 `hardware.launch.py` 当成可上场版本。

## 7. 必须向电控确认

- 串口设备名、波特率、字节序、发送频率和 USB 重连行为。
- `vx、vy、wz` 的单位、正方向和底盘限幅。
- 消息类型编号、序号、CRC 范围、心跳和应答机制。
- 轮速、IMU、实体启动、急停、电池、夹爪、升降和故障载荷格式。
- 通信断开后必须由下位机独立停车，不能依赖 Ubuntu 正常运行。
- 每条机构命令的完成反馈、错误码、超时和幂等行为。

## 8. 自动测试

```bash
cd ~/robogame
python3 -m unittest tests.test_serial_protocol -v
```
