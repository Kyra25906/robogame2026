# 文档盘点与分类（DOC_INVENTORY）

> **本文件由 `tools/docs_inventory.py --write` 生成，不要手改**——
> 手改会在下一次生成时被覆盖，而且会让「分类」和「登记表」不一致。
> 要改分类：改 `tools/docs_inventory.py` 里的 `REGISTRY`，再重新生成。

本页回答一个问题：**这份文档现在还算不算数**。判定标准只有一条——
读者现在该拿它做什么。内容与代码是否一致由 `tools/docs_audit.py` 与人工复核负责，
本页不重复。

## 分类规则

| 类别 | 含义 | 硬要求 |
|---|---|---|
| `current` | 现在照它做动作（命令、步骤、清单）。 | 必须被 `tools/docs_audit.py` 覆盖（否则「审计通过」=「没检查」） |
| `reference` | 现在照它做判断（协议字节、参数含义、接口责任、规则对照）。长期有效，按需查。 | 无（但改了事实来源要同步引用它的文档） |
| `history` | 某一天的事实与决策，内容冻结。只用于回看与交接，不用于执行。 | 不改内容（改了就不是留痕）；被引用时要写明日期 |
| `archived` | 已确认过期：**不要照做**。正文必须有「> ⚠️ 已过期」横幅并写明替代文档。 | 正文开头必须有 `> ⚠️ 已过期` 横幅 + 替代文档 |
| `teaching` | 教学材料，面向学习，不参与现场执行。 | 无 |

## 统计

| 类别 | 份数 |
|---|---|
| current | 18 |
| reference | 36 |
| history | 32 |
| archived | 5 |
| teaching | 8 |
| **合计** | **99** |

## current（18 份）—— 现在照它做动作（命令、步骤、清单）。

| 文档 | 标题 | 文件修改时间（本机） |
|---|---|---|
| `AGENTS.md` | 教学式增量开发与 Agent 协作约定（通用版） | 2026-09-18 |
| `README.md` | RoboGame 2026 robot software | 2026-09-18 |
| `docs/README.md` | RoboGame2026 文档导航 | 2026-09-18 |
| `docs/AGENT.md` | 教学式增量开发与 Agent 协作约定（通用版） | 2026-09-18 |
| `docs/ENGINEERING_DISCIPLINE.md` | 工程开发纪律（RoboGame 2026） | 2026-09-18 |
| `docs/TODO_AND_ISSUES.md` | RoboGame2026 待办任务与已知问题 | 2026-09-18 |
| `docs/field/首次上车执行清单.md` | 首次上车执行清单（B2/B3/B4：上电后无人干预完成 6 分钟全流程） | 2026-09-18 |
| `docs/field/上电自主完赛流程.md` | 上电后无人干预自主完赛：操作流程与检查点 | 2026-09-18 |
| `docs/field/现场待测清单_B2B3_待填值.md` | 现场待测清单（B2/B3）：哪些值是猜的、怎么测、错了会怎样 | 2026-09-18 |
| `docs/field/真车对接设计稿_2026-08-19.md` | 真车对接设计稿（2026-08-19） | 2026-09-18 |
| `docs/field/FIELD_SESSION_CHECKLIST.md` | 现场上车 Checklist（2026-08-18 联调复盘沉淀） | 2026-09-18 |
| `docs/field/FAULT_INJECTION_TEST_CARD.md` | 故障模式可复现测试步骤 | 2026-09-18 |
| `docs/field/INTEGRATION_CHECKLIST.md` | Integration and acceptance checklist | 2026-09-18 |
| `docs/guides/RASPBERRY_PI_SSH_AND_WEB_GUIDE.md` | 树莓派 SSH + 网页操作指南（2026-09-17 版） | 2026-09-18 |
| `docs/guides/FIELD_DASHBOARD.md` | 单 SSH 窗口联调网页 | 2026-09-18 |
| `docs/guides/FIELD_CONSOLE.md` | 单终端现场联调控制台 | 2026-09-17 |
| `docs/guides/GETTING_STARTED.md` | Ubuntu + ROS 2 quick start | 2026-09-18 |
| `docs/guides/GRASP_ALIGNMENT_WEB.md` | 抓取对准：网页怎么看（不开额外终端） | 2026-09-18 |

