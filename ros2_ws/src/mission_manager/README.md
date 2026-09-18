# mission_manager 使用说明

## 1. 这个包负责什么

它相当于“总指挥”：决定先去哪、抓哪种颜色、何时去搭建、按什么顺序放置，以及失败后重试还是安全停止。它不直接计算视觉和底盘控制，只给其他模块发任务并接收结果。

## 2. 文件分工

- `mission_manager/node.py`：把 ROS2 话题与状态机连接起来，发布路点和机构命令。
- `robogame_core/mission.py`：与 ROS2 无关的状态转移、超时、重试和装载顺序。
- `robogame_core/models.py`：任务结果、颜色、货物数量等基础数据。
- `setup.py`：登记程序名 `mission_manager`。

## 3. 最简单的演示

单块模拟任务：

```bash
ros2 launch robogame_bringup single_cube.launch.py
```

观察任务和车载数量：

```bash
ros2 topic echo /mission/state
ros2 topic echo /mission/cargo
```

模拟模式会在启动约 2 秒后自动给出实体启动信号，所以任务最终出现：

```text
state: COMPLETE
result: SUCCESS
detail: mission complete
```

这表示软件模拟闭环完成，不代表真车抓放成功。

## 4. 状态流程

现场（比赛）走路线模式，主线是：

```text
WAIT_FOR_COMMUNICATION → SELF_CHECK → WAIT_FOR_PHYSICAL_START → ROUTE_RUNNING
```

前三个状态定义在 `robogame_core/robogame_core/mission.py:15`、`:16`、`:17`，路线模式 `ROUTE_RUNNING` 在 `mission.py:20`；由 `config/robot_field.yaml` 的 `route_enabled: true` 打开（说明见 `mission_manager/node.py:10`）。

下面这条单块／一橙一紫演示链只在没有路线时使用（`route_enabled: false`，例如 `single_cube` 演示）：

```text
SELF_CHECK → WAIT_FOR_PHYSICAL_START
→ GO_TO_ORANGE → PICK_ORANGE
→ GO_TO_PURPLE → PICK_PURPLE
→ GO_TO_BUILD → PLACE_ORANGE → PLACE_PURPLE
→ RETREAT → VERIFY_BUILD → COMPLETE
```

它在 `mission.py:21-22` 已被明确标注「B1 起对完整比赛 run 作废」，保留给演示链路和既有测试使用。

颜色目标数：只有紫色有跳过逻辑（`mission.py:481-484`），橙色没有（`orange_target` 为 0 时仍会进入 `GO_TO_ORANGE`，`mission.py:266`）；失败超过 `max_retries` 或急停时进入失败/安全停止。

## 5. 输入和输出

- 输入 `/robot/status`：通信、急停、实体启动和机构故障。
- 输入 `/motion/result`：导航结果。
- 输入 `/manipulator/result`：抓放结果。
- 输入 `/line_follow/status`：巡线节点状态（`node.py:105`）。
- 输入 `/pose`：路线模式下的当前位置（`node.py:106`）。
- 输出 `/motion/goal`：目标路点。
- 输出 `/manipulator/command`：抓放命令。
- 输出 `/mission/state`：当前状态和结果。
- 输出 `/mission/cargo`：算法认为的车载方块数量。
- 输出 `/mission/active_source`：当前唯一被授权驱动底盘的来源名，其他运动节点据此决定动不动（`node.py:97` 发布、`node.py:221` 赋值）。
- 输出 `/mission/route`：路线模式的进度 JSON（`node.py:98`、`node.py:265`）。
- 输出 `/mission/turn`：路线里的转向命令（`node.py:100`、`node.py:230`）。
- 输出 `/mission/line`：路线里的巡线命令（`node.py:101`、`node.py:228`）。
- 输出 `/cmd_vel`：终止状态时发布零速度。

## 6. 配置

`robogame_bringup/config/robot.yaml` 保存三块任务的目标数量、重试、超时和路点；`single_cube.yaml` 把目标覆盖为一块橙色。

路点数组顺序是 `[x, y, yaw]`，单位分别为米、米、弧度。规则场地坐标没最终确认前不要写死比赛数值。

## 7. 当前限制和现场待办

当前 `VERIFY_BUILD` 只是等待配置的稳定时间，并没有相机检查建筑是否真的稳定；货物数量是在下层返回 `SUCCESS` 后由软件增减，没有独立传感器复核。导航和机构结果都使用字符串，后续可改成更严格的 action/service 接口。

现场必须测试急停、通信断开、导航超时、夹爪失败和运输掉块。尤其要保证“软件认为车上有方块”与真实传感器一致，否则状态机可能继续放置不存在的层。

## 8. 自动测试

```bash
cd ~/robogame
python3 -m unittest tests.test_mission -v
```
