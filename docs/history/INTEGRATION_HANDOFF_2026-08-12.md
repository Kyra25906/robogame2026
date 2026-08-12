# RoboGame2026 非视觉代码与总集成交接（2026-08-12）

## 1. 文档用途

本文件是当前唯一集成分支的正式交接入口，用于后续代码开发、树莓派部署和真车验收。

本文件记录的是“代码已经验证到哪一步、接下来允许做什么”，不是硬件合格证明。任何部署和现场测试都必须记录完整 commit SHA，不能只写“最新版”。

## 2. 唯一集成来源

仓库：

```text
https://github.com/Kyra25906/robogame2026.git
```

唯一集成分支：

```text
integration/robogame-t26
```

集成基线：

```text
main@8fd9bfc6ffa082661d2a4551c9655d33c604ee1b
```

已合入的视觉版本：

```text
codex/vision-field-readiness
d81d277c108d300ac11ed9484737af7d9a5c15e7
```

已合入的 localization 版本：

```text
feature/localization
1bf28301b81824d076aef22b72214990578290a1
```

最终部署时必须使用本文件所在提交的完整 SHA。不要让树莓派分别检出或拼接视觉、localization和其他分支。

## 3. 当前集成内容

ROS 2 Jazzy工作区包含9个包：

1. `robogame_interfaces`
2. `robogame_core`
3. `cube_perception`
4. `manipulator_client`
5. `mission_manager`
6. `motion_control`
7. `robot_bridge`
8. `robogame_bringup`
9. `localization`

关键集成结果：

- 保留 `RobotStatus.imu_valid` 安全门控；
- IMU无效、缺失、过期或非有限时降级为轮式里程计；
- 合入 yaw divergence、非有限里程计拒绝、全零四元数拒绝和 pose jump检测；
- `robot.yaml`同时保留机构稳定观察参数和 localization参数；
- `robot_mock.yaml`与`robot_field.yaml`保持环境隔离；
- field模式不接受模拟放置证据；
- navigation acceptance recorder最终只有一份`tools/navigation_acceptance.py`；
- 8个Python ROS 2包已移除错误的`ament_python` rosdep依赖声明；
- `ament_python`仍正确保留为每个Python包的构建类型；
- Windows向Ubuntu发送验收脚本前会将CRLF转换为LF。

## 4. 已完成验证

### 4.1 Windows纯软件验证

```text
全量单元测试：231/231 PASS
package.xml专项检查：2/2 PASS
```

专项测试检查：

- 工作区恰好包含9个有效`package.xml`；
- 8个Python包使用`ament_python`构建类型；
- `ament_python`不得再次作为rosdep依赖键；
- localization选择IMU时必须传入`RobotStatus.imu_valid`；
- localization发布路径保留非有限数、全零四元数和pose jump拒绝。

### 4.2 Ubuntu 24.04 / ROS 2 Jazzy无硬件验证

在独立验收目录按固定SHA完成：

```text
rosdep check：All system dependencies have been satisfied
colcon build：Summary: 9 packages finished
配置检查：CONFIG PASS: errors=0 warnings=0
单元测试：Ran 231 tests / OK
硬件相关残留进程：无
```

本轮没有启动ROS 2节点、`robot_bridge`、机构验收工具或整车launch，也没有连接STM32。

### 4.3 当前结论等级

可以声明：

```text
SOFTWARE TEST PASS
UBUNTU BUILD PASS
NO-HARDWARE PASS
```

不能声明：

```text
ARM64 BUILD PASS（需树莓派对本文件所在新SHA重新验证）
STM32 PASS
HARDWARE PASS
REAL-CAR PASS
```

## 5. 树莓派部署要求

树莓派只允许：

1. 在新的独立目录克隆仓库；
2. 按本文件所在完整SHA执行detached checkout；
3. 检查`git rev-parse HEAD`与交付SHA完全一致；
4. 检查`git status --short`无输出；
5. 执行`rosdep check`及正常依赖安装；
6. 执行9包`colcon build --symlink-install`；
7. 执行配置检查和231项纯单元测试；
8. 保存环境、构建、测试和警告记录。

当前禁止：

