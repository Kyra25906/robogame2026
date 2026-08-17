# robot_bridge：树莓派串口与机械臂控制说明

## 1. 功能边界

`robot_bridge` 负责在 ROS2 与 STM32 串口协议 V1 之间转换命令和状态。当前树莓派侧已经实现机械命令状态机，但这不代表真实机械臂已经可动；在 STM32 支持 `0x20/0x21` 前，只能使用 PTY 模拟验收。

机械命令链路：

```text
ROS2服务 → 安全检查 → 0x20命令 → ACK → 0x21状态 → ROS2结果
```

只有匹配命令的 `SUCCEEDED` 且 `error_code=0` 才返回成功。

## 2. ROS2接口

| 服务 | 类型 | 命令 |
|---|---|---|
| `/gripper/grab` | `ExecuteMechanism` | `GRAB` |
| `/gripper/release` | `ExecuteMechanism` | `RELEASE` |
| `/mechanism/home` | `ExecuteMechanism` | `HOME` |
| `/mechanism/stop` | `ExecuteMechanism` | `STOP` |
| `/lift/set_height` | `SetLiftHeight` | 绝对高度，单位米 |
| `/chassis/stop` | `ExecuteMechanism` | 全局底盘急停，不等同于机构STOP |

订阅 `/cmd_vel`，发布 `/wheel_odom`、`/imu/data` 和 `/robot/status`。

## 3. 机械传输参数

`hardware.yaml` 默认值：

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `mechanism_ack_timeout_s` | `0.1` | 每次发送后等待帧ACK的时间 |
| `mechanism_status_timeout_s` | `0.3` | ACK或中间状态后等待下一状态的时间 |
| `mechanism_max_attempts` | `3` | 同一命令最多发送次数，包含首次发送 |

重试时串口帧 `sequence` 更新，但业务 `command_id` 保持不变。超时参数必须是有限正数，最大尝试次数必须为正整数，否则节点拒绝启动。

服务请求中的 `timeout_s` 是整条业务命令的上层期限，不应小于单次协议等待时间。LIFT请求会把米转换为整数毫米。

## 4. Ubuntu启动

```bash
cd ~/robogame2026-integration/ros2_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 run robot_bridge robot_bridge --ros-args \
  --params-file src/robogame_bringup/config/hardware.yaml
```

确认串口权限：

```bash
ls -l /dev/serial/by-id/
groups
```

用户通常需要属于 `dialout` 组。修改用户组后需要注销并重新登录。

## 5. 服务调用示例

```bash
ros2 service call /gripper/grab robogame_interfaces/srv/ExecuteMechanism \
  "{command: GRAB, timeout_s: 3.0}"

ros2 service call /gripper/release robogame_interfaces/srv/ExecuteMechanism \
  "{command: RELEASE, timeout_s: 3.0}"

ros2 service call /mechanism/home robogame_interfaces/srv/ExecuteMechanism \
  "{command: HOME, timeout_s: 5.0}"

ros2 service call /mechanism/stop robogame_interfaces/srv/ExecuteMechanism \
  "{command: STOP, timeout_s: 1.0}"

ros2 service call /lift/set_height robogame_interfaces/srv/SetLiftHeight \
  "{height_m: 0.123, timeout_s: 5.0}"
```

## 6. 动作安全门

真实模式发送机械命令前必须同时满足：

- 串口已打开；
- HELLO/ACK握手完成；
- 最新STATUS有效且通信未超时；
- 没有急停；
- 没有机构故障；
- 物理启动授权有效；
- 没有另一条普通机械命令正在运行。

`/mechanism/stop` 可以取消活动命令并建立自己的STOP状态机。串口断开、MCU重启、急停或机构故障都会取消活动命令。

## 7. 错误码

| 错误码 | 含义 |
|---:|---|
| `0` | 成功 |
| `4` | 参数或超时非法 |
| `5` | 没有物理启动授权 |
| `6` | 已有机械命令正在运行 |
| `7` | 示例ACK拒绝码；实际非零ACK码原样返回 |
| `8` | 活动命令被机构STOP取消 |
| `9` | 不支持的真实机械命令 |
| `42` | 示例MCU执行错误；实际MCU终态错误码原样返回 |
| `2002` | 整条机械命令超过业务期限 |
| `9001` | 急停或全局STOP中断命令 |
| `9003` | 串口不可用、写失败或协议响应超时 |
| `9004` | MCU `boot_id`变化，旧会话命令作废 |
| `9006` | 机构故障或非法状态转换 |

## 8. 树莓派侧验收

不连接真实STM32时，在Ubuntu隔离环境运行：

```bash
source /opt/ros/jazzy/setup.bash
source ros2_ws/install_mechanism_test/setup.bash
python3 -m unittest tests.test_robot_bridge_mechanism_integration -v
```

PTY套件覆盖正常操作、ACK/状态超时、重试、错误终态、急停、MCU重启、命令互斥、STOP抢占以及串口断线重连。真实机械臂联调必须等STM32实现并经过单独授权。
