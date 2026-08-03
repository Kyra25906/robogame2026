# robogame_interfaces 使用说明

## 1. 这个包负责什么

它是九个包共同使用的“统一表格和合同”：规定一条消息有哪些字段、单位和含义。它不运行节点，也不控制硬件。

## 2. 文件分工

### 消息 `msg`

- `RobotStatus.msg`：通信、急停、实体启动、夹爪、方块、故障和电池状态。
- `CubeDetection.msg`：单个方块的颜色、置信度、距离、横向偏差等。
- `CubeDetectionArray.msg`：一帧中多个方块检测。
- `MissionState.msg`：任务状态、结果、错误码、重试数和说明。
- `CargoState.msg`：算法认为的车载橙/紫方块数量。

### 服务 `srv`

- `ExecuteMechanism.srv`：抓取、释放或停车类命令及完成结果。
- `SetLiftHeight.srv`：升降目标高度、超时和完成结果。

### 构建文件

- `CMakeLists.txt`：调用 `rosidl_generate_interfaces` 生成各语言接口。
- `package.xml`：声明生成器和运行依赖。

## 3. 编译和查看

```bash
cd ~/robogame/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-select robogame_interfaces
source install/setup.bash
```

查看接口：

```bash
ros2 interface show robogame_interfaces/msg/RobotStatus
ros2 interface show robogame_interfaces/msg/CubeDetection
ros2 interface show robogame_interfaces/srv/ExecuteMechanism
```

查看当前有哪些接口：

```bash
ros2 interface list | grep robogame_interfaces
```

## 4. 新手必须知道的规则

- `stamp` 是这份数据产生的时间，不是收到它的时间。
- 距离、高度用米，角度和角速度用弧度。
- `success=true` 只能表示动作经过反馈确认完成，不能表示“命令已发出”。
- `error_code=0` 表示无错误；非零编号要和电控、机械共同维护含义。
- 修改接口后，所有依赖包都要重新编译并重新加载 `install/setup.bash`。

## 5. 接硬件前必须协商

需要三组共同确认每个状态由谁产生、更新频率、超时、错误码、有效范围和故障后的安全动作。特别要补充轮速/编码器原始反馈、机构占用状态、升降当前位置、夹爪抓到证据、车载数量传感器和掉块状态。接口一旦冻结，不应由某一组单方面改字段。
