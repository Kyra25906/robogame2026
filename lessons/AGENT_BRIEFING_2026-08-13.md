# 给明天辅助学习的 Agent 的说明

## 用户背景

- 低年级本科生，Python 基础 OK，正在参与 RoboGame2026 机器人比赛算法开发
- 已理解：模块化设计、状态机、分层测试、配置分离、失败安全、传输层/应用层信任分离
- 尚未系统学习：串口通信、二进制协议、传感器原理、嵌入式安全机制
- 学习风格偏好：先大白话 → 再专业名词 → 再对照代码，不要一上来就讲理论

## 今天（2026-08-12）完成的工作

修复了 `/cmd_vel` 可信状态门控缺口。用户已经理解了"传输层握手"和"应用层通信可信"是两层不同的门控——这是进入通信知识学习的很好切入点。

## 学习资源

学习路线图在：
```
lessons/HARDWARE_COMMS_LEARNING_ROADMAP.md
```

请先完整阅读这份路线图。它按 5 站组织：
1. 串口通信（UART）
2. 二进制协议与帧格式
3. 编码器与轮式里程计
4. IMU
5. 安全机制（看门狗、急停、状态机）

每站都有"大白话→概念表→对应代码→学完后能回答的问题"。

## 教学方式

1. **一次只讲一站**，不要试图一次讲完 5 站
2. 用路线图中的"大白话"部分引入，然后自然过渡到专业名词
3. **每个概念都要指到本项目代码的具体位置**——抽象概念对低年级学生没有意义
4. 讲完一个概念后问"学完后能回答"中的问题，看用户是否真懂了
5. 如果用户卡住，用 Python 交互式示例帮助理解（比如 `struct.pack` 的实操）

## 关键代码位置

```
ros2_ws/src/robogame_core/robogame_core/serial_protocol.py    ← 帧打包/解包/流式解码
ros2_ws/src/robot_bridge/robot_bridge/node.py                  ← 串口读写/握手/状态发布
ros2_ws/src/robogame_core/robogame_core/hardware_readiness.py  ← 通信健康判定
ros2_ws/src/localization/localization/node.py                  ← IMU+里程计融合
ros2_ws/src/robogame_core/robogame_core/navigation.py          ← 里程计积分
ros2_ws/src/robogame_interfaces/msg/RobotStatus.msg            ← 机器人状态消息定义
docs/field/MCU_PROTOCOL.md                                     ← MCU 协议文档
docs/field/给硬件组的交接手册_2026-08-12.md                    ← 今天给硬件组的手册
```

## 项目环境

- 仓库：`https://github.com/Kyra25906/robogame2026.git`
- 分支：`integration/robogame-t26`
- 当前 SHA：`6097958`
- Ubuntu 24.04 + ROS 2 Jazzy（用户有 VMware 虚拟机）
- 测试命令：`python3 -B -m unittest discover -s ../tests`（在 ros2_ws 目录）

## 禁止事项

- 不要修改代码（这是学习环节，不是开发环节）
- 不要猜测 MCU 协议字段（协议未冻结，乱猜会误导）
- 不要在树莓派或真车上操作
- 不要把"构建通过"说成"硬件验证通过"
