# RoboGame2026 待办任务与已知问题

用途：持续维护“接下来做什么”和“已经发现但尚未解决什么”。这是项目推进清单，不是学习教程。

维护约定：

- 任务完成后标记为 `[x]`，不要直接删除，保留简短证据；
- 新问题记录复现条件、影响和下一次验证方式；
- `P0` 表示阻断真实闭环，`P1` 表示近期必须完成，`P2` 表示体验或维护改进；
- 一次只选择一个小任务进入开发，避免同时修改过多模块；
- 生成视频、JSONL 和 HTML 默认位于 `results/`，不提交 Git，重要数据需要另行备份。

最后更新：2026-08-17

## 当前状态摘要

### 已经远程完成

- [x] 单方块模拟抓取、放置、撤退、稳定观察和人为取消闭环。
- [x] mock/field 配置分离、配置一致性检查和无硬件失败安全 smoke。
- [x] 串口外层帧、CRC、流式恢复和 0x10/0x11/0x12 路由骨架。
- [x] 机构单动作验收工具、故障注入卡片和现场第一天执行清单。
- [x] 视觉新视频处理、时间段标注、取消、恢复及 HTML 报告链路。
- [x] `motion_control` 限速、异常时间步、停车顺序和机构故障传播测试。
- [x] Windows 全项目 185 项测试通过；Ubuntu 提交 `347e020` 构建 9 包并输出 `CONFIG PASS`。
- [x] `P0` `/cmd_vel` 可信状态门控修复：握手由未解码 0x12 信封完成后不再解锁非零速度。证据：2 AST 回归测试 + 6 行为测试，Ubuntu 239 测试全过，9 包构建通过。SHA `e50529c`。

### 仍可远程完成，但不阻断转现场

- [ ] `P1` 关闭并重新启动同一个视觉 `--workspace`，人工确认视频、JSONL 和时间段自动恢复。
- [ ] `P1` 对真实视频执行一次中途取消，人工确认页面显示 `cancelled` 且无半成品。
- [ ] `P1` 制定 `results/` 命名、索引和团队外部备份规则。
- [ ] `P2` 等电控/机械提供真实载荷样例后，先写固定样例解码测试，再实现适配器。

### 等待电控或机械确认

- [x] `P0` ODOM、IMU、STATUS及机构消息的V1逐字节协议由团队共同制定，见 `docs/field/STM32_SERIAL_PROTOCOL_V1.md`；含固定十六进制测试向量，上位机编解码测试已覆盖。真实STM32抓包仍需在固件实现后验收。
- [x] `P0` 底盘限幅（2026-08-18 电控确认今天可改）：`chassis.h` 三宏改非零（建议 0.3/0.3/1.0），`robot.yaml` 限速已同步 0.3/0.3/1.0（必须 ≤ 固件限幅，超限触发协议故障锁存非削顶）；使能条件清单见 `docs/field/CHASSIS_ENABLE_CHAIN_2026-08-18.md`（6 条：PB2/HELLO/看门狗/无故障/遥控离线/限幅非零）。
- [ ] `P0` 机械臂 RPi 控制通道（2026-08-18 电控确认是**新实现**非「开门」）：仓库无 arm.c（在电控本地 `D:/Codex-work/Four_Motor_PID_Test`）；算法组已给 0x20 payload 定义草案 `docs/field/MECHANISM_0x20_INTERFACE_ALIGNMENT_2026-08-18.md`（建议 ARM_SET(6)+归一化角度）；**待电控回 5 个确认问题**（arm.c 接口/编号/仲裁/授权/爪子）后实现。
- [ ] `P0` 冻结 `GRAB/LIFT/RELEASE/STOP` 的消息类型、载荷、完成条件、错误码与硬件取消方式。
- [ ] `P0` 明确 RETREAT 由 MCU、运动控制还是任务管理负责。

### 必须现场完成

- [ ] `P0` 验证急停、失联停车、方向、限速和真实状态可信度。
- [ ] `P0` 单动作空载/带载验收，确认 STOP/CANCEL 确实停止执行器。
- [ ] `P0` 固定相机安装 + 车载 ROI（橙色/紫色/空场景/遮挡数据已离线采集，见「视觉真实相机与标定进展」）。
- [ ] `P0` 完成并重复验证第一个单方块物理闭环。

### 巡线归属与待办（2026-08-17）

结论：巡线 = 上位机决策与控制，下位机只做八路灰度采集上送。归属细节见 `docs/team/两人算法最终分工.md`「巡线分工」。

- [ ] `P1` 第二位算法同学：与电控确认八路巡线模块是否装车、接口电平、ADC 通道（当前 `HAL_ADC_MODULE_ENABLED` 被注释禁用，见 STM32 工程 `stm32f4xx_hal_conf.h`）。
- [ ] `P1` 第二位算法同学：实现 `robogame_core/line_follow.py` 纯算法模块（八路原始值 → 横向偏差 + 纠偏输出 + `ON_LINE/LEFT/RIGHT/LOST` 状态机）与 `tests/test_line_follow.py` 合成数据单元测试，参考 `navigation.py` 风格。
- [ ] `P1` 算法一：把巡线作为「路段类型」接入 `motion_control` / `mission_manager` 路线选择（路段链、模式切换、与路点/视觉对准仲裁）。
- [ ] `P1` 算法一 + 电控：冻结 V1「巡线遥测」字段字节布局并实现上送；冻结前不猜测载荷。
- [ ] `P2` 真车贴线联调：低速贴线、出线恢复、交叉口行为验收。

8 月 13—18 日每日安排见 `PLAN_2026-08-13_TO_18.md`，现场资料集中在 `field/`。

## 2026-08-18 执行队列阶段 A 完成记录

依据：`docs/team/算法一执行队列_2026-08-18.md`（阶段 A–H）。每项完成后全量回归（Windows 基线 358→371 全过，0 失败）。

### A0 忠实假固件（测试基建）

