# 故障模式可复现测试步骤

现场联调时按卡片逐项测试。每条卡片包含：这个故障是什么意思、怎样触发、预期看到什么。
目的是证明软件在坏事发生时**会安全停下来**，而不是继续乱动。

---

## 卡片一：状态过期（RobotStatus 停止更新）

### 这是什么

小车肚子里有一块电路板（MCU），它会不断向上位机（你的笔记本电脑）发送"我还活着"的状态包。
这个包叫 `RobotStatus`，包含：通信是否正常、急停有没有按下、机构有没有故障、电池电压等。

上位机每 0.02 秒期待收到一次更新。如果超过 0.30 秒没收到，就认为"出事了"。
可能的原因：USB 线松了、MCU 死机了、串口驱动崩了。

**打个比方**：你每隔几秒给朋友发一条"我还好"的消息。如果超过一分钟没收到，你会怀疑出事了。
这里的 `status_stale_s=0.30` 就是这个"一分钟"，但因为是机器，所以设得很短。

### 软件的保护逻辑

`manipulator_client` 和 `motion_control` 各自检查状态是否过期。
一旦过期，立即停止所有运动、发布零速度、并报告 `COMMUNICATION_ERROR`。

### 模拟环境下怎样触发

准备两个终端。

终端 1——启动模拟底盘：
```bash
source /opt/ros/jazzy/setup.bash
source ~/robogame/ros2_ws/install/setup.bash
ros2 launch robogame_bringup single_cube.launch.py
```

终端 2——观察输出：
```bash
source /opt/ros/jazzy/setup.bash
source ~/robogame/ros2_ws/install/setup.bash
ros2 topic echo /mission/state
```

等待任务进入 `GO_TO_ORANGE` 或之后的状态。然后回到终端 1，按 `Ctrl+C` 停掉整个 launch。

终端 2 预期在停掉后很快看到：
```text
state: FAILED
result: COMMUNICATION_ERROR
```

或者直接测试纯 robot_bridge：

```bash
# 终端1: 启动 bridge
ros2 run robot_bridge robot_bridge --ros-args -p mock_mode:=true

# 终端2: 观察状态
ros2 topic echo /robot/status

# 终端1: Ctrl+C 停掉

# 终端2 的状态更新会停止
```

### 真车环境下怎样触发

小车通电、通信正常后，物理拔掉上位机和 MCU 之间的 USB 线。

### 预期行为

| 检查项 | 预期 |
|---|---|
| `/cmd_vel` 是否变为零 | 是，三个方向全为零 |
| `/mission/state` 的结果 | `FAILED` 或 `SAFE_STOP` |
| 小车物理上是否停止 | 是（电控的 150ms 失联停车独立生效） |
| 日志中是否有明确错误信息 | 有，包含 `COMMUNICATION_ERROR` 或 `status stale` |

### 恢复方法

插回 USB 线，重新启动 `robot_bridge` 节点。状态应自动恢复。

---

## 卡片二：服务不可用（机构服务没启动）

### 这是什么

夹爪和升降不是由你的代码直接控制的。你的代码通过 ROS2 **服务调用** 去请求它们动作。
服务就像一个柜台：你提交请求、等待办理、拿到结果。

如果 `robot_bridge` 没启动，或者对应的服务没注册，就是"柜台没开门"——你连请求都发不出去。

**具体流程**：
1. `manipulator_client` 发出"请抓取"的请求
2. 它先等 `/gripper/grab` 这个服务准备好（`service_is_ready()`）
3. 如果在一定时间内没准备好，就报告服务不可用

### 软件的保护逻辑

`manipulator_client` 在调用服务前检查 `service_is_ready()`。如果服务一直不可用，会超时并报告错误。

### 模拟环境下怎样触发

```bash
# 终端1: 只启动 manipulator_client，不启动 robot_bridge
ros2 run manipulator_client manipulator_client --ros-args -p action_timeout_s:=8.0

# 终端2: 发抓取命令
ros2 topic pub --once /manipulator/command std_msgs/msg/String "data: 'PICK_ORANGE'"

# 终端2: 观察结果
ros2 topic echo /manipulator/result
```

因为 `robot_bridge` 没启动，`/gripper/grab` 服务不存在。预期在超时后看到：
```text
data: TIMEOUT: manipulator action
# 或者
data: MECHANISM_ERROR: ...
```

### 真车环境下怎样触发

不启动 `robot_bridge`，只启动 `manipulator_client` 和 `mission_manager`，让任务流程走到 PICK 阶段。

### 预期行为

| 检查项 | 预期 |
|---|---|
| 是否返回失败结果 | 是，超时或服务不可用 |
| 软件是否继续发下一个命令 | 否，停在当前状态 |
| `/cmd_vel` 是否归零 | 是 |

### 恢复方法

启动 `robot_bridge` 后重新发命令。

---

## 卡片三：服务返回失败（机构动作执行失败）

### 这是什么

这次"柜台开门了"，你提交了请求，但柜台告诉你"办不了"。
比如：你请求夹爪抓取，但夹爪卡住了，电机堵转，服务返回 `success=false`。

