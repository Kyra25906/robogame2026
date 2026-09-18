# 树莓派 SSH + 网页操作指南（2026-09-17 版）

> 面向：需要在真车上跑一次联调的队友。
> 目标：**只开一个 SSH 窗口 + 一个浏览器页面**，不让你开四五个终端。
> 规则：本文只写已验证或可从仓库配置直接读到的事实；推测处标注 ⚠️。

---

## 0. 三十秒速查（TL;DR）

```powershell
# ① 树莓派 IP 每次重连热点都会变，优先直接用主机名（见第 2 节；
#    主机名不通时用 python tools/find_pi_on_lan.py 扫描网段）
#    注意：ping 不通不代表树莓派不在线，别拿 ping 当判据

# ② 一条命令：登录 + 同时把网页端口映射到本机 8765
ssh -L 8765:127.0.0.1:8765 rg26@robogame-t26-rpi4.local

# ③ 登录树莓派后，启动统一网页服务（只此一条，别再开别的终端）
cd ~/robogame && source /opt/ros/jazzy/setup.bash && source ros2_ws/install/setup.bash
python3 tools/field_dashboard.py
```

然后在 Windows 浏览器打开 👉 **http://127.0.0.1:8765**

之后所有启停节点、看状态、看日志、手动开车、巡线诊断、机械臂自检**都在这个网页里点**，不需要回 SSH 窗口敲 ROS 命令。

---

## 1. 连接参数（权威版本）

| 项目 | 值 | 来源 |
|---|---|---|
| 用户名 | `rg26` | 部署日志、`dialout` 组配置 |
| 主机名 | `robogame-t26-rpi4` | 部署日志 |
| SSH 别名 | `rg26@robogame-t26-rpi4.local` | mDNS/Avahi，需网络支持 |
| 系统 | Ubuntu Server 24.04 arm64 | 部署日志 2026-08-18 |
| ROS 2 | **Jazzy**（`/opt/ros/jazzy/setup.bash`） | 部署日志 2026-08-18 |
| **树莓派仓库路径** | **`~/robogame`** | 部署日志 2026-08-18 |
| 网页服务端口 | `8765` | `tools/field_dashboard.py` |
| 用户组 | `rg26` 属于 `dialout`（可开 STM32 串口） | 部署日志 |

> ⚠️ **如果你在别的文档里看到 `pi@192.168.1.20` 或 `~/robogame2026-integration`，那是过期的**。
> 正确的用户名是 `rg26`，正确的仓库路径是 `~/robogame`。
> `~/robogame2026-integration` 是**另一台机器**（Ubuntu VM，用户 `panwenhui`）上的路径，不要混用。

---

## 2. 树莓派 IP 会变 —— 四种找法，从快到慢

树莓派经**手机热点**联网，DHCP 每次重连都可能换地址。
实测历史（来自本机 `known_hosts`）同一个树莓派出现过：

```text
10.101.192.213
10.234.106.213
10.109.118.213
10.127.54.213
```

**规律：第三段变，最后一段常是 `213`。** 这只是巧合级别的经验，⚠️ 不能当保证，但可以先试。

### 方法 1（首选，零成本）：直接用主机名，别记 IP

```powershell
ssh rg26@robogame-t26-rpi4.local
```

**为什么能行**：树莓派上的 Avahi 会广播 mDNS 名字。手机热点如果允许客户端互相发现，
这个名字永远指向正确 IP，**IP 变了也不用管**。
**为什么可能不行**：很多手机热点做了「客户端隔离」，mDNS 被挡；此时方法 1 会超时，改用方法 2/3。

### 方法 2（推荐，仓库自带脚本）：扫整个网段

先看你自己在当前网络的地址（它的前三段就是网段）：

```powershell
ipconfig | Select-String "IPv4"
```

假设你是 `10.101.192.18`，网段是 `10.101.192.x`，然后：

```powershell
python tools/find_pi_on_lan.py --source 10.101.192.18
```

