# 单 SSH 窗口联调网页

## 它解决什么

`field_dashboard.py` 在树莓派内部统一管理 ROS 2 进程、订阅关键状态并保存原始日志。
Windows 现场只保留一个 SSH 窗口和一个浏览器页面，不再为每个节点及
`ros2 topic echo` 单独打开终端。

## Windows 连接

在 Windows Terminal 建立 SSH 隧道（用户名 `rg26`，主机名 `robogame-t26-rpi4.local`；
**树莓派经手机热点上网，IP 每次重连都会变，所以优先用主机名，不要写死 IP**；
找不到主机名时用 `python tools/find_pi_on_lan.py --source <你的IP>` 扫描）：

```powershell
ssh -L 8765:127.0.0.1:8765 rg26@robogame-t26-rpi4.local
```

> 完整的找 IP、免密、传文件、换端口、主机密钥变更处理见
> `docs/guides/RASPBERRY_PI_SSH_AND_WEB_GUIDE.md`。

登录树莓派后（**仓库路径是 `~/robogame`**）：

```bash
cd ~/robogame
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
python3 tools/field_dashboard.py
```

Windows 浏览器打开 `http://127.0.0.1:8765`。服务默认只监听树莓派回环地址，
不能从局域网直接访问。

## 安全使用顺序

1. 架空车轮、机构卸载，独立验证物理急停。
2. 页面启动 `bridge`，等待通信与物理授权均变绿。
3. 先观察状态和日志，不执行动作。
4. 手动底盘必须先点“接管”；持续按住方向键或 WASD 才运动，松开即归零。
5. 手动接管会停止控制台管理的导航、机械臂对准和巡线进程，退出后不会自动恢复。
6. 红色“全部停止”同时请求底盘和机构停止，但不能替代物理急停。

浏览器或 SSH 隧道断开时，手动控制心跳会在 300 ms 后超时并发送零速；已经启动
的观察和巡线进程保留。服务进程自身退出时会先归零，再停止由它启动的子进程。

## 网页定距试车

更新 `tools/field_dashboard.py`、`tools/distance_trial.py` 和
`tools/field_dashboard_web/` 后，需要重启树莓派网页服务并刷新浏览器；
只改 Windows 文件不会更新正在运行的树莓派服务。

1. 车轮落地、前方留足空间，启动 bridge 和 localization，确认物理授权及反馈正常。
2. 释放手动控制并停止巡线。在“底盘定距测试”中填写距离（默认 0.50 m）
   和速度（默认 0.05 m/s），点击“开始前进”。不需要终端或手工计算坐标。
3. 服务停止自己管理的其他运动节点，再检查其他速度发布者是否退出。
   根据当前位置和朝向计算前进距离，保持起始朝向，接近目标减速。
4. 保持网页前台。可点“停止定距”；失焦、心跳中断、反馈过期、反馈停滞、
   下位机重启或安全状态异常也会停止。网页定距是调试功能，不是自主比赛任务。
5. “里程计目标已到达”只表示反馈距离进入 1 cm 容差，必须尺量实际位移。
   若轮子打滑、里程计比例错误或惯性滑行，实际距离可能不同。

初次测试距离限制 0.05～1.00 m，速度限制 0.02～0.10 m/s；
位置数据超过 0.3 s 未更新、连续 2 s 没有前进反馈、横向偏差超过 10 cm
或航向偏差超过 30°时终止。心跳超时 0.5 s，由 50 ms 周期检查，
实际停车还取决于通信和底盘制动。任务结束不会自动恢复其他运动节点。

## 网页巡线诊断

同步 `tools/field_dashboard.py` 和整个 `tools/field_dashboard_web/` 到树莓派，重启现有网页服务并强制刷新浏览器。在“状态与巡线”卡片中即可检查，不需要另开终端运行 topic echo。

1. 启动 bridge 后移动探头下的黑线，观察八路读数、数据年龄及 MCU tick。柱状图按 0～4095 缩放；通道实际左右及黑白极性需要现场核对。
2. 查看发布者和 analog_valid。没有消息时检查 STM32 巡线遥测；模拟数据不算真车证据。当前控制器忽略 analog_valid=false 的帧。
3. 具备实际动作条件后点原有“启动巡线”，对照控制器状态、算法输出和发布速度。稳定放置黑线时，左侧应为 dev 负、wz 正，右侧相反。
4. 页面提示无数据、过期、模拟量无效、控制器无状态及算法要求运动但发布速度为零等情况。结合安全总览的通信、物理授权、故障字段排查。
5. 用“停止巡线”或“全部停止”结束。关闭网页不会自动停止已启动的巡线进程。