## reference（36 份）—— 现在照它做判断（协议字节、参数含义、接口责任、规则对照）。长期有效，按需查。

| 文档 | 标题 | 文件修改时间（本机） |
|---|---|---|
| `docs/DOC_INVENTORY.md` | 文档盘点与分类（DOC_INVENTORY） | 2026-09-18 |
| `docs/RULE_COVERAGE.md` | 规则条款覆盖矩阵（RULE_COVERAGE） | 2026-09-18 |
| `docs/术语表.md` | RoboGame 2026 专业术语表 | 2026-09-18 |
| `docs/树莓派_STM32机械机构通信协议_v1.0.md` | 树莓派—STM32 机械机构通信协议 v1.0 | 2026-09-18 |
| `docs/calibration/四自由度眼在手上_操作说明.md` | 四自由度眼在手上：棋盘格与采集操作 | 2026-09-09 |
| `docs/field/STM32_SERIAL_PROTOCOL_V1.md` | STM32 与树莓派串口通信协议 V1.0 | 2026-09-18 |
| `docs/field/算法给电控与机械的接口说明.md` | 算法给电控与机械的接口说明 | 2026-09-18 |
| `docs/field/GENERAL_FIELD_MAP_2026.md` | RoboGame 2026 一般场地图与状态切换基线 | 2026-08-19 |
| `docs/field/给机械组现场问答表_2026-08-19.md` | 给机械组：一页纸现场问答表（2026-08-19） | 2026-08-19 |
| `docs/field/给机械组待确认清单.md` | 给机械组：待确认清单 | 2026-09-17 |
| `docs/field/给电控组待确认清单.md` | 给电控组：待确认清单 | 2026-09-17 |
| `docs/field/给硬件组的交接手册_2026-08-12.md` | 算法→硬件（电控+机械）交接手册 | 2026-09-18 |
| `docs/field/MECHANICAL_PARAMETERS_CONFIRMATION.md` | RoboGame2026 机械机构与动作参数确认表 | 2026-08-13 |
| `docs/field/MANIPULATOR_ACTION_EXTENSION.md` | 机械动作确认后的软件扩展说明 | 2026-08-04 |
| `docs/field/MECHANISM_0x20_INTERFACE_ALIGNMENT_2026-08-18.md` | 机械臂 0x20 接口对齐（算法 ↔ 电控，2026-08-18） | 2026-09-17 |
| `docs/field/LINE_TELEMETRY_0x14_INTERFACE_ALIGNMENT_2026-08-19.md` | 巡线遥测 0x14 接口对齐（算法 ↔ 电控，2026-08-19） | 2026-09-18 |
| `docs/field/evidence/localization/README.md` | Localization navigation acceptance evidence | 2026-08-14 |
| `docs/guides/Windows本地Python环境说明.md` | Windows 本地 Python 环境说明 | 2026-08-13 |
| `docs/guides/Windows到Ubuntu一键同步.md` | Windows 到 Ubuntu 一键同步 | 2026-09-18 |
| `docs/line_follow/README.md` | 巡线模块说明 | 2026-09-17 |
| `docs/line_follow/CALIBRATION_AND_HARDWARE.md` | 八路灰度标定流程与硬件确认状态 | 2026-09-18 |
| `docs/line_follow/LINE_TELEMETRY_0x14_STM32_TEMPLATE.md` | STM32 侧 0x14 发送模板 | 2026-09-18 |
| `docs/team/两人算法最终分工.md` | 两人算法最终分工 | 2026-08-18 |
| `docs/team/GitHub两人代码协作说明.md` | GitHub 两人代码协作说明 | 2026-09-18 |
| `docs/vision/VISION_MODULE.md` | Independent cube-vision module | 2026-09-18 |
| `docs/vision/连续帧确认使用说明.md` | 连续帧确认：新手使用说明 | 2026-08-04 |
| `tools/工具路径记录.md` | 工具路径记录 | 2026-08-17 |
| `ros2_ws/src/cube_perception/README.md` | cube_perception 使用说明 | 2026-09-18 |
| `ros2_ws/src/localization/README.md` | localization | 2026-09-17 |
| `ros2_ws/src/manipulator_client/README.md` | manipulator_client 使用说明 | 2026-09-18 |
| `ros2_ws/src/mission_manager/README.md` | mission_manager 使用说明 | 2026-09-18 |
| `ros2_ws/src/motion_control/README.md` | motion_control 使用说明 | 2026-09-18 |
| `ros2_ws/src/robot_bridge/README.md` | robot_bridge：树莓派串口与机械臂控制说明 | 2026-09-18 |
| `ros2_ws/src/robogame_interfaces/README.md` | robogame_interfaces 使用说明 | 2026-09-17 |
| `ros2_ws/src/robogame_core/README.md` | robogame_core 使用说明 | 2026-09-18 |
| `ros2_ws/src/robogame_bringup/README.md` | robogame_bringup 使用说明 | 2026-09-18 |