脚本会并发探测本网段所有地址，**只在发现 SSH(22) 或网页(8765) 服务时才打印**：

```text
{"ip": "10.101.192.213", "port": 22, "banner": "SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13"}
Scan complete.
```

看到 `port: 22` 且 banner 里有 `OpenSSH` 的那行，就是树莓派。
如果同时打出 `port: 8765` 带 `HTTP/1.0 200 OK`，说明**网页服务已经在跑**，可以直接进第 4 节。

> 这个脚本是**只读探测**：只读取 SSH 欢迎横幅和 HTTP 响应头，不登录、不发送任何动作。

### 方法 3（最可靠，认 MAC）：查 ARP 表

树莓派无线网卡 MAC 是 **`98-fe-54-31-1b-9f`**（已在部署日志确认）。

```powershell
arp -a | Select-String "98-fe-54-31-1b-9f"
```

有输出就说明已经通信过，行首就是它现在的 IP。

**如果没有输出**：说明这台 Windows 还没和树莓派直接通信过，ARP 表里没有它的条目。
ARP 条目只有在**本机真的向该地址发过包**之后才会出现，所以：

- 最省事的办法是**先跑方法 2 的 `find_pi_on_lan.py`**——它会对全网段逐个发起 TCP 连接，
  这本身就会把在线主机的 ARP 条目「顶」出来，跑完再 `arp -a` 就有结果了；
- 或者让树莓派主动连一下你这台电脑（在树莓派上 `ping <你的IP>`）。

> 注意：Windows 下 `ping` 本网段广播地址通常**不会**填充 ARP 表，别指望这一招。

### 方法 4（保底）：看手机热点客户端列表

打开手机热点的「已连接设备」页面，找主机名含 `robogame` 或 `rpi` 的设备，直接读它的 IP。

### ⚠️ 别用 ping / Test-Connection 判断（会被防火墙骗）

树莓派可能**禁 ping**，而 Windows PowerShell 5.1 的 `Test-NetConnection` 在不可达时
**要等 45 秒**才返回（实测）。所以：

- `ping` 不通 **不代表** 树莓派不在线；
- 不要用 `ping`/`Test-Connection` 写循环扫描，太慢且结论不可靠。

**判断树莓派在不在，就看 22 端口能不能连上**：

```powershell
# 单个候选，约 1~7 秒
Test-NetConnection -ComputerName 10.101.192.213 -Port 22 -WarningAction SilentlyContinue
# 看 TcpTestSucceeded : True
```

### ⚠️ 扫描结果里会有「假的树莓派」

同一网段里 **Ubuntu 虚拟机（用户 `panwenhui`，约 `192.168.253.128`）也开着 SSH**，
扫描时会出现多个 `SSH-...` 结果。**认主机名，不要认第一个结果**：

- 树莓派 banner 来自 Ubuntu Server 24.04 arm64，登录后提示符是 `rg26@robogame-t26-rpi4:~$`；
- 更可靠：连上去后 `hostname` 一下，或确认 `~/robogame` 目录存在。

```bash
hostname                       # 应为 robogame-t26-rpi4
ls -d ~/robogame               # 应存在
```

---

## 3. SSH 与端口转发（关键：网页靠隧道访问）

### 3.1 标准连接（带网页隧道）

```powershell
ssh -L 8765:127.0.0.1:8765 rg26@robogame-t26-rpi4.local
```

`-L 8765:127.0.0.1:8765` 的含义：**把本机 8765 端口的流量，通过 SSH 转发到树莓派自己的 8765**。

**为什么要这样**：`field_dashboard.py` 默认只监听树莓派的回环地址 `127.0.0.1`。
这是安全设计——**服务默认不能被局域网里其他人访问**，必须经 SSH 隧道进来。
所以你的浏览器访问的是**本机** `127.0.0.1:8765`，流量被 SSH 加密送到树莓派。

**隧道随 SSH 窗口一起活**：SSH 窗口一关，隧道断，网页就打不开（但树莓派上的节点还在跑）。