- [x] `A0.1` 忠实假固件：新增 `mock_communication_ok()`（`hardware_readiness.py`，上电窗口判定）+ `robot_bridge` 参数 `mock_comm_ok_after_s`（默认 0.0=旧宽容行为，兼容基线）/`mock_boot_id`；mock 分支走忠实时序，窗口内握手 HANDSHAKING（cmd_vel 被拒）窗口后转 READY；`status.boot_id` 来自参数。证据：`tests/test_mock_boot_sequence.py` 13 项（9 纯函数 + 4 AST 结构断言），全量 371 OK，SHA 待提交。**真正杀 P0-1 在 A2**（`WAIT_FOR_COMMUNICATION` + 超时），A0.1 是其行为测试的基建。
- [x] `A0.2` launch 图完整性测试：新建 `tools/launch_graph.py`（纯 AST 静态分析，零 ROS 依赖）+ `tests/test_launch_graph.py` 11 项。断言 mock_demo/single_cube/field_no_hardware_smoke/manipulator_mock_smoke 图完整；`hardware.launch.py` 缺相机话题**显式登记** `{/camera/image_raw, /camera/camera_info}`（P0-2），修复后测试红→强制更新登记。全量 382 OK。**真正修 P0-2 在 B1**（`camera.launch.py` + `hardware.launch.py` include）。
- [x] `A0.7` 假固件限幅拒绝：`hardware_readiness.py` 新增 `mock_velocity_limits_ready()`（限幅未就绪拒绝非零、零速永远允许，对应真实 `enable=1` 拒绝）；`robot_bridge` 新参数 `mock_velocity_limits_ready`（默认 True 兼容）+ mock 分支限幅门控。测试 8 项（纯函数 5 + AST 3），`test_mock_boot_sequence.py` 共 21 项，全量 468 OK，Ubuntu 468 OK + 构建 0。与电控确认对齐：超限幅触发协议故障锁存，`robot.yaml` 限速已同步 0.3/0.3/1.0。
- [x] `A3` 视觉分辨率与焦距统一（P0-3）：工作分辨率 640×480，焦距线性缩放 **1275.0**（2550×640/1280）；`robot_field.yaml` 2550→1275；`hardware.launch.py` cube_perception 改 `parameters=[common, field]`（原漏传 field 实机用 700.0）；新建 `vision_gf100_640x480_bench.json`；测试更新焦距一致性 + 新增「field 层视觉参数覆盖生效」回归。全量 383 OK。`min_area_px`/`min_side_px` 待 B4 现场重调。
- [x] `A4` P0-4/5/6 合并改动（建筑搭建失败路径）：field 模式 PLACE 在 RELEASE+验证通过后直接成功返回（不再调机构式 RETREAT，撤退上移 mission 级 `/motion/goal`）；`robot_bridge` retreat_complete 真实模式恒 False（mock 证据不泄漏）；`MissionResult` 增 `INCONCLUSIVE` + 纯函数 `classify_action_result()`（mission.py）集中分类，mission_manager 对 INCONCLUSIVE 不判死进入 VERIFY_BUILD。证据：`tests/test_mission_classify.py` 13 项，全量 396 OK。
- [x] `A5` P2-5 清理：删除 `robot.yaml` robot_bridge 段重复 `max_mcu_sample_gap_ms`；`validate_mcu_tick` 重复导入核实仅一处无需改。新增 `tests/test_config_cleanliness.py`（按节点段扫描全部 bringup yaml 无重复键 + 导入唯一性）。全量 399 OK。
- [x] `A6` 全量回归收尾：Windows 399 OK；Ubuntu 24.04 + ROS Jazzy 全量 399 OK + `CONFIG PASS`（errors=0, warnings=1 已知漂移）+ `colcon build` 9 包成功（21.7s）。A0–A5 改动全量同步 Ubuntu。阶段 A 完成。
- [x] `B1` 相机驱动 launch：新建 `camera.launch.py`（usb_cam 640×480@30fps MJPG + 静态 CameraInfo 焦距 1275.0）+ `gf100_camera_640x480.yaml`；`hardware.launch.py` include 挂入。扩展 `tools/launch_graph.py`（递归 include + 外部包话题契约 + 节点级 remap），**P0-2 缺口闭合**（`KNOWN_HARDWARE_GAPS` 置空）。全量 400 OK，Ubuntu launch 图 12/12 + 构建 0。B2/B3/B4 为现场依赖。
- [x] `C1` 路段链模型（路径层底座）：新建 `robogame_core/route_segment.py`（SegmentKind / RouteSegment / RouteChain：段推进、限速、降级、复位），与 GoToPoseController 兼容。`tests/test_route_segment.py` 16 项，全量 416 OK，Ubuntu 16/16 + 构建 0。C2/C3/C4 将各实现段类型控制逻辑接入。
- [x] `C2` AprilTag 识别 + PnP：新建 `robogame_core/apriltag_pose.py`（detect_tags / estimate_tag_pose / build_observation 异常拒绝 / detect_and_pose）。合成图单测 14 项（缩放/透视/模糊/遮挡/位姿/拒绝），全量 430 OK，Ubuntu 430 OK + 构建 0。⚠️ 仓库样例 tag_01~06.png 检测不到标准 AprilTag（疑数字牌）——检测器按 AprilTag 实现，现场确认样式后可换模板识别，PnP 不变。localization 绝对矫正接入为后续项。
- [x] `C3` 斜坡/高台：新建 `robogame_core/ramp_control.py`（RampProfile / RampController / SlipDecision：坡道限速、打滑检测期望 vs 实测、卡住停车、下坡防冲）。`tests/test_ramp_control.py` 16 项合成数据，全量 446 OK，Ubuntu 446 OK + 构建 0。与 C1 RouteChain 限速同源。IMU pitch 视距补偿为现场依赖后续项。
- [x] `C6` P2 稳健性（P2-1 + P2-2）：`navigation.py` 新增 `pose_in_own_half()`（本方半场 + 边界 + 有限值，规则 3.2.1 S4 越线拒绝）；新建 `robogame_core/cmd_vel_arbiter.py`（/cmd_vel 多来源仲裁：急停>授权者>其他忽略>过期零速，来源白名单）。`tests/test_cmd_vel_arbiter.py` 14 项，全量 460 OK，Ubuntu 460 OK + 构建 0。P2-4 层高上限待机械确认。

## 2026-08-17 第三轮复审修复清单（方案已出，代码未动）

依据：第三轮复审报告（344 项测试基线）。详细修复方案（根因/行号/验证/依赖）见 `docs/RoboGame2026_第三轮复审修复方案_2026-08-17.md`。以下为可执行清单，代码改动待确认后逐项实施。

> **执行顺序、二审录像与完赛清单见 `docs/team/算法一执行队列_2026-08-18.md`**（阶段 A–H，含评分项对齐与待拍板决策）。
> 2026-08-18 修正两处过时假设：视觉工作分辨率 1280×720→**640×480**（焦距 2550→**1275**）；标签为 **AprilTag Tag36h11** 而非 ArUco。

### P0：field 模式必然失败（6 项）

- [ ] `P0` 启动竞态：`mission.py:80-81` 在 SELF_CHECK 就对 `communication_ok=False` 直接 fail，而 `robot_bridge` 上电 `_communication_ok=False`（`node.py:199/224`），首条 STATUS 必带 False → 立即 FAILED。方案：新增 `WAIT_FOR_COMMUNICATION` 前置状态（或 SELF_CHECK 容忍未就绪）+ 启动等待超时参数，运行时心跳丢失语义不变。
- [ ] `P0` 无相机节点：`hardware.launch.py` 无 usb_cam/v4l2_camera，`/camera/image_raw` 无人发布 → PICK_* 停在 WAITING_TARGET 直到 12s 超时。方案：独立 `camera.launch.py`（**640×480 @ 30fps MJPG，usb_cam**）+ CameraInfo；驱动安装为现场依赖。
- [ ] `P0` cube_perception 漏传 field 配置 + 焦距值过时：`hardware.launch.py:32` 只传 `common`，实机用 `robot.yaml` 的 700.0。方案：改为 `parameters=[common, field]`，且 `fallback_focal_px` 应为 **1275.0 而非 2550.0**。
  - 依据：08-15 性能测试（`docs/field/单相机模拟双相机性能测试_2026-08-15.md`）证明树莓派 4B 在 1280×720 仅 ~13fps、CPU 270%，工作分辨率定 **640×480**；焦距按分辨率线性缩放 `2550×(640/1280)=1275`。**内参无需重标**（fx≈2526.98，RMS 0.82px 依然有效），这是纯数学换算。
  - 连带：`min_area_px`（400）/`min_side_px`（12）为像素量纲，分辨率减半后方块面积变 1/4，需现场重调。
- [ ] `P0` 真实模式不支持 RETREAT：`manipulator_client/node.py:344-351` 必经 `/chassis/retreat`，真实分支（`robot_bridge/node.py:524-535`）只映射 GRAB/RELEASE/HOME，返回 error_code=9。方案（推荐 A）：field 模式 PLACE 在 RELEASE+验证通过后成功返回，撤退上移 mission 级 `RETREAT → /motion/goal` 导航（状态机与 dispatch 已存在），mock 模式保留原流程。
- [ ] `P0` retreat_complete 恒 False：`robot_bridge/node.py:961` 无条件读 mock 状态，`complete_mock_retreat` 只在 mock 分支调用。方案：与上项绑定；真实模式禁止 mock 证据泄漏，retreat 完成证据改由运动链提供。
- [ ] `P0` 稳定性判定必然失败：`placement_evidence_policy: unavailable` + `placement_max_unavailable_gap_s: 0.0` → 观察器必然 INCONCLUSIVE（`manipulator.py:120-148`），而 `INCONCLUSIVE` 不在 `MissionResult`（`models.py:12-20`）→ 被归为 MECHANISM_ERROR。方案：`MissionResult` 增加 `INCONCLUSIVE`，mission_manager 不判死，进入 mission 级 VERIFY_BUILD；保持 unavailable 诚实策略，视觉证据中期接入。

### P1：规则明确要求、零实现（5 项）

- [ ] `P1` 巡线（规则 3.1.8）：`robogame_core/line_follow.py`（八路灰度→横向偏差+纠偏+ON_LINE/LEFT/RIGHT/LOST）+ `tests/test_line_follow.py`；接入路段链（motion_control/mission_manager）；与电控冻结 V1 巡线遥测字节布局（先确认装车/ADC 通道）。
- [ ] `P1` 视觉标签识别（规则 3.1.8）：**已确认为 AprilTag Tag36h11，id 1–6**（证据：`docs/field/视觉标签样例_图3.10.png`；样式无需再现场确认，位置仍需实测）。用 `cv2.aruco` 的 `DICT_APRILTAG_36h11` 或 `apriltag` 库，PnP 解算（15×15cm 已知尺寸 + 已标定内参），接入 localization 做绝对位姿矫正（消里程计漂移）。检测器与融合逻辑**离线可做**（合成图单测）；依赖 P0-2 相机与标签坐标映射表。
  - ⚠️ 几何冲突待决策：标签在墙上 40cm 需平视，方块在地面/高台需俯视，同一相机难兼顾 → 见执行队列「待拍板决策 1」。
