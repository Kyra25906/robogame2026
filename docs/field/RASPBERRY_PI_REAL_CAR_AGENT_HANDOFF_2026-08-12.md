# RoboGame2026 树莓派与真车部署 Agent 交接

日期：2026-08-12  
适用对象：接手“树莓派系统、固定commit部署、STM32接入及真车安全验证”的其他 Agent。  
项目仓库：`https://github.com/Kyra25906/robogame2026.git`

> **2026-08-14 更新（优先于下方历史状态）：** 当前树莓派有效部署已更新为
> `2d9514d5b3ad1d2bab91a292e3c20bae730cacc9`，目录为
> `/home/rg26/robogame_deploy_2d9514d`，旧 `2d70649` 仅作回退。
> 真实 V1 已验证 HELLO/ACK、约51Hz STATUS和心跳看门狗；ODOM/IMU仍为0帧，
> 非零速度与真实机构动作均未验收。详见 `docs/LEARNING_LOG.md` 的
> “2026-08-14”章节和 `docs/TODO_AND_ISSUES.md` 的 ISSUE-019/020。

## 1. 本对话职责

本对话只负责：

1. 全新树莓派系统配置；
2. 部署经过确认并推送的唯一 Git commit；
3. STM32串口和真实状态接入；
4. STOP、物理急停与通信失联安全；
5. 底盘低速测试；
6. 机构单动作测试；
7. 最终真实单方块闭环。

正式代码开发、修复、测试、提交和分支集成由其他代码开发对话负责。本对话不得在树莓派上长期直接修改项目代码。发现问题时应保存日志与真实数据，返回代码开发对话修复、测试、提交，再部署新的确认commit。

## 2. 用户教学要求

用户是树莓派和Linux新手。指导时必须：

- 一次只推进一个小步骤；
- 每条命令都解释各部分含义和执行目的；
- 说明必要性、技术路线、需要学习的知识、预期结果、停止条件、完成后的能力和下一步方向；
- 必须等用户返回结果后才能进入下一步；
- 可以在用户明确要求时一次给出少量连续、低风险的只读步骤，但仍应逐条解释。

不要用“直接复制执行”代替概念解释。尤其在向电控索取信息前，要先让用户理解所索取的每个项目是什么、为什么需要、缺失会造成什么风险。

## 3. 绝对安全边界

未经用户和相关责任人共同确认：

- 不连接STM32；
- 不启动`robot_bridge`；
- 不运行`hardware.launch.py`或任何整车launch；
- 不发布`/cmd_vel`；
- 不调用夹爪、升降、STOP、RETREAT等动作服务；
- 不给底盘、夹爪、升降机构接通动力；
- 不让小车运动；
- 不猜测`0x10`、`0x11`、`0x12`协议字段；
- 不用`--skip-keys`、`sudo pip`或现场改源码绕过依赖/清单错误；
- 不把电脑脏工作区直接复制到树莓派；
- 不把软件测试通过描述成硬件PASS或真车PASS。

安全相关验证必须按“无动力 → 只读 → 单项 → 低速 → 组合闭环”递进。

## 4. 树莓派硬件与系统现状

### 硬件

- Raspberry Pi 4 Model B；
- 4GB RAM；
- 32GB SanDisk microSD；
- 5V 3A USB-C电源；
- 两线5V风扇已安装，红线接物理针脚4（5V），黑线接物理针脚6（GND）；
- 风扇转动正常，实测待机CPU温度约33.1°C；
- STM32尚未连接；
- 执行器尚未供动力。

### 系统

