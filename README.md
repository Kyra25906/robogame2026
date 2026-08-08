# RoboGame 2026 robot software

面向 RoboGame 2026 的 ROS 2 机器人软件仓库。项目把可独立测试的算法放在 `robogame_core`，把相机、ROS 2、串口和机构接入保留在各功能节点中，使没有真车时也能开发和验证大部分软件逻辑。

> 自动测试和模拟流程通过不等于真车完成。正式上场前仍需完成相机、底盘、IMU、夹爪、升降、急停和通信失联的实机验收。

## 当前九个功能包

当前仓库包含九个主要 ROS 2 功能包，这是现阶段的功能划分，不是永久数量限制。以后如果增加相机驱动、系统诊断、仿真或新的硬件适配包，应同步更新本表和启动配置。

| 功能包 | 白话职责 | 详细说明 |
|---|---|---|
| `cube_perception` | 从相机画面寻找橙色、紫色方块，输出距离和左右偏差 | [使用说明](ros2_ws/src/cube_perception/README.md) |
| `localization` | 根据轮式里程计和 IMU 估计小车在哪里、朝向哪里 | [使用说明](ros2_ws/src/localization/README.md) |
| `motion_control` | 根据目标位置生成底盘速度，并处理限速、超时和停车 | [使用说明](ros2_ws/src/motion_control/README.md) |
| `manipulator_client` | 根据视觉低速对准方块，协调夹爪、升降和释放 | [使用说明](ros2_ws/src/manipulator_client/README.md) |
| `mission_manager` | 决定寻找、抓取、搬运、放置和撤退的任务顺序 | [使用说明](ros2_ws/src/mission_manager/README.md) |
| `robot_bridge` | 在 ROS 2 与 MCU 串口、底盘和机械机构之间翻译数据 | [使用说明](ros2_ws/src/robot_bridge/README.md) |
| `robogame_interfaces` | 定义所有模块共同使用的消息和服务合同 | [使用说明](ros2_ws/src/robogame_interfaces/README.md) |
| `robogame_core` | 保存不依赖 ROS 2 和硬件的视觉、导航、任务与协议逻辑 | [使用说明](ros2_ws/src/robogame_core/README.md) |
| `robogame_bringup` | 集中保存参数，并统一启动模拟或真实系统 | [使用说明](ros2_ws/src/robogame_bringup/README.md) |

## 系统怎样连起来

```text
相机 → cube_perception → /cubes ─────────────┐
                                             v
任务路点 → mission_manager → motion_control → /cmd_vel
                                             |
                                             v
                                      robot_bridge ↔ 电控 MCU
                                             ^
                                             |
编码器/IMU → localization → /pose ───────────┘

mission_manager → manipulator_client → 夹爪/升降服务 → 机械机构
```

- 算法决定目标、速度和动作顺序，并根据反馈继续、重试或停车。
- 电控执行底盘命令，回传编码器、IMU和机器人状态，并独立保证急停与失联停车。
- 机械提供夹爪和升降的行程、限位、完成证据、故障判据和安全行为。
- `robot_bridge`负责 ROS 2 与真实串口协议之间的转换。

三方不能单独猜测单位、方向、载荷或完成条件。详细接口、未冻结字段和确认清单见[算法给电控与机械的接口说明](docs/field/算法给电控与机械的接口说明.md)，串口字节定义见[MCU协议](docs/field/MCU_PROTOCOL.md)。

## 当前状态

已经具备：

- 九个功能包、共用消息/服务和统一启动入口；
- 不依赖硬件的核心算法与自动测试；
- 模拟桥接、模拟视觉和单块任务演示；
- 图片/录像视觉处理、HSV/ROI、连续帧确认和结果记录；
- 运动控制限速、超时、越界和状态过期保护；
- 串口外层帧、CRC、速度载荷和流解码；
- 抓放协调流程和软件安全联锁。

仍需真实条件完成：

- 正式方块、相机、光照、ROI、HSV和焦距标定；
- MCU状态、编码器、IMU和机构命令的真实载荷；
- 底盘方向、里程计、IMU和运动参数的实车标定；
- 抓取证据、掉块检测、升降零点和三层高度；
- 急停、看门狗、通信失联和整车低速闭环测试。

## 文档入口

完整文档分类和当前执行入口见 [docs 文档导航](docs/README.md)。

| 角色或任务 | 文档 |
|---|---|
| 第一次安装、编译和运行 | [快速开始](docs/guides/GETTING_STARTED.md) |
| 电控/机械与算法共同确认接口 | [算法给电控与机械的接口说明](docs/field/算法给电控与机械的接口说明.md) |
| MCU串口实现 | [MCU协议](docs/field/MCU_PROTOCOL.md) |
| 整车逐级联调 | [集成检查清单](docs/field/INTEGRATION_CHECKLIST.md) |
| 视觉开发与验收 | [视觉专题说明](docs/vision/VISION_MODULE.md) |
| 两位算法同学职责边界 | [两人算法最终分工](docs/team/两人算法最终分工.md) |
| GitHub分支和提交协作 | [GitHub两人代码协作说明](docs/team/GitHub两人代码协作说明.md) |

## 快速开始

Ubuntu 环境配置、编译和第一轮启动步骤见[快速开始](docs/guides/GETTING_STARTED.md)。建议先运行自动测试和模拟系统，再按照[集成检查清单](docs/field/INTEGRATION_CHECKLIST.md)逐级接入真实硬件，不要第一次联调就直接运行完整任务。
