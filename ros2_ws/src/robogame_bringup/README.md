# robogame_bringup 使用说明

## 1. 这个包负责什么

它是整套程序的“总开关和参数柜”：自身不做识别或控制，而是一次启动多个包，并把统一 YAML 参数交给各节点。

## 2. 文件分工

- `launch/mock_demo.launch.py`：三块目标的全软件模拟演示。
- `launch/single_cube.launch.py`：一块橙色方块的保底模拟演示。
- `launch/hardware.launch.py`：使用真实串口和真实相机节点接口的启动入口。
- `config/robot.yaml`：通用参数、模拟开关、路点、视觉、导航和抓放参数。
- `config/single_cube.yaml`：覆盖任务数量为 1 橙、0 紫。
- `config/hardware.yaml`：覆盖 `robot_bridge` 为真实串口模式。
- `setup.py`：安装 launch 和 config 文件。

## 3. 编译

修改 launch 或 YAML 后也要重新执行构建，安装目录中的副本才会更新：

```bash
cd ~/robogame/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

## 4. 三种启动方式

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

重要：`hardware.launch.py` 只是入口已准备，不表示真实硬件协议已经实现。当前 `robot_bridge` 在真实模式下仍未解析 MCU 状态，也会拒绝真实夹爪和升降命令。

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
- `hardware.yaml` 后加载，因此会覆盖 `robot.yaml` 中同名参数。
- 比赛左右侧、路点、HSV、速度、层高和超时都应通过配置切换。
- 改完先跑单模块，再跑 `single_cube`，最后才跑完整任务。

## 7. 接现场前还需要补充

相机驱动目前没有写入 launch；硬件版启动前需要确定具体相机包和话题名。还需要增加静态 TF、日志/rosbag、左右场地配置、实体按钮启动检查以及明确的紧急停车联动。
