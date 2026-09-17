# RoboGame2026 文档导航

本页是 `docs/` 的总入口。第一次接触项目时，不需要从头阅读全部文件；先按当前任务选择入口。

最后更新：2026-09-17

本次修订做了什么：修正主线分支名（`integration/line-follow-t26`）；新增「现场联调（2026-09 当前）」一节，收录树莓派 SSH/网页指南、现场网页看板、现场检查清单、08-18 部署联调记录和 08-19 真车对接设计稿；把已过期的 08-13~18 计划页移入历史记录；更正测试取数说明与真车对接阻塞项（08-18 真车握手已实测、08-19 机械参数已冻结）。

## Agent 交接入口（新 agent 先读这里）

如果你是接手开发的 agent，按这个顺序读：

1. **[AGENT.md](AGENT.md)** —— 协作约定：先读，了解怎么和用户配合、每轮流程与边界（配合 [ENGINEERING_DISCIPLINE.md](ENGINEERING_DISCIPLINE.md) 一起读，先带上工程底线意识）
2. **[TODO_AND_ISSUES.md](TODO_AND_ISSUES.md)** —— 当前进度、阻塞项、下一步（必读，也是你完成后要更新的地方）
3. 按你的任务域，读对应最新的 handoff：
   - 整车 / 硬件对接 → `field/RASPBERRY_PI_REAL_CAR_AGENT_HANDOFF_2026-08-12.md`
   - 视觉 → `vision/VISION_AGENT_HANDOFF_2026-08-12.md`
   - 集成 / 部署 → `history/INTEGRATION_HANDOFF_2026-08-13.md`
   - **真车 / 现场（最新）** → [field/RASPBERRY_PI_DEPLOYMENT_LOG_2026-08-18.md](field/RASPBERRY_PI_DEPLOYMENT_LOG_2026-08-18.md)（08-18 树莓派部署 + 真车通信/底盘实测记录）
   - **机械对接 / 设计（最新）** → [history/SESSION_2026-08-19_SUMMARY.md](history/SESSION_2026-08-19_SUMMARY.md)（08-19 机械组当面确认与设计收敛）
4. 本页（README.md）—— 文档导航，按需查

**完成后**：更新 [TODO_AND_ISSUES.md](TODO_AND_ISSUES.md) 的完成证据和新问题；如有跨会话交接，再写一份新的 handoff 到对应域目录。

当前主线分支：`integration/line-follow-t26`。（`integration/robogame-t26` 已是它的祖先，2026-09-17 用 `git merge-base --is-ancestor origin/integration/robogame-t26 origin/integration/line-follow-t26` 验证退出码为 0。）

## 现场联调（2026-09 当前）

现场先读这一节。**树莓派经手机热点上网，IP 每次重连都会变，不要写死 IP**（优先用主机名，或 `python tools/find_pi_on_lan.py` 扫描）。

- [RASPBERRY_PI_SSH_AND_WEB_GUIDE.md](guides/RASPBERRY_PI_SSH_AND_WEB_GUIDE.md)：**现场第一份，先读这个**。树莓派 SSH（用户名 `rg26`、主机名 `robogame-t26-rpi4.local`）+ 网页看板全流程，目标是只开一个 SSH 窗口和一个浏览器页面。
- [FIELD_SESSION_CHECKLIST.md](field/FIELD_SESSION_CHECKLIST.md)：每次上车联调前/中/后对照执行（08-18 复盘沉淀，操作/环境类问题约占 40%）。
- [FIELD_DASHBOARD.md](guides/FIELD_DASHBOARD.md)：单 SSH 窗口联调网页（`tools/field_dashboard.py`），节点启停、状态、日志、手动开车、巡线诊断都在一个页面里。
- [FIELD_CONSOLE.md](guides/FIELD_CONSOLE.md)：单终端控制台替代方案（`tools/field_console.py`，只做进程管理和日志归并，不发动作命令）。
- [GRASP_ALIGNMENT_WEB.md](guides/GRASP_ALIGNMENT_WEB.md)：抓取对准在网页上怎么看（已由横移改为「原地转向 + 沿车头轴前进」，因为真车 VY 恒 0）。
- [真车对接设计稿_2026-08-19.md](field/真车对接设计稿_2026-08-19.md)：**真车对接的唯一执行文档**（真车模型、状态机、动作序列、参数表、相机决策链、证据边界）。
- [给机械组现场问答表_2026-08-19.md](field/给机械组现场问答表_2026-08-19.md)：08-19 与机械组当面确认的原始记录（C-1~C-18、S-1~S-5、D-1~D-3、P-1），也是机械参数的冻结依据。

## 每天首先查看

| 目的 | 文档 | 使用方式 |
|---|---|---|
| 确认当前进度、阻塞项和下一步 | [TODO_AND_ISSUES.md](TODO_AND_ISSUES.md) | 每次开发结束更新完成证据和新问题 |
| 复习开发中学到的知识 | [LEARNING_LOG.md](LEARNING_LOG.md) | 面向编程基础较少的同学持续追加 |
| 对照工程底线自检 | [ENGINEERING_DISCIPLINE.md](ENGINEERING_DISCIPLINE.md) | 收工时按完成标准（DoD）自检，区分「已验证 / 未验证」 |