- [ ] `P1` 斜坡/高台（规则 3.1.5/3.1.6/3.1.7）：上坡航段建模、IMU pitch 视距/重心补偿、打滑检测、上下坡限速。依赖真实 IMU（当前 imu_valid=false）。
- [x] `P1` 倒塌检测（规则 3.2.2 S4）：计时兜底已并入 A4 完成（`MissionResult.INCONCLUSIVE` 不判死 + mission 级 VERIFY_BUILD 3s 计时，`classify_action_result`）。**视觉证据部分对得分不必要**（3s 稳定由裁判判定、不判死已兜住），真正价值在 G3.7（塔倒→重搭决策），并入 G 阶段；可行性依赖「撤退后能否看到塔」实测（Claude 建议 1.6）。
- [ ] `P1` 实测航点：`robot.yaml:84-87` 占位 waypoint → 现场实测回填 `robot_field.yaml`，补上坡航段。

### P2：稳健性（5 项）

- [ ] `P2` 边界收紧：`robot.yaml:32-35` 覆盖全场 → 本方半场，越线异常处理（规则 3.2.1 S4）。
- [ ] `P2` /cmd_vel 仲裁：motion_control/manipulator_client/mission_manager 三个发布者无仲裁 → 统一出口或职责时段明确。
- [ ] `P2` 任务循环：COMPLETE 永久停止（`mission.py:82-83/137-140`）→ 支持多轮（最高 3 座、上不封顶）。
- [ ] `P2` 层高上限：`place_heights_m: [0.10,0.20,0.30]` + clamp → 与规则/机械确认层数上限。
- [ ] `P2` 清理 `robot.yaml:11-12` 重复 `max_mcu_sample_gap_ms` 键，及 `robot_bridge/node.py` 重复 `validate_mcu_tick` 导入。

## 已完成的远程准备阶段记录

### 第一天：机构接入安全边界与配置审计

- [x] `P0` 增加真实模式失败安全测试：没有成功解码 `0x12 RobotStatus` 时，`communication_ok` 不得仅因收到其他合法帧而变为真。证据：8项硬件准备测试、全项目148项测试通过。
- [x] `P0` 分离模拟与现场配置：公共参数使用 `robot.yaml`，环境参数使用 `robot_mock.yaml`/`robot_field.yaml`；field模式在代码层拒绝 `mock_qualified` 和 `mock_failed`。证据：12项硬件准备测试、全项目152项测试通过。
- [x] `P0` 为抓取和放置建立时间预算表，检查 `action_timeout_s=12.0` 是否能覆盖全部真实步骤。证据：`docs/field/TIME_BUDGET.csv`，结论为 12.0s 最坏情况不足，建议现场先用 18s 调试。
- [x] `P1` 把机械/电控必须回答的问题整理成现场可逐项填写的冻结表：动作、参数、完成证据、错误码、取消、断电行为。证据：`docs/field/FREEZE_TABLE.md`，含十节共 50+ 待填项；2026-08-08 电控组已答复底盘速度命令、里程计、IMU、串口与安全共 20 条，FREEZE_TABLE 已同步更新。

### 第二天：现场验收工具与交接准备

- [x] `P0` 准备单动作验收入口，能够独立调用 `GRAB`、`LIFT`、`RELEASE`、`STOP`，记录请求、耗时、响应和 RobotStatus；在协议未冻结前只连模拟服务。证据：`tools/mechanism_acceptance.py`，435行，支持 `--action`/`--all`/`--repeat`/`--output`。
- [x] `P0` 为状态过期、服务不可用、服务失败、急停和机构故障准备可复现的测试步骤。证据：`docs/field/FAULT_INJECTION_TEST_CARD.md`，含七张卡片，每张有模拟和真车两种触发方式。
- [x] `P1` 在 Ubuntu 完整构建并运行机构模拟 smoke，保存终端证据和提交号。证据：40/40 PASS，Ubuntu 边界测试（不启动 robot_bridge 时 SERVICE_UNAVAILABLE）通过；commit `4265759`。
- [x] `P1` 输出现场第一天执行顺序，禁止一开始运行完整任务。证据：`docs/field/FIELD_DAY1_EXECUTION_ORDER.md`，包含分阶段验收、停止条件和证据记录要求。

两天内不做：猜测 `0x10/0x11/0x12` 或机构命令的字节载荷、大规模改成 ROS2 Action、增加未经机械确认的新动作、调真实三层高度。

## 电控协议确认后新增待办（2026-08-08）

电控组通过 `电控协议确认清单.xlsx` 答复了 20 条问题。分析结论见 `docs/field/FREEZE_TABLE.md`（已更新）和当天对话记录。

- [x] `P0` RobotStatus 消息新增 `calibrating`、`imu_valid`、`boot_id` 三个字段，用于区分 IMU 校准状态和 MCU 复位检测。证据：commit `2ccc0a6`，Ubuntu 验证通过，`ros2 topic echo /robot/status` 正确输出新字段。
- [x] `P0` `robot_bridge` 新增握手状态机：HELLO → 等 ACK → READY，握手完成前不接受运动命令。握手超时 3s，最多 3 次重试。证据：commit `9891799`，Ubuntu 模拟验收 12/12 通过，mock 模式直接 READY 不受影响。
- [x] `P0` `robot_bridge` 检测 `boot_id` 变化：变化时重置定位（里程计归零）并等待重新握手。证据：commit `4300335`，Ubuntu 模拟验收 12/12 PASS。
- [ ] `P1` `robot_bridge` 真实里程计改用 STM32 回传的编码器数据，不再用上位机命令速度积分模拟。
- [x] `P1` `localization` 融合时检查 `imu_valid`：false 或数据过期时降级为纯里程计定位。证据：commit `a93d073`，`_imu_usable()` 同时检查 imu_valid 标志和 imu_stale_s 新鲜度（0.2s）。Ubuntu 验证新订阅 /robot/status 已生效，152 测试全过。
- [x] `P1` `StreamDecoder` 增加 0x10（里程计）、0x11（IMU）、0x12（STATUS）帧解析骨架。证据：commit `8c8fe43`，`_dispatch_frame()` 已可按消息类型路由；实际载荷解析仍等待逐字节布局。
- [x] `P1` 补齐串口外层协议抗异常测试：覆盖逐字节分片、多帧粘包、帧头跨读取、payload 内帧头、CRC 损坏、截断恢复、错误版本、超长声明、序号回绕、长噪声和编码边界；14/14 通过，未猜测 0x10/0x11/0x12 内部字段。
- [x] `P1` Ubuntu 复验 field 无硬件失败安全 smoke：缺失串口时 bridge 保持运行且通信不可信，机构以 2001 拒绝、非零 cmd_vel 被握手门控。证据：commit `e49d088` 在 Ubuntu 构建 9 个包，field 无硬件 smoke 输出 PASS，并正常退出。
- [x] `P0` 修正 STATUS 解析骨架的失败安全边界：在 0x12 载荷尚未完成长度、字段和值域校验时，不更新 `last_decoded_status_rx`，也不把占位 `boot_id=0` 当成真实 MCU 状态。证据：新增静态回归测试，防止占位分支重新写入这两个状态入口；硬件安全测试 13/13、全项目 153/153 通过。
- [ ] `P2` 现场确认 STM32 实际限幅值后回填 `robot.yaml` 注释或参数。
- [x] `P2` `mechanism_acceptance.py` 终端摘要模式：每次动作和最终结果打印一行关键状态（comm/estop/calibrating/imu_valid/mechanism_fault）；状态缺失统一显示 UNKNOWN，完整证据仍写入 CSV。证据：摘要与硬件安全测试 16/16、全项目 156/156 通过。
- [x] `P1` 增加 Windows→Ubuntu 一键干净验收入口：按当前已推送提交创建独立 Ubuntu 仓库，自动执行构建、单元测试、模拟闭环和机构摘要，不覆盖两个历史目录。入口：`tools/sync_accept_ubuntu.cmd`。
- [x] `P1` 复验一键验收首次实跑暴露的启动与退出竞态修复：Ubuntu 提交 `343bdec` 实跑构建 9 包、测试 156/156、模拟闭环 STABLE；四个动作状态完整、CSV 5 行、日志非空、脚本自行显示 ACCEPTANCE PASS，退出后无残留进程。
- [x] `P1` Ubuntu 复验配置一致性检查：覆盖 common/mock/field 分层、正数与时间关系、场地航点、视觉范围、单方块目标及 legacy hardware 漂移。证据：commit `d022743` 在 Ubuntu 构建 9 个包，真实五份 YAML 输出 `CONFIG PASS: errors=0 warnings=0`。
- [x] `P1` 补充 `motion_control` 远程安全边界测试：覆盖速度限幅、反向加减速、零时间步、异常里程计时间、角度跨越 ±π、到达目标归零、参数拒绝，以及所有结束路径先停车。测试发现并修复 `mechanism_fault` 未从 RobotStatus 传入运动安全判断的问题；全项目 185/185 通过。