传感器过期提示阈值为 0.25 s，控制器/速度观测为 0.5 s，仅用于面板诊断，不修改控制参数。旧读数保留但柱状图变暗；快照请求失败会标记页面旧值。

`/cmd_vel` 为共享话题，最近消息无法单独确定发送者；页面同时列出速度发布者。速度发布不代表串口发送成功或电机已执行，实际回线和停车需真车验收。

## 日志文件

每次启动自动创建：

```text
outputs/field_console/YYYYMMDD_HHMMSS_microseconds/
├── session.json
├── events.jsonl
├── telemetry.jsonl
└── raw/*.log
```

页面“清空”只清空浏览器当前显示，不删除这些证据文件。

## 机械臂联调面板（0x20 / 0x21）

页面底部“机械臂联调”卡片由 `field_dashboard_web/arm_panel.js` 自己在加载时插入 DOM，
因此新增/修改这个卡片不需要动 `index.html` 的其它部分（只保留一行 `<script>` 引用）。

同步到树莓派需要这几个文件一起更新，否则会新旧不一致：

```text
tools/field_dashboard.py          # 路由与接线（/api/arm/*）
tools/arm_selftest.py             # 五级自检的判定逻辑（可脱离 ROS 单测）
tools/field_dashboard_web/arm_panel.js
tools/field_dashboard_web/index.html      # 6 个 <script> 引用之一（/arm_panel.js）
```

改完重启网页服务并强制刷新浏览器。

### 五个按钮，按风险从低到高

| 按钮 | 会不会动 | 判据 |
|---|---|---|
| 链路与授权 | 不动 | 状态新鲜 + 通信 + PB2 授权 + 无急停/故障 + 底盘不在运动 + 四个机构服务就绪 |
| ARM_SET 通路自检 | **理论不动** | 对四个关节下发“当前安全姿态角度”，全部应 `success` |
| 夹爪闭环 | 爪开合 | `GRAB`/`RELEASE` 成功，且 `gripper_closed` 随之翻转 |
| 超时错误码 | 腕约 5° | 故意给 0.3 s 预算，应返回 `3020` |
| 停止与边界 | 不动 | `/mechanism/stop` 成功；`/lift/set_height` 应返回 `3010`（真车无升降） |

“ARM_SET 通路自检”是价值最高的一步：它下发的角度对应固件 `ARM_SAFE_PULSE_US`
（也就是当前真实姿态），所以机械臂理论上不动，却把
“上位机编码 → 0x20 → 固件解析 → 角度换算 → 舵机输出 → 0x21 回传”
整条链路验证了一遍。面板会把每个关节的零位移参考角度直接列出来。

前提：`/arm/set_joint` 默认被拒（`9010`，关节值域未冻结，依据现场问答表 C-11）。
要在面板上做通路自检，先在 `robogame_bringup/config/robot.yaml`（`robot_bridge.ros__parameters`
下，当前 `robot.yaml:26-27` 为空串）填：

```yaml
    arm_joint_ranges: "0:0:270;1:0:270;2:0:270;3:0:270"
    arm_joint_ranges_evidence: "2026-09-17 上车联调临时冻结，仅用于通路验证"
```

> ⚠️ **改 `robot.yaml`，不是 `hardware.yaml`**。现场启动命令见 `tools/field_console.json:7`，
> 只加载 `robot.yaml` + `robot_field.yaml`；`hardware.yaml` 是 legacy、**未被加载**，
> 在里面填 `arm_joint_ranges` 不会有任何效果
> （`tests/test_config_validation.py:258` 把 hardware.yaml 的漂移仅判为 warning）。
> 同理该文件里的 `command_timeout_s: 0.15` 也不生效，真正生效的是 `robot.yaml:7` 的 `0.5`。

面板底部还有“手动 ARM_SET”（关节下拉 + 角度 + 超时），做小角度真动时不用开终端。

### 这一面板**不能**证明什么

- **PASS 不等于舵机到位**：机械臂纯开环无位置反馈（C-1），服务成功只代表命令被
  接受并走完了流程。位移、方向、幅度必须现场目视确认。