## 按工作类型查找

### 现场接入与安全

- [给电控组待确认清单.md](field/给电控组待确认清单.md)：电控侧待确认问题记录。**注意**：表中「待填」已由该文件第 3 行声明为历史问题记录，执行时以 [STM32_SERIAL_PROTOCOL_V1.md](field/STM32_SERIAL_PROTOCOL_V1.md)（冻结候选版）为准。
- [给机械组待确认清单.md](field/给机械组待确认清单.md)：还需要机械组补齐的几何/夹爪/升降/动作参数。**部分已由 08-19 问答表回答**：轮径 128mm、减速比 1:36、轴距 475mm、轮距 465mm、方块边长 100±5mm 已冻结，答案见 [给机械组现场问答表_2026-08-19.md](field/给机械组现场问答表_2026-08-19.md)「〇、机械组当面确认」；仍未确认：编码器 PPR、方块质量、肩/腕角度范围、各动作耗时、安全收回姿态。
- [MECHANICAL_PARAMETERS_CONFIRMATION.md](field/MECHANICAL_PARAMETERS_CONFIRMATION.md)：机械组填写用的完整版问卷（M-01~M-25）。
- [算法给电控与机械的接口说明.md](field/算法给电控与机械的接口说明.md)：三方责任和接口说明。
- [给硬件组的交接手册_2026-08-12.md](field/给硬件组的交接手册_2026-08-12.md)：给硬件组的交接手册（含协议表格）。
- [FAULT_INJECTION_TEST_CARD.md](field/FAULT_INJECTION_TEST_CARD.md)：急停、失联、状态过期、机构故障等故障注入步骤。
- [TIME_BUDGET.csv](field/TIME_BUDGET.csv)：抓取与放置流程的时间预算，现场回填实测值。
- [INTEGRATION_CHECKLIST.md](field/INTEGRATION_CHECKLIST.md)：整车逐级集成检查（该清单部分内容仍基于旧硬件假设——例如第 9 行仍列 lift 命令，而 08-19 已冻结真车无升降；引用前请对照 [真车对接设计稿_2026-08-19.md](field/真车对接设计稿_2026-08-19.md) 核对）。

> 旧冻结表已归档到 `field/_archived/FREEZE_TABLE.md`；旧 `MCU_PROTOCOL.md` 已删除（消息编号过期，现行编号与逐字节载荷见 [STM32_SERIAL_PROTOCOL_V1.md](field/STM32_SERIAL_PROTOCOL_V1.md)）。

### 视觉开发与验收

- [VISION_MODULE.md](vision/VISION_MODULE.md)：视频处理、JSONL schema v2、时间段标注和 HTML 报告。
- [连续帧确认使用说明.md](vision/连续帧确认使用说明.md)：连续帧过滤参数与使用方法。
- [VISION_AGENT_HANDOFF_2026-08-12.md](vision/VISION_AGENT_HANDOFF_2026-08-12.md)：视觉 agent 交接。
- [GF100_CAMERA_CALIBRATION_CANDIDATE_REPORT_2026-08-12.md](vision/GF100_CAMERA_CALIBRATION_CANDIDATE_REPORT_2026-08-12.md)：GF100 候选标定（37 张棋盘格，RMS 0.82px）。
- [P1_ANGLE_AND_OCCLUSION_ANALYSIS_2026-08-12.md](vision/P1_ANGLE_AND_OCCLUSION_ANALYSIS_2026-08-12.md)：侧转与遮挡分析。
- [VISION_FIELD_TASKS_2026-08-12.md](vision/VISION_FIELD_TASKS_2026-08-12.md)：现场任务清单。
- [CURRENT_STATUS_HANDOFF_REAL_CAMERA_2026-08-11.md](vision/CURRENT_STATUS_HANDOFF_REAL_CAMERA_2026-08-11.md)：真机视觉现状。
- 视频、JSONL、标注和报告默认保存在 `results/`，不进入普通 Git 历史，必须另行备份。

### 新手学习与项目协作

- [BEGINNER_PROJECT_LEARNING_GUIDE.md](guides/BEGINNER_PROJECT_LEARNING_GUIDE.md)：系统性入门教程。
- [GETTING_STARTED.md](guides/GETTING_STARTED.md)：环境、编译和首次运行。
- [Windows本地Python环境说明.md](guides/Windows本地Python环境说明.md)：Windows 上该用哪个 Python（两个 Python 的问题）。
- [Windows到Ubuntu一键同步.md](guides/Windows到Ubuntu一键同步.md)：按 Git 提交号建立 Ubuntu 干净验收副本。
- [GitHub两人代码协作说明.md](team/GitHub两人代码协作说明.md)：分支、提交和协作方法。

### STM32 串口协议