- 主机名：`robogame-t26-rpi4`；
- 用户名：`rg26`；
- Ubuntu Server 24.04.4 LTS；
- 代号：`noble`；
- 架构：`aarch64` / `arm64`；
- 内核：`6.8.0-1060-raspi`；
- Python：`3.12.3`；
- ROS 2 Jazzy，标准路径：`/opt/ros/jazzy/setup.bash`；
- SSH已启用并验证；
- 手机热点下曾使用IPv4：`10.101.192.213`，该地址由DHCP分配，重连后可能改变；
- 网卡MAC：`98:fe:54:31:1b:9f`；
- 根分区约29GB，曾测得可用24GB；
- 未配置Swap，目前不是阻塞项。

### 屏幕

- GPIO/SPI 3.5英寸LCD上电纯白；
- 厂商提供的`LCD-show`教程主要面向Raspbian，未在Ubuntu 24.04上执行；
- 当前使用SSH无头管理，白屏不阻塞项目部署；
- 不要在正式系统卡上直接运行厂商的高影响安装脚本。

## 5. 已部署的唯一代码版本

远程分支：

```text
integration/robogame-t26
```

当前唯一有效部署commit（2026-08-14更新）：

```text
2d9514d5b3ad1d2bab91a292e3c20bae730cacc9
```

树莓派部署目录：

```text
/home/rg26/robogame_deploy_2d9514d
```

离线Git bundle：

```text
/home/rg26/robogame_2d9514d5b3ad.bundle
```

部署使用detached HEAD，最终核验：

```text
git rev-parse HEAD
2d9514d5b3ad1d2bab91a292e3c20bae730cacc9
```

构建和测试后的`git status --short`无输出。

### 已废弃版本

上一版可回退部署（不再作为当前版本）：

```text
/home/rg26/robogame_deploy_2d70649
/home/rg26/robogame_2d70649.bundle
```

```text
5923d900e02be849849a4e96c8b44a5ac53fc60b
```

该版本因8个Python包错误声明`<buildtool_depend>ament_python</buildtool_depend>`而停止部署。旧目录与bundle保留用于追溯：

```text
/home/rg26/robogame_deploy_5923d90
/home/rg26/robogame_5923d90.bundle
```

不要继续使用、覆盖或误加载旧目录。

## 6. 当前软件验收结果

新commit修复了错误的`ament_python` rosdep key，同时保留正确的：

```xml
<export>
  <build_type>ament_python</build_type>
</export>
```

树莓派实测结果：

```text
ROSDEP PASS
ARM64 BUILD PASS
9/9 PACKAGE PASS
CONFIG PASS: errors=0 warnings=0
231/231 UNITTEST PASS
NO-HARDWARE PASS
HARDWARE NOT RUN
REAL-CAR NOT RUN
```

详细证据：

- `rosdep check`最终输出：`All system dependencies have been satisfied`；
- 通过rosdep安装了真实依赖`ros-jazzy-cv-bridge`与`python3-opencv`；
- `colcon build --symlink-install`：`Summary: 9 packages finished [1min 41s]`；
- 9个包均能被`ros2 pkg prefix`发现；
- `python3 tools/validate_config.py ...`：`CONFIG PASS: errors=0 warnings=0`；
- `python3 -m unittest discover -s tests -p 'test_*.py'`：`Ran 231 tests in 2.418s`，`OK`；
- 最终进程检查：`NO HARDWARE-RELATED PROCESS`。

9个ROS 2包：

```text
cube_perception
localization
manipulator_client
mission_manager
motion_control
robogame_bringup
robogame_core
robogame_interfaces
robot_bridge
```

## 7. 分支集成背景

集成版本包含：

- 基线`main@8fd9bfc6ffa082661d2a4551c9655d33c604ee1b`；
- 视觉现场准备`codex/vision-field-readiness@d81d277c108d300ac11ed9484737af7d9a5c15e7`；
- 队友localization `feature/localization@1bf28301b81824d076aef22b72214990578290a1`。

关键集成结果：

- 保留`RobotStatus.imu_valid`安全门控；
- IMU无效或过期时回退轮式里程计；
- 包含yaw divergence、非有限数拒绝、全零四元数拒绝和pose jump检测；
- mock/field配置分离；
- 保留机构稳定观察参数和localization参数；
- navigation acceptance recorder最终只保留一份。