## 现场第一天建议顺序

```text
三方冻结字段与安全行为
→ 只接通信验证帧/心跳
→ 确认 RobotStatus 与急停
→ 单机构空载 GRAB/RELEASE/LIFT/STOP
→ 单机构带载与失败反馈
→ 验证硬件取消和超时安全状态
→ 低速底盘方向与失联停车
→ 最后才运行单方块联合闭环
```

## 视觉真实相机与标定进展（2026-08-11/12）

来源：`docs/vision/VISION_AGENT_HANDOFF_2026-08-12.md`。这些工作在 `codex/vision-field-readiness` 完成，已通过 `fd568f0` 合并进主线；代码已核实存在于当前分支（`opencv_detector.py` 的 `clipped`/`max_working_distance_m`、GF100 配置）。

- [x] 触边候选拒绝 + 完整候选短边测距：轮廓触图边缘即拒绝；测距改用短边，正对 60 样本距离误差 0.45%（橙）/1.24%（紫），最大单张 <2.2%。
- [x] 可配置最大工作距离 `max_working_distance_m`，GF100 配置启用 1.2m；空场景视频橙色误确认从 176/523 帧降为 0/523 帧。
- [x] GF100 正式标定：37 张棋盘格，RMS 重投影 0.82px，fx≈2526.98 / fy≈2539.21 / cx≈620.62 / cy≈355.66；与候选 `focal_px=2550` 差 0.9%，暂不改（见 ISSUE-014）。
- [x] P1-1 侧转角度分析：短边测距天然鲁棒，无需角度过滤。
- [x] P1-2 内部遮挡分析：10% 安全、30% 距离不可靠、50% 失效；`distance_valid` 字段需求已记录。
- [ ] `P1` P1-3 车载 ROI 与自遮挡区：等机械结构冻结。
- [ ] `P1` P1-4 0.3m/0.4m 近距离：等工作距离确认。
（接口需求单列于下方「视觉→集成接口需求」。）

## 视觉→集成接口需求（P0 待办，源自视觉交接 08-12）

视觉检测/标定已完成，但闭环还缺三个集成侧接口（`VISION_AGENT_HANDOFF_2026-08-12.md` 第 12 节）。这些是解锁「视觉找目标 → manipulator 抓取」的硬依赖。现状已审计（2026-08-13，见 `docs/history/INTEGRATION_HANDOFF_2026-08-13.md`）：视觉节点无任何阶段/目标选择输入。

- [ ] `P0` 视觉阶段 SEARCH / ACQUIRE / VERIFY：集成需能驱动视觉节点进入不同阶段——SEARCH 允许远距观察、只给方向/粗略颜色线索；ACQUIRE 在材料区附近用 1.2m 工作距离建立抓取轨迹；VERIFY 用于抓取/放置/稳定观察证据。已审计：节点无阶段输入，`max_working_distance_m`/ROI 已是参数，阶段即运行时切换这些旋钮。
- [ ] `P0` 多合法同色目标选择约束：材料区会同时出现 10 个橙色 / 3 个紫色合法目标，集成需提供至少一种约束（`desired_color` / `expected_material_zone` / `desired_slot` / `expected_grasp_center` 或明确选择策略），不能只靠最高置信度或离画面中心最近。
- [ ] `P0` `distance_valid` 字段：`DetectionEstimate` 现无法表达「检测到但距离因侧转/遮挡不可信」，只能整候选拒绝。需扩展消息接口：`detected:true + distance_valid:false + distance_invalid_reason(side_rotated|occluded|clipped|out_of_working_range)`。

## 视觉验收模块待办

- [x] 新视频在网页中自动生成 schema v2 检测时间线。证据：提交 `d8ffecb`。
- [x] HEVC 手机视频自动生成 H.264 浏览器预览。证据：提交 `17f6ada`，真实视频转换前后均为 574 帧。
- [x] 增加检测进度、处理取消和半成品清理。证据：提交 `277edf4`，139 项测试通过。
- [x] manifest 保存后可从同一工作目录恢复最后一个数据集。证据：自动恢复测试通过。
- [x] 防止恢复后把 H.264 历史预览再次当作原视频处理；重新检测必须重新提交原始视频。
- [ ] `P1` 收集并标注橙色、紫色、空场景三类真实视频——距离/角度/遮挡样本已采集验证（见「视觉真实相机与标定进展」），但正式手动标注数据集仍未建立。
- [ ] `P1` 补充光照变化数据（不同距离/拍摄角度/部分遮挡数据已采集，见「视觉真实相机与标定进展」）。
- [ ] `P1` 将 Windows 产生的正式验收数据备份到团队共享位置；`results/` 当前只保存在本机且不进入 Git。
- [ ] `P2` 为包含多个数据集的 manifest 增加网页数据集选择菜单。目前自动恢复最后一个数据集。
- [ ] `P2` 评估是否显示 FFmpeg 转码的真实百分比。目前检测阶段显示真实帧进度，转码阶段只显示阶段文字。
- [ ] `P2` 增加实验结果索引，避免依靠文件夹名称寻找历史报告。

## 单方块闭环待办

- [x] ROS2 模拟完成抓取、抬升、释放、撤退和稳定观察四终态。
- [x] 模拟覆盖 `STABLE`、`FAILED`、`INCONCLUSIVE`、`CANCELLED`。
- [ ] `P0` 与机械组确认真实 `GRAB/LIFT/RELEASE/STOP/RETREAT` 的协议、完成条件和错误码。
- [ ] `P0` 接入真实机构服务，验证取消是否能让真实执行器安全停止。
- [ ] `P0` 确认相机安装位置、视野和机械臂/车体遮挡关系。
- [ ] `P0` 使用真实相机定义抓取成功和放置稳定的视觉证据。
- [ ] `P0` 在现场完成第一个单方块物理闭环。
- [ ] `P1` 重复运行并统计成功率、平均耗时和主要失败原因。

## ODOM、IMU与定位链补充待办（2026-08-15）

背景：树莓派软件侧已经用PTY伪串口打通 `V1 ODOM → /wheel_odom → localization → /pose`，并覆盖正向、反向、ODOM过期和MCU `boot_id`变化；IMU也已覆盖有效、`valid=false`和过期路径。以下问题尚未解决，不能把软件链通过扩大为实车定位通过。

- [x] `P0` 为 `/wheel_odom` 和有效 `/imu/data` 填写非零、可配置的协方差；ODOM过期使用极大速度方差，IMU无效或过期保持 `angular_velocity_covariance[0] = -1`。当前数值是明确标注的保守临时值，最终仍需实机标定。证据：Ubuntu配置检查0错误0警告，ODOM/IMU/定位/安全相关回归121项全部通过。
- [x] `P1` 使用V1载荷中的 `mcu_tick_ms`检查ODOM/IMU时间连续性：正常递增和32位回绕放行，重复与倒退拒绝，大间隔拒绝当前帧并重建基准，拒绝帧不刷新数据新鲜时间；阈值 `max_mcu_sample_gap_ms=250` 可配置且必须为正整数。证据：Ubuntu配置0错误0警告，时间/ODOM/IMU/定位/安全相关回归129项全部通过。
- [x] `P0` 增加转向与IMU融合的PTY/ROS端到端测试：同时发送 `ODOM.wz_radps`、`IMU.gyro_z_radps`和 `STATUS.imu_valid`；已验证IMU有效时采用IMU角速度，STATUS无效或IMU测量过期时回退轮式ODOM，正负旋转方向不颠倒，差异过大时 `/rosout` 出现告警且 `/pose` 保持有限值。测试发现并修复定位节点忽略 `angular_velocity_covariance[0] = -1`、把桥接节点重复发布的过期IMU误当可用的问题。证据：Ubuntu PTY/ROS与定位、串口、安全停止相关回归133项全部通过。
- [ ] `P0` 现场补齐真实硬件证据：确认STM32持续发送ODOM/IMU，编码器方向及 `vx/vy/wz`单位正确，IMU轴向和正负号正确，真实断联能停车，树莓派突然掉电时STM32本地看门狗在协议规定的150 ms内触发。必须在执行器断电、底盘架空和落地低速三个阶段逐级验收。