- 连接STM32；
- 启动`robot_bridge`；
- 运行`hardware.launch.py`或整车launch；
- 发布非零`/cmd_vel`；
- 调用真实夹爪、升降、STOP或RETREAT；
- 在树莓派修改正式源码；
- 使用`--skip-keys`绕过无法解析的rosdep声明；
- 把构建通过写成硬件或真车通过。

## 6. 当前已知P0问题

### 6.1 `/cmd_vel`可信状态门控

已审计到以下风险：外层合法的`0x12 STATUS`帧可能完成握手，但payload尚未完整解码，`communication_ok`仍为false；当前非零速度门控需要增加“可信且新鲜的RobotStatus”条件。

下一轮最小代码任务：

```text
先写失败回归测试
→ 未解码STATUS不得解锁非零cmd_vel
→ 最小修复门控
→ 全量测试
→ Ubuntu九包构建
→ 生成新部署SHA
```

在该问题修复前，不允许连接STM32后发送非零速度。

### 6.2 MCU真实payload尚未冻结

仍缺：

- `0x10 ODOM`逐字节布局和真实十六进制样例；
- `0x11 IMU`逐字节布局和真实十六进制样例；
- `0x12 STATUS`逐字节布局和真实十六进制样例；
- 长度、类型、单位、缩放、值域、序号和时间戳规则。

未知协议不得猜测实现。只有完整payload解码和值域检查成功，才能刷新状态新鲜时间。

### 6.3 真实机构契约尚未冻结

仍缺：

- GRAB、LIFT、RELEASE、STOP的消息类型与payload；
- 动作完成条件和统一错误码；
- STOP/CANCEL是否真正抢占并停止执行器；
- RETREAT责任模块及完成证据；
- 机械高度、速度、夹持力、允许误差和安全极限。

在契约冻结前，真实机构服务继续以错误码`2001`拒绝请求。

## 7. 后续阶段

### 阶段1：树莓派ARM64无硬件验收

目标：证明本文件所在SHA可以在比赛树莓派上完成rosdep、九包构建、配置检查和231项测试。

### 阶段2：远程安全代码补强

目标：修复非零`/cmd_vel`必须依赖可信、新鲜RobotStatus的门控缺口。

### 阶段3：机械与电控接口冻结

目标：冻结机械物理参数、动作完成判据、STOP/CANCEL、RETREAT责任以及MCU逐字节协议。

### 阶段4：协议离线实现

目标：先将真实十六进制样例写成固定输入测试，再实现ODOM、IMU、STATUS和机构协议适配器。不得直接在真车上试错解析器。

### 阶段5：真车只读通信

目标：连接STM32后只观察帧、状态、新鲜度、重启和断线恢复，不发送运动或机构命令。

### 阶段6：安全功能与单动作

目标：依次验证上电不动、STOP、急停、失联停车、低速底盘单轴、机构空载和机构带载。

### 阶段7：模块联合运行

目标：依次联调底盘与定位、视觉与对准、视觉与机构、放置与撤退观察。

### 阶段8：完整任务与可靠性

目标：从单方块第一层低速闭环开始，重复验证后再逐步增加层数、颜色和任务规模。

## 8. 现场安全顺序

```text
接口冻结
→ 只读通信
→ 状态可信度
→ STOP
→ 急停
→ 失联停车
→ 底盘低速单轴
→ 机构空载
→ 机构带载
→ RETREAT
→ 单方块闭环
```

STOP、急停和失联停车没有通过前，不运行完整任务。

## 9. 版本和反馈规则

- 每次部署记录完整代码SHA；
- 同时记录机械版本、STM32固件版本和配置版本；
- 树莓派上的临时修改只算诊断实验，不算正式代码；
- 真车反馈必须带回电脑端正式修改、测试、提交和推送；
- 树莓派不能成为唯一代码来源；
- 每次更新都生成新的集成SHA，旧SHA保留用于追溯和回滚。

## 10. 下一位负责人开始前的检查

```bash
git branch --show-current
git rev-parse HEAD
git status --short
git log -5 --oneline --decorate
```

预期：

- 位于`integration/robogame-t26`或从本文件所在SHA创建新的工作分支；
- HEAD与交接提供的完整SHA一致；
- 工作树干净；
- 不直接在个人文档较多的视觉工作区进行集成。

下一轮只建议推进一个最小任务：修复`/cmd_vel`可信状态门控并建立回归测试。