**真实原因可能是**：方块在夹爪里卡住了、限位开关坏了、电机过流、升降撞到硬限位等。

### 软件的保护逻辑

`manipulator_client` 检查服务响应中的 `success` 字段。如果为 false，报告 `MECHANISM_ERROR` 并停止后续动作。
`mission_manager` 收到失败后会重试（最多 `max_retries` 次），超过重试次数后进入 `FAILED`。

### 模拟环境下怎样触发

模拟 `robot_bridge` 提供了 `mock_grab_success` 参数来控制成功/失败：

```bash
# 启动 bridge 并设置抓取必然失败
ros2 run robot_bridge robot_bridge --ros-args -p mock_mode:=true -p mock_grab_success:=false

# 另开终端，直接调用服务
ros2 service call /gripper/grab robogame_interfaces/srv/ExecuteMechanism "{command: 'GRAB', timeout_s: 3.0}"
```

预期响应：
```text
success: false
error_code: ...
detail: '...'
```

也可以通过 `manipulator_client` 完整走一遍：

```bash
# 终端1: 启动 bridge（抓取失败模式）+ 必要的视觉模拟
ros2 launch robogame_bringup mock_demo.launch.py
# 如果 launch 不支持参数覆盖，手动启动各节点

# 终端2: 观察
ros2 topic echo /manipulator/result
```

### 真车环境下怎样触发

在夹爪空载时故意放一个障碍物让它无法闭合，或者请求一个超出机械极限的高度。

### 预期行为

| 检查项 | 预期 |
|---|---|
| 服务响应 `success` | false |
| 响应 `error_code` | 非零，具体值需查错误码表 |
| `/manipulator/result` | 包含 `MECHANISM_ERROR` |
| 软件是否重试 | 是，最多 `max_retries` 次 |
| 超重试次数后 | 进入 `FAILED`，/cmd_vel 归零 |
| 载荷计数是否变化 | 否（没抓到就不能加） |

---

## 卡片四：硬件急停

### 这是什么

小车上有一个物理急停按钮（通常是红色大蘑菇头）。按下去后，**不论软件在做什么**，
所有电机和机构必须立即断电停止。这是安全链的最后一层，不能经过任何代码判断。

**关键认知**：急停的保护逻辑必须在电控（MCU）上实现。
急停按钮的线路直接切断电机电源或触发 MCU 的硬件中断。
软件只能**观察到**急停发生了，然后做出反应；但不能**依赖**软件来执行停止。

### 软件的保护逻辑（双重保护）

**第一层（电控硬件，优先级最高）**：急停按下 → 直接切断所有执行器。上位机死机也能生效。

**第二层（软件）**：`RobotStatus.emergency_stop=true` 通过串口传到上位机。
`control_safety_result()` 函数检查到这个标志后，返回 `SAFETY_STOP`（在安全判断中优先级最高，高于通信中断和机构故障）。
`mission_manager` 收到后进入 `SAFE_STOP` 终态，不再前进到任何新状态。
`manipulator_client` 收到后立即停止当前动作并发布零速度。

### 模拟环境下怎样触发

模拟 `robot_bridge` 默认 `emergency_stop=false`。可以手动发布一帧来模拟：

```bash
# 先启动单方块任务
ros2 launch robogame_bringup single_cube.launch.py

# 等任务运行中，另开终端模拟急停信号
ros2 topic pub --once /robot/status robogame_interfaces/msg/RobotStatus "
stamp:
  sec: 0
  nanosec: 0
communication_ok: true
emergency_stop: true
physical_start: true
gripper_closed: false
cube_present: false
mechanism_fault: false
battery_voltage: 24.0
error_code: 0
detail: 'manual e-stop test'
"
```

然后立即观察：
```bash
ros2 topic echo /mission/state
ros2 topic echo /cmd_vel
```

预期：
- `/mission/state.state` 变为 `SAFE_STOP`
- `/mission/state.result` 变为 `SAFETY_STOP`
- `/cmd_vel` 三个方向全为零

### 真车环境下怎样触发

小车低速运行时（**务必先架空轮子**），按下物理急停按钮。

### 预期行为

| 检查项 | 预期 |
|---|---|
| 底盘是否立即停止 | 是，不依赖上位机 |
| 夹爪/升降是否停止 | 是 |
| 上位机是否收到 `emergency_stop=true` | 是（如果通信仍正常） |
| 任务状态 | `SAFE_STOP` |
| 解除急停后是否自动恢复运动 | **否**，必须重新开始任务 |

### 恢复方法

1. 确认现场安全
2. 旋转急停按钮解除（通常需要旋转弹起）
3. 重新启动任务（不能从急停前的状态继续）

---

## 卡片五：机构故障（mechanism_fault）

### 这是什么

和急停不同，急停是人按的。机构故障是机器自己检测到的问题。
比如：电机过流（电流太大，可能卡住了）、限位开关失效、驱动器报错、传感器故障。

`RobotStatus.mechanism_fault` 这个字段是电控汇总了所有机构（夹爪、升降、底盘驱动）的健康状态后，
告诉上位机："机构这边有问题了，不要再发运动命令"。