## manipulator_client 软件缺口（2026-08-13 审计新增）

来源：`manipulator_client/README.md` 的「当前流程和缺口」与 `manipulator_client/node.py` 走读。这些缺口此前未在 TODO 单列为可执行待办，本次补录。部分只依赖软件、mock 可验证；部分依赖真实传感器或视觉证据，需等硬件到位。

- [ ] `P1` `SEARCH` 旋转搜索：目标丢失时旋转车体重新找目标，当前直接停车等总超时。
- [ ] `P1` 目标角度 `wz` 控制：对准只输出 `linear.x/y`（`node.py:509-512`），不校正朝向角。
- [ ] `P0` 抓取后双证据验证：`grab_verification_policy` 默认 `service_only`（`node.py:46`），只看服务返回；真实需夹爪限位/电流 + 视觉双重证据，部分依赖机械传感器。
- [ ] `P1` 运输掉块检测：搬运途中方块掉落无检测。
- [ ] `P0` 放置后视觉稳定性验证：真实视觉证据接入前诚实返回 `INCONCLUSIVE`（关联 ISSUE-002 / ISSUE-012）。
- [ ] `P0` 机构取消命令：`_cancel_current_action` 只 `_publish_stop()`，不调用 `/chassis/stop`（关联 ISSUE-017）。
- [ ] `P2` 精细重试：失败后细分重试策略。

## 已知问题与观察项

### ISSUE-001：测试虚拟机偶发 RobotStatus 过期

- 优先级：`P1` 观察项；
- 现象：一次模拟测试出现 `robot status stale or communication unavailable`，随后相同测试正常通过；
- 当前判断：可能是虚拟机调度抖动；
- 当前处理：不放宽 `status_stale_s`，避免降低真机安全敏感度；
- 后续验证：记录出现频率，并区分虚拟机环境与真机通信问题。

### ISSUE-002：相机与机械结构遮挡关系未知

- 优先级：`P0`；
- 影响：撤退前可能看不见已放置方块，也可能方块已经稳定但视觉无法给出连续证据；
- 当前处理：稳定观察持续时间和允许间隔均参数化；
- 所需信息：相机安装高度、俯仰角、水平视野、机械臂运动包络和撤退距离。

### ISSUE-003：本地结果没有远程备份

- 优先级：`P1`；
- 影响：`results/` 不提交 Git，电脑损坏时视频、JSONL、标注和 HTML 可能丢失；
- 当前处理：代码和文档已推送，实验结果仍只在本机；
- 后续方案：制定数据命名、筛选和团队共享备份规则，避免把大量视频直接塞入普通 Git 历史。

### ISSUE-004：Windows/Ubuntu 依赖安装方式不同

- 优先级：`P1`；
- Windows：当前使用 `D:\python.exe`，安装了 OpenCV 和 `imageio-ffmpeg==0.6.0`；
- Ubuntu/ROS2：仍需验证 `python3-opencv`、FFmpeg 预览依赖及安装后配置路径；
- 验收条件：Ubuntu `colcon build` 成功，并能处理同一段测试视频、生成 JSONL 和 HTML。

### ISSUE-005：旧实验目录存在重复文件

- 优先级：`P2`；
- 现象：早期重复点击产生多个 `_2`、`_3`、`_4` 文件；
- 当前处理：未删除，以免误删仍有价值的数据；新代码已禁止运行期间重复点击；
- 后续方案：确认哪些文件需要保留后，再进行一次有清单、有备份的整理。

### ISSUE-006：首份真实报告未通过且标注区间有重叠

- 优先级：`P1`，不阻塞转到其他模块；
- 证据：`results/VID20260806162500_acceptance/vision_batch_report.html` 为 1/6 通过；
- 算法现象：一个橙色区间显示约 90% 检出率但略低于精确阈值，空场景仍有 6.6%～43.9% 意外检测；
- 标注现象：橙色区间结束于 15.548 秒，空场景从 15.516 秒开始，存在约 0.032 秒重叠；另有两个相互重叠的空场景区间；
- 当前结论：验收工具已可用，但不能据此宣布视觉算法达标；
- 后续方案：重新选择无争议区间，再判断需要调整标注、报告显示精度还是视觉参数。

### ISSUE-007：真实串口机构与状态载荷字段待补充

- 优先级：`P0`；
- 最新进展（2026-08-08）：电控组已确认消息类型 ID 和帧结构，但逐字节字段表仍未给。
- 已确认：消息类型（0x01=CMD_VEL, 0x02=HEARTBEAT, 0x10=ODOM, 0x11=IMU, 0x12=STATUS, 0x13=ACK/握手）、字节序（小端）、帧结构（帧头+版本+类型+长度+seq+payload+CRC16-CCITT）、速度命令和反馈通道的频率与单位。
- 仍待电控给出：STATUS（0x12）各字段的偏移/长度/类型、ODOM（0x10）和 IMU（0x11）的逐字节布局，以及机构命令的独立消息类型和载荷定义。
- 协议冲突：已确认 `0x02=HEARTBEAT`，但旧机构文档仍把 `0x02` 写成机构命令；真实机构接入前必须由电控和机械共同确认，代码不得猜测复用。
- 当前保护：`robot_bridge` 在非模拟模式主动拒绝机构服务，错误码为2001。
- 下一步：保留 StreamDecoder 的 0x10/0x11/0x12 路由骨架，但占位分支不得刷新真实状态新鲜度；等电控给出精确布局后再实现完整校验和状态更新。

### ISSUE-008：真实通信健康判据过宽

- 优先级：`P0`；
- 原问题：收到心跳或其他合法帧不代表关键 `RobotStatus` 已成功解码，不能据此发布 `communication_ok=true`；
- 已完成缓解：区分任意帧时间与已解码状态时间；真实状态适配器未完成前保持 `communication_ok=false`；
- 仍未完成：三方冻结并实现 `0x12` 载荷后，只有完整字段和值域校验成功才能更新状态时间戳。

### ISSUE-009：软件 CANCEL 不能保证真实执行器停止

- 优先级：`P0`；
- 现状：活动服务 future 被本地丢弃后，服务端动作仍可能完成；RETREAT 也只有软件流程停止语义；
- 现场必须确认：独立STOP命令、ROS2服务取消还是Action取消，最长停止时间、停止完成证据、无法停止的错误码；
- 验收条件：看到执行器实际停止并收到新状态后，才允许任务接受下一条机构命令。

### ISSUE-010：真实放置流程的总超时预算未冻结

- 优先级：`P0`；
- 最新进展（2026-08-08）：已建立 `docs/field/TIME_BUDGET.csv`，逐项列出 PICK/PLACE 各阶段的 min/max/typical 估计值。
- 分析结论：`action_timeout_s=12.0` 在最坏情况下不够用 —— PICK 最坏 14.3s，PLACE 最坏 12.5s。典型情况下够用（PICK 7.1s，PLACE 6.55s）。
- 建议：现场调试阶段先用 18s，实测真实耗时后再收紧到合理值。
- 现场数据：升降、释放、撤退、状态回传的正常值、P95值和最大安全值。填入 TIME_BUDGET.csv 的 `estimated_real_*` 列替换当前估计值。

### ISSUE-011：RETREAT 的真实责任边界未确定

- 优先级：`P0`；
- 现状：当前表现为 `/chassis/retreat` 的 `ExecuteMechanism` 服务，模拟中瞬间完成；
- 待确认：由MCU执行固定距离、运动控制器执行相对位移，还是任务管理器导航到撤退点；
- 验收条件：明确距离/速度/方向、停止精度、遮挡解除条件、取消方式和 `retreat_complete` 证据来源。

### ISSUE-012：正式配置仍含模拟放置证据