## history（32 份）—— 某一天的事实与决策，内容冻结。只用于回看与交接，不用于执行。

| 文档 | 标题 | 文件修改时间（本机） |
|---|---|---|
| `docs/history/B3_WORK_LOG_2026-09-17.md` | B3 工作留痕（路线 → 路口转弯 → 坡道 → 取放 → 自主完赛） | 2026-09-18 |
| `docs/history/DEV_LOG_2026-08-04.md` | RoboGame2026 开发日志：单方块模拟闭环 | 2026-08-08 |
| `docs/history/DEVELOPMENT_PLAN_2026-08-06_TO_14.md` | RoboGame2026 开发计划：2026 年 8 月 6 日至 14 日 | 2026-08-06 |
| `docs/history/INTEGRATION_HANDOFF_2026-08-12.md` | RoboGame2026 非视觉代码与总集成交接（2026-08-12） | 2026-09-18 |
| `docs/history/INTEGRATION_HANDOFF_2026-08-13.md` | RoboGame2026 集成交接（2026-08-13） | 2026-09-18 |
| `docs/history/PAUSE_HANDOFF_2026-08-06.md` | RoboGame2026 暂停工作交接记录 | 2026-08-08 |
| `docs/history/RoboGame2026从远程开发到现场推进_大白话总结.md` | RoboGame2026：从远程开发到现场推进的大白话总结 | 2026-08-13 |
| `docs/history/RoboGame2026分支整理建议_2026-08-08.md` | RoboGame2026 Git 分支整理建议 | 2026-08-13 |
| `docs/history/SESSION_2026-08-08_SUMMARY.md` | 2026-08-08 会话总结：远程开发最后一天 | 2026-08-08 |
| `docs/history/SESSION_2026-08-19_SUMMARY.md` | SESSION 2026-08-19 总结：机械组当面确认 + 视觉方案收敛 | 2026-09-18 |
| `docs/RoboGame2026_本对话树莓派软件推进总结_2026-08-15.md` | RoboGame2026 本对话树莓派软件推进总结 | 2026-09-18 |
| `docs/RoboGame2026_第三轮复审修复方案_2026-08-17.md` | RoboGame2026 第三轮复审修复方案(2026-08-17) | 2026-09-18 |
| `docs/field/ARM_GPIO_FIND_PIN_TEST_2026-08-19.md` | 用「点灯测试」找出舵机信号针（2026-08-19） | 2026-09-17 |
| `docs/field/CHASSIS_ENABLE_CHAIN_2026-08-18.md` | 底盘自动运动使能条件清单（2026-08-18，电控确认） | 2026-09-18 |
| `docs/field/ODOM_DROP_ROOT_CAUSE_2026-08-19.md` | ODOM 跳变根因分析：为什么只在树莓派控制时发生（2026-08-19） | 2026-08-19 |
| `docs/field/FIELD_MEASUREMENT_PLAN_2026-08-18.md` | 现场实测执行计划：D 阶段（2026-08-18 下午） | 2026-08-18 |
| `docs/field/RASPBERRY_PI_DEPLOYMENT_LOG_2026-08-10.md` | RoboGame2026 树莓派与真车部署日志 | 2026-09-18 |
| `docs/field/RASPBERRY_PI_DEPLOYMENT_LOG_2026-08-18.md` | 树莓派部署与真车联调记录（2026-08-18） | 2026-09-18 |
| `docs/field/RASPBERRY_PI_REAL_CAR_AGENT_HANDOFF_2026-08-12.md` | RoboGame2026 树莓派与真车部署 Agent 交接 | 2026-09-17 |
| `docs/field/REAL_CAR_HARDWARE_PRELIMINARY_INVENTORY_2026-08-09.md` | RoboGame2026 真车硬件初步盘点（照片观察版） | 2026-08-13 |
| `docs/field/单相机模拟双相机性能测试_2026-08-15.md` | 单相机模拟双相机性能测试 | 2026-08-17 |
| `docs/team/Claude建议_相机方案参考_2026-08-18.md` | Claude 建议：相机方案与搭建验证（参考记录，非团队决策） | 2026-08-18 |
| `docs/team/给第二位算法同学的任务清单.md` | 给第二位算法同学的任务清单 | 2026-09-18 |
| `docs/team/给电控机械的最小请求单_2026-08-18.md` | 给电控组 / 机械组的最小请求单（2026-08-18） | 2026-09-18 |
| `docs/team/第二位算法同学Git提交说明.md` | 第二位算法同学 Git 提交说明 | 2026-08-08 |
| `docs/team/算法一执行队列_2026-08-18.md` | 算法一执行队列（2026-08-18 起 → 二审 → 自动完赛） | 2026-09-18 |
| `docs/vision/CURRENT_STATUS_HANDOFF_REAL_CAMERA_2026-08-11.md` | RoboGame2026 视觉模块当前状态说明 | 2026-08-13 |
| `docs/vision/GF100_CAMERA_CALIBRATION_CANDIDATE_REPORT_2026-08-12.md` | GF100 相机候选内参标定报告（2026-08-12） | 2026-08-13 |
| `docs/vision/GF100_DATA_COLLECTION_GUIDE_2026-08-11.md` | GF100 真实摄像头台架数据采集指南 | 2026-08-11 |
| `docs/vision/P1_ANGLE_AND_OCCLUSION_ANALYSIS_2026-08-12.md` | P1 侧转角度与遮挡分析（2026-08-12） | 2026-08-13 |
| `docs/vision/VISION_AGENT_HANDOFF_2026-08-12.md` | RoboGame2026 视觉模块 Agent 交接（2026-08-12） | 2026-08-13 |
| `docs/vision/VISION_FIELD_TASKS_2026-08-12.md` | RoboGame2026 视觉现场任务清单（2026-08-12） | 2026-08-13 |