- 面板不显示实际关节角度——因为**没有**这个数据；`gripper_closed` 是固件按命令
  推算的标志，不是爪的位置传感器。
- `cube_present` 恒为 0（本车没有方块传感器），所以任何依赖它的抓取验证策略在
  真车永远无法通过；真车抓取只能用 `service_only`。
- 机构服务本身可用，不代表固件收到了、也不代表机械臂动了。

## 真伪与实例核查（防「假数据」与「双实例」）

页面底部「真伪与实例核查」卡片由 `field_dashboard_web/runtime_guard.js` 自己插入 DOM，
同步到树莓派需要这几个文件**一起**更新：

```text
tools/field_runtime_guard.py             # 判据与措辞（纯逻辑，可离线单测）
tools/field_dashboard.py                 # 发布者地图 + 进程表扫描 + 快照字段 + 启动拦截
tools/field_dashboard_web/runtime_guard.js
tools/field_dashboard_web/app.js          # refresh 里多一行 renderRuntimeGuard(s)
tools/field_dashboard_web/index.html      # 多一行 <script src="/runtime_guard.js">
```

它解决两个「看起来一切正常」型的误判：

1. **真假**：`robot_bridge` 的 `mock_mode` 默认是 `True`（`robot_bridge/node.py:124`）。
   只带共用层启动就是假数据——安全总览照样显示「通信正常」，而车上根本没接固件。
   判据是 `/robot/status` 的 `detail` 字段，分类函数与 launch 里的 `runtime_source_guard`
   **是同一个**（`robogame_core/runtime_source.py:classify_status_detail`）。
2. **双实例**：控制台只知道自己 spawn 的 PID，对「有人先在 SSH 里起了
   `hardware.launch.py`」无感。起了第二个 bridge 的后果不是一句报错，而是
   `runtime_source_guard` 关掉整个 launch，或两个进程抢同一个串口。判据两路：
   关键话题的发布者数量（与 source guard 同一份 `CRITICAL_TOPICS`）+ 进程表里
   「匹配到已知节点、但不属于本控制台」的进程。

### 三种状态，颜色有纪律

| 状态 | 颜色 | 含义 |
|---|---|---|
| 真车 + 关键话题各一个发布者 | 绿 | 可以继续 |
| mock / 串口在但没解码到状态 / 没收到状态 / 别的节点有外部实例 | 灰 + ⚠️ | 需要人注意，但**允许继续**（现场要能手动联调） |
| 关键话题两个发布者、发布者不是 `robot_bridge`、mock 与真车混在一起 | 红 | 真的被拦住 |

红色只留给「起了就是第二个实例」——把警告也画成红色，人会开始忽略红色。

### 拦截规则（只拦这一种）

点「启动」时后端先核查，命中就拒绝并给出 PID 与命令行：

- 你要启动的节点已经有**外部实例**在跑（整栈在 SSH 里跑着时的典型情况）；
- 关键话题已经有 >1 个发布者（再起就是第三个）；
- `/line_sensor` 有两个发布者时，「启动巡线」被拦（巡线节点分不清哪一帧是真的）。

被拦的按钮会置灰并把理由放在 `title` 里，同时「启动底盘链路」整组一起拦下——
半启动状态比不启动更难排查。**其余情况一律不拦**：mock 只警告，因为手动联调本来
就可能跑 mock。

### 它**不能**证明什么

- 进程表里没有第二个实例 ≠ 串口没被占用（那要看 `lsof`）；Windows 上没有 `ps -eo`，
  卡片会明说「进程表不可用——**不能**据此认为没有第二个实例」，而不是显示一片正常；
- `detail` 说「真车」只代表**这份运行中的 bridge 解到了 MCU 的状态帧**，
  不代表车上烧的是仓库里这份固件（固件版本/构建哈希仍未上报）；
- 它不阻断任何**动作**：手动接管、机构命令、定距测试都不经过这张卡片的判据，
  仍由各自的 `action_blockers` 门控。

## 关节微调与姿态示教（让机械臂像遥控器那样动，并能复现）

两张卡片由 `field_dashboard_web/arm_jog_panel.js` 与 `arm_pose_panel.js` 自己插入 DOM。
同步到树莓派需要这几样**一起**更新：

