# RoboGame 2026 robot software

面向 RoboGame 2026 的 ROS 2 机器人软件仓库。项目把可独立测试的算法放在 `robogame_core`，把相机、ROS 2、串口和机构接入保留在各功能节点中，使没有真车时也能开发和验证大部分软件逻辑。

> 自动测试和模拟流程通过不等于真车完成。正式上场前仍需完成相机、底盘、IMU、夹爪、急停和通信失联的实机验收。
>
> **2026-09-18 更正（以代码为准）**：真车**没有升降机构**——`LIFT_ABS` 由固件直接以错误码 `3010`（`RPI_ERROR_MECH_NO_LIFT`，`Four_Motor_PID_Test_1/.../Core/Src/rpi_protocol.c:217,766-775`）拒绝，放置是**单层地面放置**。本文原先把「升降」列为待验收子系统，是 8 月的旧结构，已删。另外「横移（VY）」的硬件结论与软件行为**不一致**，见下。

## 当前九个功能包

当前仓库包含九个主要 ROS 2 功能包，这是现阶段的功能划分，不是永久数量限制。以后如果增加相机驱动、系统诊断、仿真或新的硬件适配包，应同步更新本表和启动配置。

| 功能包 | 白话职责 | 详细说明 |
|---|---|---|
| `cube_perception` | 从相机画面寻找橙色、紫色方块，输出距离和左右偏差 | [使用说明](ros2_ws/src/cube_perception/README.md) |
| `localization` | 根据轮式里程计和 IMU 估计小车在哪里、朝向哪里 | [使用说明](ros2_ws/src/localization/README.md) |
| `motion_control` | 巡线（`line_follow_controller`：跟线、路口转弯、坡道）与按目标位置生成底盘速度，并处理限速、授权门控、超时和停车 | [使用说明](ros2_ws/src/motion_control/README.md) |
| `manipulator_client` | 根据视觉低速对准方块，协调夹爪、升降和释放 | [使用说明](ros2_ws/src/manipulator_client/README.md) |
| `mission_manager` | 决定寻找、抓取、搬运、放置和撤退的任务顺序 | [使用说明](ros2_ws/src/mission_manager/README.md) |
| `robot_bridge` | 在 ROS 2 与 MCU 串口、底盘和机械机构之间翻译数据 | [使用说明](ros2_ws/src/robot_bridge/README.md) |
| `robogame_interfaces` | 定义所有模块共同使用的消息和服务合同 | [使用说明](ros2_ws/src/robogame_interfaces/README.md) |
| `robogame_core` | 保存不依赖 ROS 2 和硬件的视觉、导航、任务与协议逻辑 | [使用说明](ros2_ws/src/robogame_core/README.md) |
| `robogame_bringup` | 集中保存参数，并统一启动模拟或真实系统 | [使用说明](ros2_ws/src/robogame_bringup/README.md) |

## 系统怎样连起来

```text
相机 → cube_perception → /cubes ───────────────┐
                                               v
任务路点 → mission_manager → motion_control → /cmd_vel ─→ robot_bridge ↔ 电控 MCU
              │  授权 /mission/active_source         ↑
              v                                      │
      line_follow_controller ← /line_sensor ─────────┘（MCU 0x14 巡线遥测解码）
              │ /line_follow/status
              └──────────────────────────→ mission_manager（段推进判据）

编码器/IMU → localization → /pose → mission_manager / motion_control

mission_manager → manipulator_client → 夹爪服务 → 机械机构
```

- 算法决定目标、速度和动作顺序，并根据反馈继续、重试或停车。
- 巡线节点（`line_follow_controller`）只有在任务层通过 `/mission/active_source` 授权后才允许驱动底盘（`require_authorization`），并上送 `/line_follow/status` 供任务层判断段是否走完。**注意**：真车上 `/line_sensor` 的发布者目前还是 mock（`motion_control/line_follow_node.py:14` 自述），验收时要切到 `robot_bridge` 的 0x14 解码结果。
- 电控执行底盘命令，回传编码器、IMU和机器人状态，并独立保证急停与失联停车。
- 机械提供**夹爪**的行程、限位、完成证据、故障判据和安全行为（真车无升降，见文首更正）。
- `robot_bridge`负责 ROS 2 与真实串口协议之间的转换。