- [STM32_SERIAL_PROTOCOL_V1.md](field/STM32_SERIAL_PROTOCOL_V1.md)：当前 V1 消息编号、逐字节载荷、值域和固定十六进制向量。
- [INTEGRATION_HANDOFF_2026-08-13.md](history/INTEGRATION_HANDOFF_2026-08-13.md)：当日硬件识别、树莓派串口回环、部署决策和未提交工作区交接。

## 历史记录

以下文件保留当时决策和开发过程，不作为当前任务入口：

- `history/DEV_LOG_2026-08-04.md`
- `history/PAUSE_HANDOFF_2026-08-06.md`
- `history/SESSION_2026-08-08_SUMMARY.md`
- `history/DEVELOPMENT_PLAN_2026-08-06_TO_14.md`（原始计划）
- `history/INTEGRATION_HANDOFF_2026-08-12.md`
- `history/RoboGame2026从远程开发到现场推进_大白话总结.md`
- `history/RoboGame2026分支整理建议_2026-08-08.md`
- [PLAN_2026-08-13_TO_18.md](PLAN_2026-08-13_TO_18.md)（8 月 13—18 日计划，**窗口已于 2026-08-18 结束，仅供回看，不要再按它排期**）

## 当前软件结论

- 主线 `integration/line-follow-t26`（08-18 已 cherry-pick 并入第二位同学 XuLingfeng 的 `feature/line_follow`，当日 HEAD `b2f7557`）；`integration/robogame-t26` 已是它的祖先，不再分叉。
- 软件模拟闭环、取消、四种放置终态已经完成。
- 测试数量**不在文档里写死**（每轮都在变：08-13 快照的 `258`、阶段 A 的 `399+` 都已过期；08-18 树莓派实车记录为 `499`，到 2026-09-17 又已超出）。现场取数命令：`D:\python.exe -B tools\run_tests.py`（DSH 沙箱适配的 unittest 包装器，**以输出的 `Ran N tests` 为准**）；只数测试函数定义可用 `python -c "import pathlib,re;print(sum(len(re.findall(r'def test_',p.read_text(encoding='utf-8'))) for p in pathlib.Path('tests').rglob('*.py')))"`。两个口径定义不同，**数字不相等是正常的，不要互相印证**。2026-09-17 Windows 实测：定义数 `1050`；`tools/run_tests.py` 报 `Ran 1023 tests`、`errors=5, skipped=68`，其中 5 个 error 全部是本机环境造成（3 个 `import cv2` 失败 + 2 个 `field_console` 子进程建管道被沙箱拒绝 WinError 5），**不代表代码结论**，权威全量仍需在 Ubuntu / 树莓派上跑。Windows 上需 rclpy 的行为测试会 skip，Ubuntu/Pi 上全量运行。测试数量每轮都在变，**引用前必须自己重跑上面两条命令**。
- V1 协议文档与上位机编解码已实现并随 `integration/line-follow-t26` 合入主线（编码器在 `ros2_ws/src/robogame_core/robogame_core/serial_protocol.py`，测试在 `tests/test_serial_protocol.py`）；08-18 真车实测 STATUS 解码成功（`detail="decoded MCU V1 STATUS"`，50Hz 稳定）。
- 真实 STM32 握手已实测通过：HELLO→ACK→READY 正常（`validated type=0x13`），串口设备 `/dev/serial/by-id/usb-STMicroelectronics_STM32_Virtual_ComPort_307A39653433-if00` 与 `robot_field.yaml` 一致；架空底盘的前进/后退/原地转方向已验证。早先 `/dev/ttyUSB0` 收到 `b'hello'` 的回环只证明 USB 转串口基础收发，该结论已被本项取代。
- 树莓派当前部署：GitHub clone `integration/line-follow-t26` 到 `~/robogame`，`rosdep` 装齐依赖 + `colcon build` 9 包成功，树莓派单测 `499 tests OK（skipped=54）`，与 Windows/Ubuntu 一致。08-18 之后的新改动尚未重新部署。
- 真车对接阻塞项已收敛（08-19 与机械组当面确认后）。**已解除**：机械几何参数冻结（轮径 128mm / 减速比 1:36 / 轴距 475mm / 轮距 465mm / 方块 100±5mm EVA）；动作合同确认（GRAB/RELEASE 是开关式命令、软件不下发开合量；真车禁用 LIFT 改单层地面放置；抓取期间底盘锁死；底盘运动期间机构不动或收回；禁发横移 VY）；逐字节载荷由 [STM32_SERIAL_PROTOCOL_V1.md](field/STM32_SERIAL_PROTOCOL_V1.md) 承载，STATUS/HELLO/ACK/READY 已在真车验证。**仍开放**：编码器 PPR、方块质量、肩/腕角度映射、各动作耗时、安全收回姿态，以及**机械臂自动指令通道（arm.c）未就绪**——臂动作的前提。详见 [真车对接设计稿_2026-08-19.md](field/真车对接设计稿_2026-08-19.md)。
- field 无硬件模式保持通信不可信、拒绝机构动作并门控速度命令。
- 真实闭环不能用模拟 PASS 代替，必须等 MCU 机构载荷（arm.c 自动指令通道）、相机安装和现场数据。