- 优先级：`P0`；
- 原问题：公共配置曾包含 `placement_evidence_policy: mock_qualified`，现场误用可能伪造 `STABLE`；
- 已完成缓解：公共配置移除模拟证据，新增 mock/field 配置；field 使用 `unavailable`，节点拒绝 field 与任何 `mock_*` 证据组合；Ubuntu 已通过 mock 闭环和 field 无硬件失败安全 smoke；
- 仍未完成：真实视觉证据接入前，field 对放置稳定只能诚实返回 `INCONCLUSIVE`。

### ISSUE-013：多候选场景缺少真实ROI和任务目标约束

- 优先级：`P0` 现场视觉边界；
- 已完成缓解（2026-08-10）：时序过滤器不再从多个初始同色候选中按最高置信度猜测目标；已有轨迹附近出现多个候选时也会中断确认并标记 `ambiguous`。相关时序测试 8/8、视觉回归测试 70/70 通过。
- 真实视频证据：橙色背景干扰在 104 帧中全部标记为歧义，错误确认从 102 帧降为 0 帧。
- 补充（2026-08-12）：`max_working_distance_m=1.2` 在时序过滤前拒绝远距离假候选，空场景 523 帧视频的橙色确认从 176 帧降为 0 帧。
- 仍未解决：中央紫色方块因贴近画面上边缘、轮廓长宽比超限而未形成候选，右侧唯一背景紫色方块仍会被确认；时序过滤不能补救检测阶段漏检。
- 下一步：固定最终相机后采集完整构图，确定抓取 ROI；在此之前不猜测正式 ROI，也不放宽全局形状阈值。

### ISSUE-014：GF100像素焦距需要最终车载复验

- 优先级：`P0` 现场距离边界；
- 已完成候选标定（2026-08-11）：GF100 在 1280×720、当前手动对焦状态下，使用 10cm 紫色方块和 50cm、70cm、90cm 三个固定距离样本，得到候选 `focal_px=2550.0`；
- 补充（2026-08-12）：37 张棋盘格正式标定，RMS 重投影 0.82px，fx≈2526.98 / fy≈2539.21 / cx≈620.62 / cy≈355.66；与候选 `focal_px=2550` 差 0.9%，暂不改。
- 已分离入口：通用 `vision_default.json`、演示配置和 `robot.yaml` 保留开发兜底值；GF100 候选值单独保存在 `vision_gf100_1280x720_bench.json`，field 层显式覆盖，并由配置回归测试防止混用；
- 可信边界：该值不是通用相机内参；改变分辨率、镜头、对焦或最终安装姿态后必须重新验证；
- 下一步：在最终车载位置用独立距离样本检查 30cm～100cm 误差，并记录方块倾斜和画面边缘的失败范围。

### ISSUE-015：串口读写缺乏异常保护

- 优先级：`P1`；
- 发现时间：2026-08-12 安全审计；
- 现象：`serial.write()` 和 `serial.read()` 没有 try/except。USB 意外断开时 SerialException 传播到定时器回调，可能导致整个 bridge 功能（里程计、状态发布、握手、命令超时）全部冻结；
- 已完成（2026-08-13）：新增 `_safe_serial_write` / `_safe_serial_read`，捕获 `OSError`（`serial.SerialException` 是其子类，覆盖 USB 拔线），失败时关闭串口、置 None、记日志后继续；`node.py` 三处读写（原 132/214/302 行）改走助手。证据：新增 1 个 AST 回归测试 + 6 个行为测试（假串口），Windows 全量 252 项通过（12 项 rclpy 行为测试按预期 skip）。

### ISSUE-016：命令超时不发零速度帧给 MCU

- 优先级：`P1`；
- 发现时间：2026-08-12 安全审计；
- 现象：`_tick` 超时只将内存 velocity 归零，不向 MCU 发送零速度帧。MCU 继续执行最后一个非零速度直到自身看门狗触发，中间可能有 350ms+ 失控窗口；
- 已完成（2026-08-13）：`_tick` 超时且内存速度非零时调用 `_send_zero_velocity`，在握手 READY + communication_ok + 串口在位时主动发送 `encode_velocity(0,0,0)`，关掉 350ms 失控窗口；只在「动→停」过渡发一次，不刷屏串口。证据：新增 1 个 AST 回归测试 + 3 个行为测试，Windows 全量 256 项通过（15 项 rclpy 行为测试按预期 skip）。


### ISSUE-017：软件 CANCEL 不调用 STOP 服务

- 优先级：`P0`（ISSUE-009 的软件侧）；
- 现象：`manipulator_client/node.py` 的 `_cancel_current_action` 最终只 `_publish_stop()`（发零速度 Twist），从不调用已创建的 `/chassis/stop` 服务；结果字符串也承认 `active {operation} service may still complete`；
- 已完成（2026-08-13，软件侧）：新增 `self.stop` 客户端（`/chassis/stop`），`_cancel_current_action` 顶部调用 `_request_stop()` fire-and-forget 发送 STOP，失败经 `_on_stop_done` 记日志。证据：新增 1 个 AST 回归测试 + 1 个行为测试，Windows 全量 258 项通过（16 项 rclpy 行为测试按预期 skip）。
- 仍未完成：等待 STOP 完成证据、以及真实 STOP 能否物理停止执行器，仍须电控/机械确认（ISSUE-009）。

### ISSUE-018：测试数量文档不一致

- 优先级：`P2`；
- 已完成（2026-08-13）：实测全量 258 项（Windows 16 项 rclpy 行为测试 skip，Ubuntu 上全量运行），`README.md` 已更新为该数。

## 2026-08-13 串口协议与部署准备

- [x] 确认 Windows 能识别 `ST-LINK V2`，Keil 能看到 `ARM CoreSight SW-DP`。这只证明 SWD 调试连接可被识别，不等于业务串口通信已通。
- [x] 确认树莓派识别 USB 转串口模块为 `/dev/ttyUSB0`，TX/RX 回环收到 `b'hello'`。证据范围仅为树莓派↔转串口模块的基础收发。
- [x] 完成 V1 协议文档、上位机载荷编解码、固定测试向量和 `HELLO` 消息类型修正的当前工作区实现。针对测试 **19/19 PASS**。
- [x] `P1` 增加树莓派端 V1 正式协议串口验证入口 `tools/serial_v1_acceptance.py`：支持固定 HELLO 帧回环和真实 `HELLO→ACK` 两种模式；真实硬件结果仍需现场执行后记录。
- [x] `P1` 对当前协议改动运行 Windows 全量回归：**267 项通过，0 失败，16 skip**（skip 为本机无 rclpy 的行为测试）；待整理提交工作区。
- [x] `P1` 已以 `2d9514d` 创建独立树莓派部署目录，保留 `2d70649` 作为回退版本。
- [ ] `P0` 等 STM32 协议固件可用后，执行真实 `HELLO→ACK`、心跳、里程计、IMU 和 STATUS 帧联调。

## 2026-08-14 晚间总集成与真实通信状态

### 已完成

- [x] 总集成上位机 V1安全通信实现并推送：握手、心跳、STATUS/ODOM/IMU解码、STOP、断线清状态和自动重连。
- [x] Ubuntu/VMware：9个ROS2包构建通过，配置0错误0警告，全量 `294 tests OK`，两项无硬件 smoke 通过。
- [x] 吸收 `feature/localization@2f2fd33` 独有导航验收数据；定位代码此前已在总集成分支。
- [x] 树莓派以独立目录部署 `2d9514d5b3ad1d2bab91a292e3c20bae730cacc9`，保留旧目录作为回退。
- [x] 树莓派 ARM64 构建、配置和测试由现场执行并报告通过；配置明确显示 `errors=0 warnings=0`。
- [x] 树莓派识别 STM32 VCP，并为 `rg26` 配置 `dialout` 权限。
- [x] 正式配置改用稳定串口身份：`/dev/serial/by-id/usb-STMicroelectronics_STM32_Virtual_ComPort_307A39653433-if00`。
- [x] 真实 V1：HELLO/ACK通过，STATUS约51Hz，心跳停止后看门狗正确置位，无非法载荷。
- [x] 正式 `robot_bridge` 已将真实 STM32 STATUS 发布到 `/robot/status`；清理后无残留机器人进程。

### 尚未完成：上位机