```text
tools/arm_jog.py                                 # 微调纯逻辑（校验/累加/边界/停机理由）
tools/field_dashboard.py                         # 路由 + 后台循环 + 快照字段
tools/field_dashboard_web/arm_jog_panel.js        # 「关节微调」卡片
tools/field_dashboard_web/arm_pose_panel.js       # 「姿态示教」卡片
tools/field_dashboard_web/app.js                  # refresh 里多两行 render
tools/field_dashboard_web/index.html              # 多两行 <script>
ros2_ws/src/robogame_core/robogame_core/arm_poses.py   # 姿态表纯逻辑（在 ROS 包里！）
```

> ⚠️ **`arm_poses.py` 在 ROS 包里，不是 `tools/`**：symmetric 部署时它需要
> `colcon build --symlink-install`（或重新 build）才会生效，光 scp 一个文件不够。
> 放在 ROS 包里是**故意的**——将来 `manipulator_client` 要用同一份姿态表，
> 而 ROS 节点不能 import `tools/`。

### 前置条件（不做则每一步都被拒）

`arm_joint_ranges` 必须是**非空**且带 `evidence`，否则 `/arm/set_joint` 一律返回
**9010**（关节值域未冻结），微调和回放都不动。见 `robot.yaml` 里那两行的注释。

### 关节微调：按住走一小步

| 控件 | 说明 |
|---|---|
| 关节 | 0 腰/云盘、1 肩、2 肘、3 腕；**爪子不在这条路上**（9012，走夹取/释放） |
| 起点角度 | 默认是该关节的**安全姿态角度**（131/158/135/25°，由固件常量换算）。⚠️ 我们读不到真实角度，这个值是你"相信它现在在哪" |
| 步长 | 1/2/5/10°（必须整度：`parameter = joint*1000 + angle` 没有小数位） |
| 间隔 | 两步之间停顿 0.2/0.5/1.0s（外加固件按约 45°/s 走过去的时间） |
| 按住 −/+ | 按住才走。**松手、切走窗口、断线**都会在心跳超时（0.6s）内自动停 |

停止理由会显示在卡片上：心跳中断、安全门拦截（急停/通信/状态过期/机构故障/物理授权）、
到 0°/270° 边界、某一步失败（带错误码：9010 值域未冻结、9011 越界或非整度、9012 爪子、
3020 固件没在预算内走到、6 已有其它机构命令）。**微调进行中，别的机构命令会被拒绝**
（0x20 同一时刻只允许一条命令，否则两条会互相顶掉、表现为"角度乱跳"）。

### 姿态示教：摆一次，记下来，能复现

- **「记录当前姿态」**：把"我们给每个关节下发过的最后一个目标角度"存成一条具名姿态，
  落盘到 `~/robogame_arm_poses.json`（可用 `--pose-file` 改）。出厂自带一条
  「安全姿态」（固件上电授权后的姿态，来自 `arm.c` 的 `ARM_SAFE_PULSE_US` 换算）。
- **「回放」**：按**示教时的操作顺序**逐关节串行下发，每步等固件回执；失败即停，
  且不推进"我们相信它在哪"。顺序不自作主张重排——先动肩还是先动肘是机械问题。
- **「先回零（建议）」**：回放前先 `/mechanism/home`，从同一个已知起点出发，误差才不累积。
  回零成功后「当前下发目标」会整体重置成安全姿态。

### 这两张卡片**不能**证明什么

- **显示的"当前角度"不是测量值**：机械臂无位置反馈，0x21 状态帧也没有位置/脉宽字段。
  页面显示的是**我们发出去的目标角度**累加值。**别把它当成"车知道臂在哪"。**
- **回放是开环的**：固件的 `SUCCEEDED` 只代表"输出脉宽到了目标 ±4µs"
  （`arm.c` 的 `ARM_AUTO_TOLERANCE_US`），**不代表舵机真转到了那个角度**。被挡、丢步、
  电池电压不足都不会被发现。
- **遥控器永远优先**：摇杆/按键一动（含摇杆漂移超死区），固件立刻清掉全部自动目标
  （`arm.c:451`）。所以遥控在线时，微调与回放随时会被打断。
- **不能同时动多个关节**：0x20 一条命令一个关节，且并发命令被拒（错误码 6）。
  这是协议层限制，不是本面板的限制。

## 证据边界

网页中的“原因定位”来自明确状态字段和时间阈值。进程显示运行只能证明进程尚未
退出，不能证明串口、真实底盘或机构已经可用。真车动作必须按架空、断线停车、
故障注入、机构卸载、最后落地低速的顺序逐级验收。
