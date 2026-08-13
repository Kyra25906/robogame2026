# RoboGame2026 文档导航

本页是 `docs/` 的总入口。第一次接触项目时，不需要从头阅读全部文件；先按当前任务选择入口。

最后更新：2026-08-13

## Agent 交接入口（新 agent 先读这里）

如果你是接手开发的 agent，按这个顺序读：

1. **[AGENT.md](AGENT.md)** —— 协作约定：先读，了解怎么和用户配合、每轮流程与边界
2. **[TODO_AND_ISSUES.md](TODO_AND_ISSUES.md)** —— 当前进度、阻塞项、下一步（必读，也是你完成后要更新的地方）
3. 按你的任务域，读对应最新的 handoff：
   - 整车 / 硬件对接 → `field/RASPBERRY_PI_REAL_CAR_AGENT_HANDOFF_2026-08-12.md`
   - 视觉 → `vision/VISION_AGENT_HANDOFF_2026-08-12.md`
   - 集成 / 部署 → `history/INTEGRATION_HANDOFF_2026-08-12.md`
4. 本页（README.md）—— 文档导航，按需查

**完成后**：更新 [TODO_AND_ISSUES.md](TODO_AND_ISSUES.md) 的完成证据和新问题；如有跨会话交接，再写一份新的 handoff 到对应域目录。

当前主线分支：`integration/robogame-t26`。

## 每天首先查看

| 目的 | 文档 | 使用方式 |
|---|---|---|
| 确认当前进度、阻塞项和下一步 | [TODO_AND_ISSUES.md](TODO_AND_ISSUES.md) | 每次开发结束更新完成证据和新问题 |
| 查看 8 月 13—18 日安排 | [PLAN_2026-08-13_TO_18.md](PLAN_2026-08-13_TO_18.md) | 每天只执行当前阶段，未通过不得跳级 |
| 复习开发中学到的知识 | [LEARNING_LOG.md](LEARNING_LOG.md) | 面向编程基础较少的同学持续追加 |

## 按工作类型查找

### 现场接入与安全

- [给电控组待确认清单.md](field/给电控组待确认清单.md)：还需要电控组补齐的逐字节载荷表、错误码、参数（真车对接的硬阻塞）。
- [给机械组待确认清单.md](field/给机械组待确认清单.md)：还需要机械组补齐的几何/夹爪/升降/动作参数。
- [MECHANICAL_PARAMETERS_CONFIRMATION.md](field/MECHANICAL_PARAMETERS_CONFIRMATION.md)：机械组填写用的完整版问卷（M-01~M-25）。
- [算法给电控与机械的接口说明.md](field/算法给电控与机械的接口说明.md)：三方责任和接口说明。
- [给硬件组的交接手册_2026-08-12.md](field/给硬件组的交接手册_2026-08-12.md)：给硬件组的交接手册（含协议表格）。
- [FAULT_INJECTION_TEST_CARD.md](field/FAULT_INJECTION_TEST_CARD.md)：急停、失联、状态过期、机构故障等故障注入步骤。
- [TIME_BUDGET.csv](field/TIME_BUDGET.csv)：抓取与放置流程的时间预算，现场回填实测值。
- [INTEGRATION_CHECKLIST.md](field/INTEGRATION_CHECKLIST.md)：整车逐级集成检查。

> 旧冻结表已归档到 `field/_archived/FREEZE_TABLE.md`；旧 `MCU_PROTOCOL.md` 已删除（消息编号过期，现行编号见电控清单）。

### 视觉开发与验收

- [VISION_MODULE.md](vision/VISION_MODULE.md)：视频处理、JSONL schema v2、时间段标注和 HTML 报告。
- [连续帧确认使用说明.md](vision/连续帧确认使用说明.md)：连续帧过滤参数与使用方法。
- [VISION_AGENT_HANDOFF_2026-08-12.md](vision/VISION_AGENT_HANDOFF_2026-08-12.md)：视觉 agent 交接。
- 视频、JSONL、标注和报告默认保存在 `results/`，不进入普通 Git 历史，必须另行备份。

### 新手学习与项目协作

- [BEGINNER_PROJECT_LEARNING_GUIDE.md](guides/BEGINNER_PROJECT_LEARNING_GUIDE.md)：系统性入门教程。
- [GETTING_STARTED.md](guides/GETTING_STARTED.md)：环境、编译和首次运行。
- [Windows本地Python环境说明.md](guides/Windows本地Python环境说明.md)：Windows 上该用哪个 Python（两个 Python 的问题）。
- [Windows到Ubuntu一键同步.md](guides/Windows到Ubuntu一键同步.md)：按 Git 提交号建立 Ubuntu 干净验收副本。
- [GitHub两人代码协作说明.md](team/GitHub两人代码协作说明.md)：分支、提交和协作方法。

## 历史记录

以下文件保留当时决策和开发过程，不作为当前任务入口：

- `history/DEV_LOG_2026-08-04.md`
- `history/PAUSE_HANDOFF_2026-08-06.md`
- `history/SESSION_2026-08-08_SUMMARY.md`
- `history/DEVELOPMENT_PLAN_2026-08-06_TO_14.md`（原始计划）
- `history/INTEGRATION_HANDOFF_2026-08-12.md`
- `history/RoboGame2026从远程开发到现场推进_大白话总结.md`
- `history/RoboGame2026分支整理建议_2026-08-08.md`

## 当前软件结论

- 主线 `integration/robogame-t26`，已合并 vision 分支，不再分叉。
- 软件模拟闭环、取消、四种放置终态已经完成。
- 231 项测试通过（Windows 与 Ubuntu 部署环境一致）。
- 真车对接仍阻塞于：电控逐字节载荷表 + 机构动作合同 + 机械参数（见两张待确认清单）。
- field 无硬件模式保持通信不可信、拒绝机构动作并门控速度命令。
- 真实闭环不能用模拟 PASS 代替，必须等 MCU 载荷、相机安装和现场数据。