> **未验证项 + 未拍板的冲突（2026-09-18 记录；同日更正措辞）**：有一条 08-19 当面记录（`docs/field/给机械组现场问答表_2026-08-19.md` 的 C-6）称「底盘只能前后 + 原地转；左右平移**偏差过大不可用**」——但它**没有实测数字、没有落地横移测试记录**，仓库里唯一的实测证据方向相反：08-18 架空实测「左移（+vy）、右移（−vy）转向组合正确 ✅」（`docs/field/RASPBERRY_PI_DEPLOYMENT_LOG_2026-08-18.md:22-24`），固件也**实现了** vy（`chassis.h:36,73` 限幅 0.4 m/s、`chassis.c:23` 符号翻转），电控侧仍把「vx/vy/wz 最大速度实测值」列为**待测**（`docs/field/给电控组待确认清单.md:101`）。所以「横移不可用」目前是**未验证的说法，不是硬件侧已确认的事实**（"能用"同样未验证）。
>
> 而软件侧**确实没有屏蔽** vy：`navigation.py` 按 `ky: 1.2 / max_vy: 0.30`（`robot.yaml:32,38`）下发 `/cmd_vel.linear.y`，路线里也有侧移段（`mission_route.py` S11/S14、`work_sequence.py` 0.15/0.11 m），`cmd_vel_arbiter.py:91` 不滤 vy。
>
> 上车前要定两件事：① **落地实测横移偏差**（0.2～0.3 m ×5，只看现象）；② 测出之前要不要先把 vy 钳 0（当前**没做**）。

三方不能单独猜测单位、方向、载荷或完成条件。详细接口、未冻结字段和确认清单见[算法给电控与机械的接口说明](docs/field/算法给电控与机械的接口说明.md)，串口字节定义见[STM32 串口协议 V1](docs/field/STM32_SERIAL_PROTOCOL_V1.md)（旧的 `docs/field/MCU_PROTOCOL.md` 已删除，消息编号过期，勿再引用）。

<!-- docs-audit: allow path-missing docs/field/MCU_PROTOCOL.md -->
<!-- 上面这行是给 tools/docs_audit.py 看的显式豁免：本段**就是在提醒**该文件已删除。 -->

## 当前状态

已经具备：

- 九个功能包、共用消息/服务和统一启动入口；
- 不依赖硬件的核心算法与自动测试；
- 模拟桥接、模拟视觉和单块任务演示；
- 图片/录像视觉处理、HSV/ROI、连续帧确认和结果记录；
- 运动控制限速、超时、越界和状态过期保护；
- 巡线（跟线、路口转弯、坡道限速）、巡线黑白标定与落盘、开赛前的标定门控；
- 串口外层帧、CRC、速度载荷和流解码；
- 抓放协调流程和软件安全联锁。

仍需真实条件完成：

- 正式方块、相机、光照、ROI、HSV和焦距标定；
- MCU状态、编码器、IMU和机构命令的真实载荷；
- 底盘方向、里程计、IMU和运动参数的实车标定；
- 抓取证据、掉块检测和**单层地面**放置高度（`robot.yaml:132` 现为 `[0.10, 0.20, 0.30]`，与计划要求的 `[0.10, 0.10, 0.20]` 不一致，尚未拍板）；
- 巡线参数（kp/kd/vx_base/阈值）与真车黑白标定值——代码里全是占位值；
- 急停、看门狗、通信失联和整车低速闭环测试。

## 文档入口

完整文档分类（哪些还算数、哪些已过期、哪些根本没进版本库）见 [文档盘点与分类](docs/DOC_INVENTORY.md)；按任务找入口见 [docs 文档导航](docs/README.md)。

| 角色或任务 | 文档 |
|---|---|
| **第一次上车执行（B2/B3/B4 全流程，逐步照做）** | [首次上车执行清单](docs/field/首次上车执行清单.md) |
| **上电后无人干预完赛（为什么这样做）** | [上电自主完赛流程](docs/field/上电自主完赛流程.md) |
| 第一次安装、编译和运行 | [快速开始](docs/guides/GETTING_STARTED.md) |
| **现场联调（到现场先读这个）** | [树莓派 SSH + 网页操作指南](docs/guides/RASPBERRY_PI_SSH_AND_WEB_GUIDE.md) |
| 现场上车前/中/后对照执行 | [现场上车 Checklist](docs/field/FIELD_SESSION_CHECKLIST.md) |
| 真车对接唯一执行文档 | [真车对接设计稿 2026-08-19](docs/field/真车对接设计稿_2026-08-19.md) |
| 电控/机械与算法共同确认接口 | [算法给电控与机械的接口说明](docs/field/算法给电控与机械的接口说明.md) |
| MCU串口实现 | [STM32 串口协议 V1](docs/field/STM32_SERIAL_PROTOCOL_V1.md) |
| 整车逐级联调 | [集成检查清单](docs/field/INTEGRATION_CHECKLIST.md) |
| 视觉开发与验收 | [视觉专题说明](docs/vision/VISION_MODULE.md) |
| 两位算法同学职责边界 | [两人算法最终分工](docs/team/两人算法最终分工.md) |
| GitHub分支和提交协作 | [GitHub两人代码协作说明](docs/team/GitHub两人代码协作说明.md) |

## 快速开始

Ubuntu 环境配置、编译和第一轮启动步骤见[快速开始](docs/guides/GETTING_STARTED.md)。建议先运行自动测试和模拟系统，再按照[集成检查清单](docs/field/INTEGRATION_CHECKLIST.md)逐级接入真实硬件，不要第一次联调就直接运行完整任务。