- [ ] `P0` 增加 `/cmd_vel → robot_bridge → V1 CMD_VEL` ROS→PTY端到端测试，核对 `vx/vy/wz/enable` 和安全门控。
- [ ] `P0` 增加 V1 ODOM/IMU → `/wheel_odom`、`/imu/data` → localization 的ROS端到端测试。
- [x] `P0` 修正 ODOM与IMU协方差/不可用字段语义，避免默认全零被消费者误解为高精度测量；Ubuntu相关回归121项通过，临时方差仍待实机标定替换。
- [ ] `P0` 为每次硬件验收增加 ROS发布者来源和残留进程检查，防止 mock 与 real 同名话题污染证据。
- [ ] `P0` 定义并实现真实 `GRAB/LIFT/RELEASE/RETREAT` 机构协议；当前非mock模式仍主动拒绝，错误码2001。

### 尚未完成：STM32与实车

- [ ] `P0` 在主循环周期调用 `RPI_SendOdom()`，使用四轮实际RPM和 `Chassis_GetBodyVelocity()` 生成真实 `vx/vy/wz`。
- [ ] `P0` IMU未接入前发送明确 `valid=0` 的IMU帧；确认型号后再实现真实驱动、零偏和轴方向。
- [ ] `P0` 增加速度命令可观察证据：最近命令、四轮目标RPM或调试器观察方案。
- [ ] `P0` 执行器断电验证CMD_VEL解析、STOP清零、失联清零和重连不续跑。
- [ ] `P0` 架空完成单轮、四轮、前后、横移、旋转和STOP验证后，才允许落地低速。
- [ ] `P0` 真实夹爪与升降单动作、限位、堵转、STOP和完成状态尚未开始。

### ISSUE-019：mock与real同名发布者污染验收

- 优先级：`P0` 验收可靠性；
- 现象：读取真实 STM32 时曾得到 `detail: mock hardware`、24V、`imu_valid=true`；检查发现残留两个 `robot_bridge` 进程；
- 影响：会把模拟状态误认为真实硬件状态，也可能造成订阅者在两种状态之间跳变；
- 当前处理：清除残留进程后重新验证，真实状态为 boot_id=1、battery=0、imu_valid=false；机械臂 mock smoke 清理后连续3轮通过；
- 后续：硬件测试脚本必须使用可控生命周期，并在开始/结束检查同名节点、发布者和进程。

### ISSUE-020：ODOM/IMU链路两侧均未封口

- 优先级：`P0`；
- 上位机现状：协议解码、ROS发布和失效回退已有实现，但缺ROS端到端测试及完整协方差语义；
- STM32现状：发送函数存在但主循环未调用，真实safe-suite收到 ODOM=0、IMU=0；工程内未发现真实IMU驱动；
- 影响：不能进行可信定位、导航或落地底盘控制验收；
- 下一步：先完成上位机PTY端到端测试，再补STM32周期遥测并重新执行safe-suite。

## 2026-08-15 二审算法缺口复审与执行清单

审查依据：项目根目录《二审评分细则（竞技组）》与《RoboGame2026 竞技组规则手册2.1》，并以当前工作树、当前测试和现有实机证据为准。结论是：软件骨架基本齐全，但真实机构链路、真实定位闭环、建筑稳定证据和连续实机闭环仍未完成，当前不能宣称满足二审算法要求。

### 二审评分项状态

- [ ] `P0` 底盘运动（10分）：完成真实 ODOM、架空方向/STOP、落地低速和启动区到材料区的重复导航验收。
- [ ] `P0` 运动决策（20分）：冻结正式场地坐标和路点，用真实定位驱动任务状态机完成连续自主运行。
- [ ] `P0` 取存操作（20分）：实现真实 `GRAB/LIFT/RELEASE/RETREAT` 协议；当前 field 模式仍以错误码 `2001` 主动拒绝机构动作。
- [ ] `P0` 建筑搭建（20分）：完成真实放置、撤退，并在机器人脱离后提供连续稳定至少3秒的真实证据。
- [ ] `P0` 急停（5分，算法配合项）：验证运行中物理急停能切断底盘、夹爪和升降等全部执行器，并单独拍摄证明。
- [ ] `P1` 附加分（5分）：在主闭环稳定后优化动作连贯性、总耗时和视频表现。
- [ ] 电控布线与形态完整由电控/机械主责；算法仅负责接口标识、相机/传感器安装约束和验收配合。

### P0-1：冻结并实现真实机构闭环

- [x] 冻结 `GRAB/LIFT_ABS/RELEASE/HOME/STOP` 的消息类型、载荷、单位、状态机、幂等重试和固定十六进制样例；见 `docs/树莓派_STM32机械机构通信协议_v1.0.md`。RETREAT 仍归底盘运动责任域。
- [x] 区分“命令帧 ACK”和“动作终态”，冻结 `ACCEPTED/RUNNING/SUCCEEDED/FAILED/CANCELLED/REJECTED`、限位、堵转、过流、超时和错误码语义。
- [ ] 电控/机械填写并实测冻结行程、层高、速度、电流、位置容差、夹持/释放/堵转判据及 STOP/断电保持方式。
- [ ] STM32 实现夹爪/升降底层驱动、非阻塞动作状态机、`0x20/0x21` 处理、命令去重、STOP、心跳中断和急停联动。

#### P0-1A：树莓派机构控制交付清单

##### 协议层

- [ ] 实现 `0x20 MECHANISM_COMMAND` 的 12 字节 payload 编码（`<HBBiI`）和值域检查。
- [ ] 实现 `0x21 MECHANISM_STATUS` 的 10 字节 payload 解码（`<HBBHI`），检查 operation、state 和 error_code。
- [ ] 实现 `0x22 EMERGENCY_STOP` 零载荷高优先级发送，并处理其 ACK。
- [ ] 将通用帧 ACK 与机械动作状态分开匹配，不得将 `ACK_OK`、`ACCEPTED` 或 `RUNNING` 当作动作成功。

##### 机构客户端与会话

- [ ] 实现统一 `MechanismClient`，对外提供 `home/grab/release/lift_abs/stop/emergency_stop`。
- [ ] 实现会话内唯一 `command_id` 分配和 pending 命令表，按 `command_id` 分发 `0x21`。
- [ ] 实现幂等重试：重试时仅更换 `frame_sequence`，`command_id` 及原 payload 必须保持不变。
- [ ] 实现 ACK 100 ms 超时/最多 3 次重试、机械状态 300 ms 静默检测及命令总超时。
- [ ] 实现 `HELLO -> ACK_OK -> READY` 会话门控、50 ms 心跳，在 READY 之前拒绝急停以外的机械命令。
- [ ] 检测串口断开、重连和 STM32 `boot_id` 变化：失败所有 pending 命令，清除旧会话，要求重新 HELLO/HOME，不自动续执。
- [ ] 维护 `DISCONNECTED/HANDSHAKING/READY_UNHOMED/READY/BUSY/FAULT/ESTOP` 本地状态及合法动作门控。

##### ROS 2 与任务流程

- [ ] 在 `robot_bridge` 中将回零、夹爪抓取、夹爪释放、升降高度、取消和软件急停服务映射到真实协议。
- [ ] 只将 `SUCCEEDED` 映射为 ROS 成功；`FAILED/CANCELLED/REJECTED`、ACK/状态超时、断线和 MCU 重启必须返回失败并保留原始错误码。
- [ ] 移除 field 模式对机构服务的错误码 `2001` 占位阻塞，但只在真实协议客户端 READY 后启用机构动作。
- [ ] 任务流程固定为“底盘停稳 -> 机构动作 -> 收到终态 -> 下一步”；任一机构步失败时停止后续动作并请求 STOP。
- [ ] RETREAT 保持由底盘运动实现，不发送机械协议命令；单独冻结距离、速度、停车精度和完成证据。

##### 自动测试与验收

- [ ] 增加命令/状态 payload 长度、小端、有符号参数、CRC 和协议黄金帧测试。
- [ ] 增加 ACK 丢失、状态丢失、重复终态、非法状态回退、operation 不匹配和未知错误码测试。
- [ ] 增加“重试不改 `command_id`”和“相同命令不重复执行”的模拟 STM32 集成测试。
- [ ] 增加串口断开、STM32 重启/`boot_id` 变化、心跳中断、服务取消 -> STOP 和急停优先级测试。
- [ ] 增加 HOME -> GRAB -> LIFT_ABS -> RELEASE -> STOP 的模拟 STM32 端到端测试，证明非成功终态不会驱动后续任务。
- [ ] 在树莓派/Ubuntu 上运行协议、ROS 服务和模拟 STM32 全套测试，保存日志、命令帧、状态帧和测试结果。