树莓派只部署最终集成SHA，不分别合并视觉、localization或其他分支。

## 8. 网络与部署经验

- 手机热点访问GitHub和`raw.githubusercontent.com`存在间歇性TLS/IPv6超时；
- rosdep初始化时发现IPv4较稳定、IPv6超时；曾临时用`sysctl`关闭IPv6，但未做永久配置；
- 最终代码通过Windows独立干净仓库生成Git bundle，再用SCP传至树莓派；
- Windows代理可访问GitHub，但树莓派不一定继承Windows代理；
- 不应因网络问题使用未知第三方镜像或复制电脑脏工作区。

若未来更新commit，继续使用新SHA对应的新目录和新bundle，不覆盖历史部署目录。

## 9. 当前进度位置

已经完成：

```text
树莓派系统安装
→ ROS 2 Jazzy环境
→ 固定集成commit部署
→ rosdep依赖
→ ARM64九包构建
→ 配置检查
→ 231项单元测试
→ 无硬件安全审计
```

尚未完成：

```text
协议知识教学
→ 向电控索取并冻结协议
→ STM32只读连接
→ 真实状态接入
→ STOP/急停/失联安全
→ 底盘架空与低速测试
→ 夹爪/升降单动作
→ 真实单方块闭环
```

树莓派在本轮验收后已由用户安全关机。下次使用前重新上电、查找当前IP并SSH连接。

## 10. 下一阶段的正确教学顺序

用户明确要求：在向电控索取信息前，先让其理解“我们要的都是什么东西”。因此下一位Agent不要立即丢给用户一张协议表让其转发，应按以下顺序教学。

### 第1课：物理接口

需要讲清：

- USB CDC、USB转TTL、GPIO UART、CAN和网口是不同路线；
- TX、RX、GND分别是什么；
- UART中TX/RX交叉、必须共地；
- 树莓派GPIO是3.3V逻辑，不能直接接5V TTL或RS-232电平；
- 首次联调优先考虑USB串口；
- `/dev/ttyACM0`、`/dev/ttyUSB0`、`/dev/serial0`分别通常对应什么；
- 设备名可能变化，后续需要按USB身份建立稳定udev别名。

当前已经向用户介绍过这一课的概览，但应先用提问或小结确认其理解，再进入下一课。禁止实际接线。

### 第2课：串口参数

需要解释：

- 波特率；
- 数据位；
- 校验位；
- 停止位；
- 硬件/软件流控；
- `115200 8N1`中每一部分的含义；
- 参数不一致为何会产生乱码、丢帧或完全无法通信。

### 第3课：数据帧

需要解释：

- 串口只是连续字节流，没有天然“消息边界”；
- 帧头、版本、消息类型/命令字、长度、序号、载荷、校验的作用；
- 粘包、拆包、噪声字节和重新同步；
- 为什么不能仅约定“发几个字节”。

### 第4课：字段编码

需要解释：

- 有符号/无符号整数；
- 位宽和范围；
- 大端/小端；
- 缩放系数和单位，例如mm/s、m/s、度、弧度；
- 正方向、坐标系和轮序；
- 枚举、标志位和保留位；
- 为什么不能猜测`0x10/0x11/0x12`。

### 第5课：校验、序号和应答

需要解释：

- checksum、CRC及覆盖范围；
- 序列号如何发现重复/丢失/乱序；
- ACK/NACK、错误码和超时；
- 命令“收到”和动作“完成”是不同状态。

### 第6课：状态与时间

需要解释：

- STM32应以固定频率上报什么真实状态；
- 状态时间戳/计数器；
- 电机使能、速度、里程计、IMU有效性、限位、夹爪/升降状态、故障码；
- 状态陈旧（stale）与设备离线的区别；
- 上电默认状态和复位后的状态。