### 3.2 只想敲命令、不要网页

```powershell
ssh rg26@robogame-t26-rpi4.local
```

### 3.3 换端口 / 本机 8765 被占用

如果本机 8765 已被别的程序占了，换本机端口即可（左边那个数字随便改）：

```powershell
ssh -L 8877:127.0.0.1:8765 rg26@robogame-t26-rpi4.local
# 然后浏览器开 http://127.0.0.1:8877
```

### 3.4 首次连接 / 换了 IP 后的主机密钥提示

第一次连会问 `Are you sure you want to continue connecting (yes/no)?`，输 `yes`。

**换了 IP 重新连，可能报 `REMOTE HOST IDENTIFICATION HAS CHANGED!`**
这通常**不是被攻击**，而是：DHCP 把这个 IP 分给了别的设备，或树莓派重装过系统。
确认是上面两种情况后，删掉旧记录再连：

```powershell
ssh-keygen -R 10.101.192.213        # 把 IP 换成报错里提到的那个
```

### 3.5 免密登录（可选，建议做）

```powershell
ssh-keygen -t ed25519            # 已有密钥就跳过
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh rg26@robogame-t26-rpi4.local "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

### 3.6 常用运维命令

```bash
# 在树莓派上
cd ~/robogame                                  # 进仓库
source /opt/ros/jazzy/setup.bash               # ROS 2 环境
source ros2_ws/install/setup.bash              # 本项目 9 个包

git log --oneline -5                           # 当前部署的是哪个提交
git status --short                             # 现场有没有手改文件

# 确认没有残留节点（双实例会互相打架，08-18 踩过多次坑）
ps aux | grep -E "field_dashboard|robot_bridge|motion_control|localization" | grep -v grep