## archived（5 份）—— 已确认过期：**不要照做**。正文必须有「> ⚠️ 已过期」横幅并写明替代文档。

| 文档 | 标题 | 文件修改时间（本机） |
|---|---|---|
| `docs/PLAN_2026-08-13_TO_18.md` | RoboGame2026 2026-08-13 至 08-18 执行计划（v2 快进版） | 2026-09-18 |
| `docs/field/FIELD_DAY1_EXECUTION_ORDER.md` | 现场第一天执行清单 | 2026-09-18 |
| `docs/field/_archived/FREEZE_TABLE.md` | 机械与电控现场冻结表 | 2026-09-18 |
| `docs/team/SPRINT_BOARD.md` | Two-person sprint board: 2026-07-18 to 2026-08-17 | 2026-09-18 |
| `docs/team/给第二位算法同学的任务_巡线模块.md` | 【任务：巡线偏差计算与纠偏控制器（robogame_core 纯算法模块）】 | 2026-09-18 |

## teaching（8 份）—— 教学材料，面向学习，不参与现场执行。

| 文档 | 标题 | 文件修改时间（本机） |
|---|---|---|
| `docs/LEARNING_LOG.md` | RoboGame2026 开发学习记录 | 2026-09-18 |
| `docs/guides/BEGINNER_PROJECT_LEARNING_GUIDE.md` | 从少量编程基础到机器人项目开发：RoboGame2026 学习总结 | 2026-08-05 |
| `docs/vision/TEACHING_INCREMENTAL_DEVELOPMENT_AGREEMENT.md` | RoboGame2026 教学式增量开发约定（供 Agent 交接） | 2026-08-13 |
| `lessons/AGENT_BRIEFING_2026-08-13.md` | 给明天辅助学习的 Agent 的说明 | 2026-08-13 |
| `lessons/HARDWARE_COMMS_LEARNING_ROADMAP.md` | 从算法到硬件：通信与嵌入式知识学习路线 | 2026-09-18 |
| `lessons/LOCAL_LESSONS_README.md` | RoboGame 九模块从零探索 | 2026-09-17 |
| `lessons/01_interfaces_core/README.md` | 第 1 课：先认识“共同语言”和纯逻辑 | 2026-09-17 |
| `lessons/02_cube_perception/README.md` | 第 2 课：让机器人先“看见”方块 | 2026-09-17 |