### 第7课：安全语义

必须重点解释：

- 普通速度零、软件STOP、物理急停、故障锁存不是同一件事；
- 通信看门狗；
- 超时后STM32必须本地停车，不能依赖树莓派再发一次STOP；
- 上电默认禁止运动；
- 断线、进程崩溃、树莓派重启时的行为；
- 恢复通信后不得自动继续旧命令；
- 急停最好切断执行器能量，并独立于ROS/Linux；
- 清错和重新使能必须是明确动作。

### 第8课：首次联调与证据

需要解释：

- 为什么先无执行器动力只读监听；
- 保存原始十六进制字节、时间戳和解析结果；
- 逐字节对照协议；
- 注入断包、粘包、错误CRC、拔线和失联；
- 通过安全门后才允许单项发送命令。

## 11. 教学完成后向电控索取的内容

只有用户理解上述概念后，才生成并提交给电控的填写表。至少应索取：

1. 物理接口类型、连接器、引脚、电平、供电与共地方案；
2. 串口参数：波特率、数据位、校验位、停止位、流控；
3. 完整逐字节帧格式与协议版本；
4. 每个消息ID/命令字的精确定义；
5. 每个字段的偏移、长度、类型、大小端、单位、比例、范围和正方向；
6. CRC/checksum算法、初值、覆盖范围与测试向量；
7. 命令频率、状态上报频率、ACK/NACK和超时；
8. 上电、掉线、错误帧、看门狗超时和恢复通信后的行为；
9. STOP、急停、清错、使能、故障锁存的完整状态机；
10. STM32固件commit/版本号和协议兼容版本；
11. 至少一组真实发送帧、真实状态帧和逐字节解析示例；
12. 首次联调现场负责人、断电手段和物理急停确认。

收到电控资料后先做文档审查，不立即接线。若存在空白、歧义或相互矛盾，应退回补充。

## 12. 后续阶段安全门

### Gate A：协议冻结

物理、电气、逐字节协议、安全语义和固件版本均明确，无猜测项。

### Gate B：无动力只读串口

STM32可供逻辑电，但执行器动力断开；树莓派只枚举设备、保存原始字节、验证状态解析，不发送动作。

### Gate C：STOP/失联安全

验证STM32本地看门狗、拔线停车、程序退出停车、上电默认禁动、恢复不续跑和物理急停。此门未通过不得给底盘动作。

### Gate D：分机构单动作

一次只给一个机构动力：底盘先架空，再极低速短距离；夹爪和升降先空载；验证限位、超时、堵转和STOP。

### Gate E：真实单方块闭环

逐级从只检测、人工确认、低速接近、单抓、单放，最后组合为完整任务。任何失败均保存数据，回代码开发对话修复并部署新commit。

## 13. 相关本地文档

- `docs/field/RASPBERRY_PI_DEPLOYMENT_LOG_2026-08-10.md`：完整树莓派配置和部署记录；
- `docs/field/REAL_CAR_HARDWARE_PRELIMINARY_INVENTORY_2026-08-09.md`：真车硬件初步清单；
- `docs/field/MECHANICAL_PARAMETERS_CONFIRMATION.md`：机械参数确认；
- `docs/vision/CURRENT_STATUS_HANDOFF_REAL_CAMERA_2026-08-11.md`：视觉模块当前状态交接；
- `docs/field/MCU_PROTOCOL.md`或其当前实际位置：MCU协议相关项目文档，使用前应核对最新集成commit内容，不能视作电控已确认的真实协议。

## 14. 接手Agent的第一步

不要立即向电控发问题清单。先从“第1课：物理接口”检查用户理解：让用户用自己的话说明USB串口与GPIO UART的区别，以及TX、RX、GND为什么这样连接。确认理解后，再一次只讲“串口参数与115200 8N1”。

在整个教学阶段保持树莓派关机、STM32未连接、执行器无动力。
