# robogame_core 使用说明

## 1. 这个包负责什么

它保存不依赖 ROS2、不依赖相机和串口的纯算法。这样做的好处是：没有小车时也能在普通 Python 中学习、运行和自动测试；ROS2 节点只负责收发消息。

这个包没有 `ros2 run` 入口，这是正常设计，不是缺文件。

## 2. 文件分工

- `models.py`：颜色、任务结果、二维位姿、速度和车载方块数量。
- `navigation.py`：角度归一化、限速、到点 P 控制和简化里程计积分。
- `perception.py`：视觉候选结构、置信度、针孔估距和目标选择。
- `mission.py`：任务状态机、有限重试、安全停止和橙—橙—紫顺序。
- `serial_protocol.py`：串口帧、CRC16、V1各类载荷编解码和字节流重新同步。
- `route_loader.py`：把场地图 yaml 读成自检过的 `RoutePlan`（纯逻辑，零 ROS）。
- `mission_route.py`：全流程路线（比赛 run）的段数据与段推进（纯算法，零 ROS）。
- `mission_run.py`：任务运行循环——把「当前段 → 该发什么命令」的决策收在一处（纯逻辑）。
- `work_sequence.py`：作业段的动作序列，如 W02 连取 3 块、W04 搭 2 层（纯逻辑）。
- `junction_turn.py`：路口转弯器，「到路口了、转过去了」由巡线自己判断（纯逻辑，零 ROS）。
- `arm.py`：机械臂关节表（编号、脉宽、行程）、角度↔脉宽换算和 ARM_SET 拒绝错误码。
- `field_map.py`：RoboGame 2026 场地图数据模型（规则推导 + 现场回填）。
- `__init__.py`：Python 包标记。
- `setup.py`、`package.xml`、`resource/robogame_core`：ROS2 安装信息。

本目录共 33 个 `.py`（`robogame_core/robogame_core/` 下 32 个模块加一个 `setup.py`），上面只列了与 ROS2 节点直接对接或最常被问到的主要模块，完整清单看目录本身。

## 3. 如何使用

其他包直接导入，例如：

```python
from robogame_core.navigation import GoToPoseController
from robogame_core.mission import MissionMachine
```

先构建并加载环境：

```bash
cd ~/robogame/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-select robogame_core
source install/setup.bash
```

检查是否能导入：

```bash
python3 -c "from robogame_core.models import Pose2D; print(Pose2D(1.0, 2.0, 0.0))"
```

## 4. 自动测试

```bash
cd ~/robogame
python3 -m unittest discover -s tests -v
```

也可以只测某一部分：

```bash
python3 -m unittest tests.test_navigation -v
python3 -m unittest tests.test_mission -v
python3 -m unittest tests.test_serial_protocol -v
python3 -m unittest tests.test_perception -v
```

## 5. 修改原则

纯数学、状态转移和协议编码优先写在这里；ROS2 话题名称、相机读取和串口打开不要放进这里。每次修改核心逻辑都应先添加自动测试，再接回 ROS2 节点。

## 6. 当前限制

里程计积分和导航控制是冲刺期简化模型；视觉估距假设方块尺寸已知。串口V1载荷已冻结候选版并有固定测试向量，协议依据为 `docs/field/STM32_SERIAL_PROTOCOL_V1.md`。

STM32 侧的机械命令通道（0x20/0x21）**已经在固件里实现**，例如 `Four_Motor_PID_Test_1/Four_Motor_PID_Test/Core/Src/rpi_protocol.c:766-776` 的 `RPI_MECH_OP_LIFT_ABS` 分支会明确回 `FAILED` + 错误码 `3010`（本车没有升降装置）。真正**尚未完成**的是真机验收：串口权限、时序、错误码透传和故障恢复都需要现场逐项确认。以上只是静态代码事实，不能证明真车动作已经可用。
