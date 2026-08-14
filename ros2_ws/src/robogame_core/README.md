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
- `__init__.py`：Python 包标记。
- `setup.py`、`package.xml`、`resource/robogame_core`：ROS2 安装信息。

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

里程计积分和导航控制是冲刺期简化模型；视觉估距假设方块尺寸已知。串口V1载荷已冻结候选版并有固定测试向量，协议依据为 `docs/field/STM32_SERIAL_PROTOCOL_V1.md`；STM32端实现和真机验收尚未完成。
