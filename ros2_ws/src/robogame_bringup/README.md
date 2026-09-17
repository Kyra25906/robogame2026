# robogame_bringup 使用说明

## 1. 这个包负责什么

它是整套程序的“总开关和参数柜”：自身不做识别或控制，而是一次启动多个包，并把统一 YAML 参数交给各节点。

## 2. 文件分工

- `launch/mock_demo.launch.py`：三块目标的全软件模拟演示。
- `launch/single_cube.launch.py`：一块橙色方块的保底模拟演示。
- `launch/hardware.launch.py`：使用真实串口和真实相机节点接口的启动入口。
- `launch/line_follow_hardware.launch.py`：真实巡线最小图——`robot_bridge`（`mock_mode: false`）+ `motion_control/line_follow_controller`（`active_source: line_follow`），不含任务链其余节点。
- `launch/camera.launch.py`：相机驱动（usb_cam 640x480 @ 30fps MJPG + 静态 CameraInfo，话题 `/camera/image_raw`、`/camera/camera_info`）；`hardware.launch.py:17-21` 已 include 本文件，也可单独启动。
- `config/robot.yaml`：通用参数（公共层）、路点、视觉、导航和抓放参数；**环境开关（`mock_mode`/`runtime_mode`）不在这一层**（写进来会被配置校验判 ERROR，见 `tests/test_config_validation.py:251`）。
- `config/single_cube.yaml`：覆盖任务数量为 1 橙、0 紫。
- `config/robot_field.yaml`：现场层——`robot_bridge` 的 `mock_mode: false`、`serial_port`、`baud_rate`，`manipulator_client.runtime_mode: field`，以及现场视觉焦距。
- `config/robot_mock.yaml`：模拟层——`mock_mode: true`、`runtime_mode: mock`（`mock_demo` / `single_cube` 启动用它）。
- `config/robot_speed080.yaml`：可选限速层——把 `motion_controller.max_vx/max_vy` 提到 0.80/0.40，仅在固件已烧录 0.8/0.4 限幅后才叠加。
- `config/hardware.yaml`：**legacy，已不被任何启动入口加载**（`tools/field_console.json:7` 与 `launch/hardware.launch.py:13-14` 加载的是 `robot.yaml` + `robot_field.yaml`；`tests/test_config_validation.py:258` 只把它的漂移判为 warning）。改这里的参数不会生效。
- `setup.py`：安装 launch 和 config 文件。

## 3. 编译

修改 launch 或 YAML 后也要重新执行构建，安装目录中的副本才会更新：

```bash
cd ~/robogame/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

## 4. 启动方式

全软件三块演示：

```bash
ros2 launch robogame_bringup mock_demo.launch.py
```

全软件单块演示：

```bash
ros2 launch robogame_bringup single_cube.launch.py
```

硬件入口：

```bash
ros2 launch robogame_bringup hardware.launch.py
```

只调真实巡线链：

```bash
ros2 launch robogame_bringup line_follow_hardware.launch.py
```

只起相机：

```bash
ros2 launch robogame_bringup camera.launch.py
```

重要：`hardware.launch.py` 是入口，不等于真车验收已经完成。真实模式下 `robot_bridge` **已经**解析 MCU 的 `0x12` STATUS（`robot_bridge/node.py:1061` 的 `decode_status(frame.payload)`），GRAB/RELEASE/HOME 也会真正下发到固件（`node.py:754-777`）；唯一被拒的是 `/lift/set_height`——固件对 `LIFT_ABS` 明确回 `3010`（本车没有升降装置，C-3）。真车动作前仍须满足 `robot_bridge/README.md` §6 的动作安全门。

## 5. 启动后检查

```bash
ros2 node list
ros2 topic list
ros2 topic echo /mission/state
ros2 topic echo /robot/status
```

模拟启动应至少看到 `robot_bridge`、`localization`、`motion_controller`、`mock_perception`、`manipulator_client`、`mission_manager`。

## 6. 修改参数的规则

- 长期参数写入 YAML，不要散落在源码里。
- 每次只调整一类参数，并记录测试结果。
- 加载顺序是 `robot.yaml`（公共层）→ `robot_field.yaml`（现场）或 `robot_mock.yaml`（模拟）→ 必要时再叠 `robot_speed080.yaml`；后加载的同名参数生效。**`hardware.yaml` 是 legacy、不被任何启动入口加载，改它不会生效。**
- 比赛左右侧、路点、HSV、速度、层高和超时都应通过配置切换。
- 改完先跑单模块，再跑 `single_cube`，最后才跑完整任务。

## 7. 接现场前还需要补充

相机驱动**已经**写进 launch：`launch/camera.launch.py`（usb_cam，640x480 @ 30fps MJPG，话题 `/camera/image_raw` 与 `/camera/camera_info`），`hardware.launch.py:17-21` 已 include 它——现场临时禁用只需注释那一行。其余还需要增加静态 TF、日志/rosbag、左右场地配置、实体按钮启动检查以及明确的紧急停车联动；相机的现场依赖是装 `ros-jazzy-usb-cam`、确认 `/dev/video0` 设备路径、冻结安装高度/俯仰/遮挡（ISSUE-002）。