# 串口在不在（STM32 VCP）
ls -l /dev/serial/by-id/
```

从 Windows 直接传文件（不用登录）：

```powershell
scp tools/field_dashboard.py rg26@robogame-t26-rpi4.local:~/robogame/tools/
scp -r tools/field_dashboard_web rg26@robogame-t26-rpi4.local:~/robogame/tools/
```

> ⚠️ **只改 Windows 上的文件不会影响树莓派**。树莓派跑的是它自己 `~/robogame` 里的副本，
> 改完必须同步过去并**重启网页服务 + 强制刷新浏览器**。

---

## 4. 网页操作指南（真车联调只开这一个页面）

启动命令（在树莓派 SSH 窗口里）：

```bash
cd ~/robogame
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
python3 tools/field_dashboard.py
```

浏览器打开 **http://127.0.0.1:8765**。

> ⚠️ **只从网页（或 `hardware.launch.py`）启动 `bridge`，不要手敲裸 `ros2 run`。**
> 网页的 `bridge` 走 `tools/field_console.json`，会加载 `robot.yaml` + `robot_field.yaml`。
> 若某个启动方式**不传 params 文件**，节点会退回**硬编码默认值**
> （`serial_port=/dev/ttyACM0`、`command_timeout_s=0.15`、`max_mcu_sample_gap_ms=250`），
> 于是 08-18 修好的**零速插入顿挫**和 **LOCALIZATION_ERROR 停车**会静默复发，
> 而且日志上看不出原因。（`line_follow_hardware.launch.py` 曾有此缺陷，已于 2026-09-17 修复。）

### 4.1 安全顺序（不要跳步）

1. **架空车轮、机构卸载**，并且**独立验证物理急停有效**（不依赖软件）。
2. 页面里启动 `bridge`，等**通信**和**物理授权**都变绿。
3. 先只观察状态和日志，**不执行任何动作**。
4. 再按需要启动 `localization`、`motion`、`line`。
5. 手动开车前必须**先点「接管」**；**按住**方向键/WASD 才动，**松开立即归零**。
6. 收尾点「全部停止」或 `quit`。

> 红色「全部停止」会同时请求底盘和机构停止，**但它不能替代物理急停**。

### 4.2 页面能做什么（都在网页里点，不用开终端）

| 功能 | 卡片/按钮名（照页面上的字找） | 说明 |
|---|---|---|
| 安全总览 | 「安全总览」 | 通信、物理授权、急停、故障字、阻塞原因（blockers） |
| 进程启停 | 「进程」→ 启动底盘链路 / 启动机械臂 / 启动巡线 | 启停 bridge / localization / motion / arm / line，看 PID 与运行状态 |
| 手动驾驶 | 「底盘手动」→ 接管 / 释放 | 先点「接管」，按住 W/A/S/D/Q/E 才动，松开即停；失焦/断线 300 ms 自动归零 |
| 定距试车 | 「底盘定距测试」→ 开始前进 / 停止定距 | 默认 0.50 m、0.05 m/s |
| 巡线诊断 | 「状态与巡线」→ 启动巡线 / 停止巡线 + 标定按钮 | 八路读数、数据年龄、MCU tick、门控链、控制器状态、dev/wz |
| 机械臂 | 「机械臂」→ 夹取 / 释放 / 回零 / 机构停止 | **真车无升降**，LIFT 会被固件拒（`3010`） |
| 机械臂自检 | 「机械臂联调」卡片 | 五级自检（链路/通路/夹爪/超时/停止），含手动 ARM_SET |
| 抓取对准 | 「抓取对准（视觉 → 转向/前进）」 | 视觉对准仿真，与真车同一份纯函数 |
| 全流程路线 | 「全流程路线（B1 只读）」 | 13 段路线、每段退出判据与可信度；**只显示计划，不驱动车辆** |
| 日志 | 「原始日志」 | 可按模块筛选/搜索、暂停滚动，不必再 `ros2 topic echo` |

### 4.3 定距试车注意

- 初次测试限制：距离 **0.05–1.00 m**，速度 **0.02–0.10 m/s**。
- 下列任一情况会自动终止：位置数据 >0.3 s 未更新、连续 2 s 无前进反馈、
  横向偏差 >10 cm、航向偏差 >30°。
- 提示「里程计目标已到达」**只代表反馈距离进入 1 cm 容差**，
  **必须用尺子量实际位移**——轮子打滑、里程计比例错误都会让它说谎。
  ⚠️ **注意：里程计不准这件事不一定还成立——我们换过电机了。** 2026-08-18 曾观测到**约 10 倍偏差**（命令 1 m 实际约 10 cm），但那是**旧电机时期**的记录，且之后的 `1404` 编码器常量是 09-13 在旧硬件上手工转出来的。**换电机后这些前提都可能变了**，不要预设里程计一定坏、也不要预设它一定好。**先看面板上的「里程计是否自洽」那一行**（里程计等效速度 ÷ 命令速度，≈1 才自洽），再决定要不要尺量定标度。

### 4.4 机械臂联调注意

- 做「ARM_SET 通路自检」前，需要先在 `ros2_ws/src/robogame_bringup/config/robot.yaml`
  的 `robot_bridge.ros__parameters` 下填（当前 `robot.yaml:26-27` 是空串）：

  ```yaml
      arm_joint_ranges: "0:0:270;1:0:270;2:0:270;3:0:270"
      arm_joint_ranges_evidence: "临时冻结，仅用于通路验证"
  ```

  否则 `/arm/set_joint` 默认被拒（`9010`）。

  > ⚠️ **注意改哪个文件**：现场启动用的是 `robot.yaml` + `robot_field.yaml`
  > （见 `tools/field_console.json:7`）。**`hardware.yaml` 是 legacy，根本没被加载**，
  > 里面有 `arm_joint_ranges: ""` 是历史残留，改它**没有任何效果**
  > （`tests/test_config_validation.py:258` 明确把 hardware.yaml 的漂移只当 warning）。
  > 同理 `hardware.yaml` 里的 `command_timeout_s: 0.15` 也不生效，
  > **真正生效的是 `robot.yaml:7` 的 `0.5`**（08-18 修「零速插入顿挫」的那个值）。
- **任何 PASS 都不等于舵机到位**：机械臂是纯开环、无位置反馈，服务成功只说明
  「命令被接受并走完流程」。位移、方向、幅度**必须现场目视确认**。

### 4.5 日志落在哪

每次启动自动建目录：

```text
~/robogame/outputs/field_console/YYYYMMDD_HHMMSS_microseconds/
├── session.json
├── events.jsonl
├── telemetry.jsonl
└── raw/*.log
```

网页上的「清空」**只清浏览器显示，不删这些证据文件**。

### 4.6 断线会发生什么

- **浏览器或 SSH 隧道断开**：手动控制心跳 300 ms 后超时 → 自动发零速；
  已经启动的观察/巡线进程**保留**（不会自动停）。
- **网页服务进程自身退出**：先归零，再停掉它启动的子进程。
- **关掉网页不会自动停止已启动的巡线进程** —— 要停就点「停止巡线」或「全部停止」。

---

## 5. 常见故障速查

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 网页打不开 | 没带 `-L` 隧道 / SSH 窗口关了 | 重连并带 `-L 8765:127.0.0.1:8765`；确认本机 8765 没被占 |
| 网页打不开（隧道在） | 树莓派上服务没跑 | SSH 里 `ps aux \| grep field_dashboard`；没有就按第 4 节启动 |
| SSH 连不上但手机能上网 | 热点做了客户端隔离 / IP 又变了 | 用方法 2 扫描；或看热点客户端列表 |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | IP 被分给了别的设备 | `ssh-keygen -R <该IP>` 后重连 |
| 页面里状态全灰 | `bridge` 没启动 / 串口没打开 | 启 `bridge`；`ls -l /dev/serial/by-id/` 确认 STM32 VCP |
| 车走走停停、顿挫 | 双实例打架 / 心跳或超时问题 | `ps aux \| grep <节点>` 清残留；节点必须单实例 |
| 命令没反应 | 物理授权掉了（STM32 重启会清授权） | 重新长按 PB2；看页面「物理授权」灯 |
| 改了网页代码没生效 | 改的是 Windows 副本，或没重启服务 | 同步到树莓派 → 重启服务 → **Ctrl+F5 强制刷新** |

### 启动顺序铁律（08-18 现场教训）

1. **必须单实例**：启动前先 `ps aux | grep <节点>` 确认无残留。
2. **按依赖顺序**：`robot_bridge` → `localization` → `motion_control`。
3. **真车测试脚本用 50 Hz 发送**（`time.sleep(0.02)`）。
   **10 Hz 会触发 `command_timeout` 插零速**，表现为一段一段地走。

---

## 6. 这套东西能证明什么 / 不能证明什么

**能证明**：网页显示的状态、时间戳、日志，来自树莓派实际收到的帧与节点实际状态。

**不能证明**：

- 进程「正在运行」只说明它**还没退出**，**不等于**串口通了、底盘能动、机构正常。
- 页面里的速度数字**不代表**电机真的执行了。
- 「自检 PASS」**不等于**机械臂到位（纯开环无反馈）。
- 网页定距的「到达」**不等于**实际位移正确（**换电机后里程计需重新标定**；2026-08-18 旧电机时期曾有 ~10 倍偏差，该记录不自动适用于当前硬件）。
- 任何网页结果都**不能替代物理急停**。

---

## 7. 相关文档

- `docs/guides/FIELD_DASHBOARD.md` —— 网页各卡片细节与证据边界
- `docs/guides/FIELD_CONSOLE.md` —— 单终端控制台（不想用网页时的替代方案）
- `docs/field/FIELD_SESSION_CHECKLIST.md` —— 现场上车流程
- `docs/field/RASPBERRY_PI_DEPLOYMENT_LOG_2026-08-18.md` —— 树莓派部署与真车联调事实记录
- `docs/field/ODOM_DROP_ROOT_CAUSE_2026-08-19.md` —— 里程计丢帧根因（LOCALIZATION_ERROR 的来源）