> 最后一列是**本机文件的修改时间，不是 git 提交时间**：clone / checkout 之后它会统一变成
> checkout 时间，所以只能当粗略参考。要看某个文件真实的最后改动，用 `git log -1 -- <路径>`。

## 不在版本库里的文档与目录（含 `.gitignore` 排除的）

clone 到树莓派或别人的机器上时，**下面这些文件不会出现**。
所以：不要在任何入库文档里把它们当成「可点开的链接」。

| 位置 | 类型 | 文件数 | 示例 | `.gitignore` 依据 | 为什么不入库 |
|---|---|---|---|---|---|
| `docs/*.docx` | *.docx | 4 | RoboGame2026_二审人话版实机任务清单_2026-08-15.docx、RoboGame2026_二审前执行计划_可打印版.docx、RoboGame2026_二审算法缺口与执行清单_2026-08-15.docx | `*.docx` | 由 tools/build_*.py 生成的可打印版，字节大且逐次重生成 |
| `docs/calibration/checkerboard_A4_20mm_9x6.pdf` | 文件 | 1 | checkerboard_A4_20mm_9x6.pdf | `*.pdf` | 由同目录 svg 生成，需要时重生成 |
| `tmp` | 目录 | 11137 | _commit_code.txt、_commit_docs.txt、_hosttmp/stm32_host_w598uc4a/line_telemetry_test.exe | `tmp/` | 临时脚本与中间产物 |
| `outputs` | 目录 | 87 | _review_requirements_extract.txt、camera_acceptance_20260803/annotated.mp4、camera_acceptance_20260803/detections.jsonl | `outputs/` | 生成的可打印文档 |
| `results` | 目录 | 82 | gf100_calibration_audit_20260811/purple50cm.jpg、gf100_calibration_audit_20260811/purple70cm.jpg、gf100_calibration_audit_20260811/purple90cm.jpg | `results/` | 视觉原始视频/图片与报告（体积大，必须另行备份） |
| `_dialogue_doc_work` | 目录 | 5 | build_transcript.py、export_word_pdf.ps1、md_to_html.py | `_dialogue_doc_work/` | 对话归档工程的中间文件 |
| `_inspect_four_motor` | 目录 | 31 | Four_Motor_PID_Test/Core/Inc/boot_id.h、Four_Motor_PID_Test/Core/Inc/chassis.h、Four_Motor_PID_Test/Core/Inc/chassis_control.h | `_inspect_four_motor/` | 只读解包的厂商固件工程 |
| `Four_Motor_PID_Test_1` | 目录 | 1337 | Four_Motor_PID_Test/.mxproject、Four_Motor_PID_Test/backup_uart7_line_sensor_20260916/line_sensor.c、Four_Motor_PID_Test/backup_uart7_line_sensor_20260916/line_sensor.h | `Four_Motor_PID_Test_1/` | 厂商 Keil 工程（含 CMSIS/HAL 与编译产物） |
| `（仓库根）*.pdf` | 外部素材 | 1 | RoboGame2026 竞技组规则手册2_1 (1).pdf | `*.pdf` | 组委会发布的规则手册/评分细则（外部素材，不随仓库分发） |
| `（仓库根）*.docx` | 外部素材 | 3 | STM32与树莓派机械臂开发_完整对话记录.docx、STM32与树莓派机械臂开发_完整对话记录_打印版.docx、二审评分细则（竞技组）.docx | `*.docx` | 外部对话记录/第三方生成的文档（*.docx 被 .gitignore 排除） |
| `（仓库根）*.zip` | 外部素材 | 1 | Four_Motor_PID_Test_1.zip | `*.zip` | 打包快照 |
| `（仓库根）*.pack` | 外部素材 | 2 | Keil.STM32F4xx_DFP.2.17.1.pack、Keil.STM32F4xx_DFP.3.1.1.pack | `*.pack` | Keil 厂商器件支持包，单个近 300MB |