- [x] 明确 RETREAT 不属于机械协议，由树莓派任务管理器调用底盘运动实现；距离、速度、方向、停车精度和完成证据仍待现场冻结。
- [ ] 验证 STOP/CANCEL 能中断正在执行的真实机构动作；未收到停止完成证据前不得接受下一条机构命令。
- [ ] 依次完成夹爪空载、升降回零、目标高度、带载抓取、释放、堵转、断线和中途取消测试。

### P0-2：封口真实定位与底盘链路

- [ ] STM32 主循环周期发送真实 ODOM，数据来自四轮实际 RPM/编码器和车体速度解算。
- [ ] IMU 未接入时持续明确报告 `valid=0`；接入后完成型号、轴向、零偏、角速度和协方差标定。
- [ ] 在 Ubuntu/树莓派运行 `/cmd_vel → robot_bridge → V1 CMD_VEL` ROS→PTY端到端测试。
- [ ] 在 Ubuntu/树莓派运行 `V1 ODOM/IMU → /wheel_odom、/imu/data → localization` 端到端测试。
- [ ] 执行器断电核对 `vx/vy/wz/enable`、最近命令和四轮目标 RPM，确保单位、符号和轮序一致。
- [ ] 架空通过单轮、四轮、前后、横移、旋转、STOP、失联和重连不续跑，再允许落地低速。
- [ ] 落地标定轮径、轮距、速度、停车误差和航向误差，替换当前临时协方差。

### P0-3：真实视觉、货物与建筑证据

- [ ] 最终相机安装后复验 GF100 焦距、0.3m～1.0m距离误差、车载 ROI、振动和机械自遮挡区。
- [ ] 修复或规避中央紫色方块触边/形状过滤漏检，以及背景紫色方块被确认的问题。
- [ ] 用任务目标约束多个同色合法候选，至少包含颜色、材料区/槽位和抓取中心，禁止只按最高置信度猜测。
- [ ] 增加 `distance_valid` 及无效原因，表达“检测到但距离因侧转、遮挡或触边不可信”。
- [ ] 抓取后使用夹爪/电流/行程或视觉证据确认方块确实在车上，并能发现运输途中掉块。
- [ ] 放置后确认方块离开夹爪；机器人撤退脱离后，真实观察建筑连续稳定至少3秒。

### P0-4：二审最小真实闭环

- [ ] 先限定为一个橙色方块、一个固定抓取位、一个固定搭建位，不让多方块、多层塔或紫色屋顶抢占P0时间。
- [ ] 连续完成：物理启动→启动区出发→材料区→视觉确认→对准→抓取→持有确认→搭建区→释放→撤退→稳定3秒→停车。
- [ ] 每轮保存代码 SHA、树莓派部署目录、STM32 固件版本、参数快照、串口帧、ROS话题、视频、耗时和失败原因。
- [ ] 每次验收开始与结束检查同名节点、发布者和残留进程，禁止 mock 与 real 混合污染证据。
- [ ] 完整闭环至少连续重复5轮；底盘固定路点建议完成5～10轮后再录二审主视频。
- [ ] 单独拍摄运行中按下物理急停，证明底盘、夹爪和升降全部停止且执行器能量被切断。

### 当前软件基线与交付前清理

- [x] 2026-08-15 使用项目 Python 环境运行全量测试：`333 tests OK`，其中 `39 skipped`、`0 failed`。
- [ ] 在 POSIX ROS 2 环境补跑39项跳过测试中的关键 PTY/ROS 集成用例；Windows全绿不能替代该证据。
- [ ] 清理 `robot.yaml` 中重复的 `max_mcu_sample_gap_ms` 配置。
- [ ] 清理 `robot_bridge/node.py` 中重复的 `validate_mcu_tick` 导入。
- [ ] 对当前未提交工作区完成差异复审、Ubuntu/树莓派构建、配置校验、全量回归和两个无硬件 smoke，再形成部署版本。
- [x] 生成参考文档：`docs/RoboGame2026_二审算法缺口与执行清单_2026-08-15.docx`。

### 当前唯一推荐下一步

先冻结并打通真实 `GRAB/LIFT/RELEASE/RETREAT + 完成状态 + STOP` 协议。该链路未完成时，取存操作与建筑搭建合计40分仍然无法进行真实演示。

## 2026-08-15 晚间树莓派部署、STM32安全联调与源码审查

### 已完成

- [x] 新集成commit `d98b9d3253c72c87922feeab6118dab07c957c15` 以bundle部署到 `/home/rg26/robogame_deploy_d98b9d3253c7`，detached HEAD与完整SHA一致，工作树干净。
- [x] 树莓派 `rosdep check` 通过，ARM64九包构建通过，结果为 `Summary: 9 packages finished [1min 40s]`。
- [x] 配置、九包发现和树莓派全量软件测试通过；现场结果为 `Ran 333 tests in 13.033s`、`OK`。
- [x] 定位首次串口失败不是权限或ROS问题，而是原USB线未让STM32进入USB枚举；更换数据线后识别 `0483:5740`、`/dev/ttyACM0` 和既定 `by-id` 路径。
- [x] 正式 `robot_bridge` 完成真实V1握手与STATUS解码：`communication_ok=true`、`boot_id=1`、约50Hz、`error_code=0`。
- [x] 真实ODOM和IMU话题均约50Hz；ODOM静止样本为零，IMU明确 `imu_valid=false`，未把不可用IMU误记为有效。
- [x] 执行器无动力条件下完成USB拔线失败安全与重插自动重连；恢复后节点仍存活并重新达到约50Hz。
- [x] 完成ROS `/chassis/stop` 到STM32的真实闭环：服务写出零速度+急停成功，STM32回传 `emergency_stop=true`、`error_code=9001`，通信继续正常。
- [x] 安全退出 `robot_bridge`，确认无残留桥接进程且STM32稳定设备路径仍存在。
- [x] 收到并只读审查STM32F427完整工程；确认9001、150ms通信看门狗、本地PB2长按重新授权、重连不清急停、遥控器优先和新HELLO清旧速度等实现。

### ISSUE-021：STM32当前明确禁止树莓派自动运动

- 优先级：`P0`；
- 源码事实：`chassis.h` 中 `CHASSIS_RPI_VX_LIMIT_MPS`、`CHASSIS_RPI_VY_LIMIT_MPS`、`CHASSIS_RPI_WZ_LIMIT_RADPS` 均为 `0.0f`；协议层和底盘控制层在限幅未就绪时拒绝启用的自动速度命令；
- 安全意义：当前固件是“通信/STOP验收版”，不是允许非零 `/cmd_vel` 的运动版；
- 下一步：由机械、电控和算法共同冻结轮径、轴距/轮距、轮序、正方向及首次架空限值；修改固件后重新编译、烧录，并从无动力通信门重新复验；
- 禁止事项：不得为了让车动而临时绕过 `Chassis_RpiLimitsReady()`、`RPI_IsReady()`、`physical_start`、看门狗或急停锁存。

### ISSUE-022：STM32固件缺少可现场读取的版本身份

- 优先级：`P0`，验收可追溯性；
- 现状：收到的源码和现场行为高度一致，但压缩包不能证明当前Flash与源码逐字节相同；
- 风险：上位机commit可固定，STM32固件却可能因重新烧录、不同电脑工程或未保存改动而失去对应关系；
- 下一步：在HELLO/STATUS或独立诊断消息中增加固件版本、协议版本和构建哈希；每轮实车验收同时记录上位机SHA与固件身份。

### 下一阶段安全顺序

- [ ] 电控确认并记录PB2长按重新授权的现场操作、指示状态和停止条件。
- [ ] 形成首个非零自动限幅的固件commit/构建哈希，不直接修改现场二进制而不留版本。
- [ ] 烧录后先复验上电默认未授权、HELLO/STATUS、150ms看门狗、STOP锁存、拔线停车和重连不续跑。
- [ ] 执行器有动力前确认车轮架空、现场急停/断电人员和机械安全空间。
- [ ] 先验证零速度，再做单轮/四轮极低速短脉冲；方向、轮序、编码器任一不符立即STOP并断电。
- [ ] IMU继续按无效处理，直到真实驱动、轴向、零偏和有效位全部验收。

## 后续维护入口

每次开发结束时更新：

1. `LEARNING_LOG.md`：今天理解了哪些工程知识；
2. `TODO_AND_ISSUES.md`：哪些任务完成了、出现了哪些新问题、下一步是什么；
3. 对应模块文档：如果命令、参数或接口发生变化，再更新具体使用说明。