### 软件的保护逻辑

`control_safety_result()` 检查 `mechanism_fault`，返回 `MECHANISM_ERROR`（优先级低于急停但高于普通失败）。
`mission_manager` 在 `tick` 中检查 `mechanism_fault`，如果为 true 则进入 `FAILED`。
`manipulator_client` 在收到新的机构命令时先做安全判断，`mechanism_fault=true` 时直接拒绝命令。

### 模拟环境下怎样触发

```bash
# 先启动单方块任务
ros2 launch robogame_bringup single_cube.launch.py

# 等任务运行中，模拟机构故障
ros2 topic pub --once /robot/status robogame_interfaces/msg/RobotStatus "
stamp:
  sec: 0
  nanosec: 0
communication_ok: true
emergency_stop: false
physical_start: true
gripper_closed: false
cube_present: false
mechanism_fault: true
battery_voltage: 24.0
error_code: 201
detail: 'simulated gripper overcurrent'
"
```

观察：
```bash
ros2 topic echo /mission/state
```

预期 `/mission/state.state` 变为 `FAILED`，`result` 为 `MECHANISM_ERROR`。

### 纯代码层验证（不启动 ROS2）

这个测试已经在 `tests/test_safety.py` 中覆盖，可以直接运行：
```bash
cd ~/robogame
python3 -m unittest tests.test_safety -v
```

预期 `test_mechanism_fault_blocks_manipulation` 通过。

### 预期行为

| 检查项 | 预期 |
|---|---|
| 任务状态 | `FAILED`，`result=MECHANISM_ERROR` |
| 当前正在执行的动作 | 立即停止 |
| `/cmd_vel` 是否归零 | 是 |
| 故障解除后是否自动恢复 | 否，需要确认故障原因并重新启动任务 |

---

## 卡片六：动作总超时

### 这是什么

`manipulator_client` 的 `action_timeout_s=12.0` 意思是：从收到命令（比如 PICK_ORANGE）开始，
如果 12 秒内没有完成整个动作（包括对准 + 抓取 + 验证），就认为失败了。

这和单个服务的超时（比如 GRAB 的 3 秒）不同——单个服务超时是"这个子步骤太慢"，
动作总超时是"整个过程太慢，可能某个环节卡住了但没报错"。

### 软件的保护逻辑

`manipulator_client._tick()` 在每次循环中检查 `now - self.started_at > action_timeout_s`。
超时后发布 `TIMEOUT: manipulator action` 结果并停止。

### 模拟环境下怎样触发

把超时设得极短，让任务不可能在时间内完成：

```bash
ros2 run manipulator_client manipulator_client --ros-args -p action_timeout_s:=0.5

# 另开终端发命令
ros2 topic pub --once /manipulator/command std_msgs/msg/String "data: 'PICK_ORANGE'"
ros2 topic echo /manipulator/result
```

预期很快（0.5 秒后）看到：
```text
data: 'TIMEOUT: manipulator action'
```

### 预期行为

| 检查项 | 预期 |
|---|---|
| `/manipulator/result` | `TIMEOUT: manipulator action` |
| `/cmd_vel` 是否归零 | 是 |
| `mission_manager` 收到超时后 | 重试或失败，取决于 `max_retries` |

---

## 卡片七：视觉目标丢失

### 这是什么

抓取前，小车需要用摄像头找到方块并低速对准它。如果在对准过程中方块"不见了"，
（比如光照突变、摄像头被挡、或者方块被蹭走了），软件不能继续盲目前进。

软件用 `target_stale_s=0.5` 来控制：如果超过 0.5 秒没有收到匹配颜色的方块检测结果，
就认为目标丢失，停止对准。

### 软件的保护逻辑

`manipulator_client._tick()` 在对准阶段检查 `now - target_time > target_stale_s`。
目标丢失后发布零速度并等待新的视觉数据。

### 模拟环境下怎样触发

启动单方块任务，但在对准阶段遮挡摄像头（模拟环境需要视觉模拟节点在运行）：

```bash
ros2 launch robogame_bringup single_cube.launch.py
# 如果使用了模拟视觉，可以停止 cube_perception 节点来模拟目标丢失
```

### 预期行为

| 检查项 | 预期 |
|---|---|
| 对准速度是否归零 | 是，小车停下 |
| 目标恢复后是否继续对准 | 是 |
| 长时间丢失后 | 动作总超时，返回 TIMEOUT |

---

## 测试记录表

现场测试时填写：

| 卡片 | 测试日期 | 触发方式 | 预期行为出现？ | 实际行为 | 通过？ | 备注 |
|---|---|---|---|---|---|---|
| 一：状态过期 | | | | | | |
| 二：服务不可用 | | | | | | |
| 三：服务失败 | | | | | | |
| 四：硬件急停 | | | | | | |
| 五：机构故障 | | | | | | |
| 六：动作超时 | | | | | | |
| 七：目标丢失 | | | | | | |

**规则**：所有卡片通过后，才能运行单方块闭环测试。任何一张未通过，先修问题再继续。
