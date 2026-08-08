# RoboGame2026 文档导航

本页是 `docs/` 的总入口。第一次接触项目时，不需要从头阅读全部文件；先按当前任务选择入口。

最后更新：2026-08-08

## 每天首先查看

| 目的 | 文档 | 使用方式 |
|---|---|---|
| 确认当前进度、阻塞项和下一步 | [TODO_AND_ISSUES.md](TODO_AND_ISSUES.md) | 每次开发结束更新完成证据和新问题 |
| 查看 8 月 9—14 日安排 | [PLAN_2026-08-09_TO_14.md](PLAN_2026-08-09_TO_14.md) | 每天只执行当前阶段，未通过不得跳级 |
| 查看现场第一天逐项操作 | [FIELD_DAY1_EXECUTION_ORDER.md](FIELD_DAY1_EXECUTION_ORDER.md) | 带到现场照表执行并保存证据 |
| 复习开发中学到的知识 | [LEARNING_LOG.md](LEARNING_LOG.md) | 面向编程基础较少的同学持续追加 |

## 按工作类型查找

### 现场接入与安全

- [FREEZE_TABLE.md](FREEZE_TABLE.md)：电控、机械、算法共同确认的接口冻结表；未知字段不得猜测。
- [FAULT_INJECTION_TEST_CARD.md](FAULT_INJECTION_TEST_CARD.md)：急停、失联、状态过期、机构故障等故障注入步骤。
- [TIME_BUDGET.csv](TIME_BUDGET.csv)：抓取与放置流程的时间预算，现场回填实测值。
- [INTEGRATION_CHECKLIST.md](INTEGRATION_CHECKLIST.md)：整车逐级集成检查。
- [算法给电控与机械的接口说明.md](算法给电控与机械的接口说明.md)：三方责任和接口说明。
- [MCU_PROTOCOL.md](MCU_PROTOCOL.md)：已经冻结的串口外层协议；内部载荷仍以电控最终表格为准。

### 视觉开发与验收

- [VISION_MODULE.md](VISION_MODULE.md)：视频处理、JSONL schema v2、时间段标注和 HTML 报告。
- [连续帧确认使用说明.md](连续帧确认使用说明.md)：连续帧过滤参数与使用方法。
- 视频、JSONL、标注和报告默认保存在 `results/`，不进入普通 Git 历史，必须另行备份。

### 新手学习与项目协作

- [BEGINNER_PROJECT_LEARNING_GUIDE.md](BEGINNER_PROJECT_LEARNING_GUIDE.md)：系统性入门教程。
- [LEARNING_LOG.md](LEARNING_LOG.md)：按开发过程解释工程知识。
- [GETTING_STARTED.md](GETTING_STARTED.md)：环境、编译和首次运行。
- [Windows到Ubuntu一键同步.md](Windows到Ubuntu一键同步.md)：按 Git 提交号建立 Ubuntu 干净验收副本。
- [GitHub两人代码协作说明.md](GitHub两人代码协作说明.md)：分支、提交和协作方法。

## 历史记录

以下文件保留当时决策和开发过程，不作为当前任务入口：

- `DEV_LOG_2026-08-04.md`
- `PAUSE_HANDOFF_2026-08-06.md`
- `SESSION_2026-08-08_SUMMARY.md`
- `DEVELOPMENT_PLAN_2026-08-06_TO_14.md`（原始计划，当前执行以新计划为准）

## 当前软件结论

- 软件模拟闭环、取消、四种放置终态已经完成。
- Windows 全项目 185 项测试通过。
- Ubuntu 提交 `347e020` 构建 9 个 ROS2 包，配置检查零错误、零警告。
- field 无硬件模式会保持通信不可信、拒绝机构动作并门控速度命令。
- 真实闭环仍依赖 MCU 逐字节载荷、机构动作合同、相机安装和现场数据，不能用模拟 PASS 代替。
