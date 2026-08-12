# RoboGame2026 视觉模块 Agent 交接（2026-08-12）

## 1. 交接目标

本文供后续 Codex/开发 agent 继续视觉模块工作。请从本文记录的真实工作区状态继续，不要重新猜测 HSV、焦距、ROI 或距离参数，也不要把候选结果描述成真车通过。

本交接不授权总集成、树莓派部署、STM32连接、`robot_bridge` 启动或整车动作。

## 2. 固定基线与工作边界

```text
repository: https://github.com/Kyra25906/robogame2026.git
branch: codex/vision-field-readiness
baseline commit: f6f736ea9c4dce44f49c0b2c6009bcdc0d4aa16b
ROS: ROS 2 Jazzy
vision package: ros2_ws/src/cube_perception/
```

基线包含三个视觉修复 commit（按时间顺序）：

```text
d81d277 feat(vision): reject ambiguous targets and isolate GF100 config
e1388fa fix(vision): reject clipped targets and correct distance sizing
f6f736e feat(vision): add configurable max working distance filter
```

优先允许修改：

- `ros2_ws/src/cube_perception/`
- 视觉相关测试
- `docs/vision/`

涉及以下内容时只列接口需求，不直接覆盖集成版本：

- `robot.yaml`
- launch
- 消息接口
- `localization`
- `mission_manager`

不要在树莓派上修改代码，不连接 STM32，不启动 `robot_bridge`，不运行整车 launch。

## 3. 当前交付状态（已推送）

```text
branch: codex/vision-field-readiness
HEAD: f6f736ea9c4dce44f49c0b2c6009bcdc0d4aa16b
remote: origin/codex/vision-field-readiness (已同步)

已追踪修改（三个 commit 均已推送，工作区无已追踪文件的修改）：
M ros2_ws/src/cube_perception/cube_perception/opencv_detector.py (触边+短边+距离过滤)
M ros2_ws/src/cube_perception/cube_perception/node.py (+max_working_distance_m 参数)
M tests/test_opencv_detector.py (+6 tests)

新增未跟踪文档：
?? docs/vision/TEACHING_INCREMENTAL_DEVELOPMENT_AGREEMENT.md
?? docs/vision/GF100_CAMERA_CALIBRATION_CANDIDATE_REPORT_2026-08-12.md
?? docs/vision/VISION_FIELD_TASKS_2026-08-12.md
?? docs/vision/VISION_AGENT_HANDOFF_2026-08-12.md
```

不要删除或覆盖其他未跟踪 DOCX、PDF、视频、`results/`、渲染目录和个人文件。

## 4. 本次源码修改

### 4.1 触边候选拒绝

文件：

```text
ros2_ws/src/cube_perception/cube_perception/opencv_detector.py
```

已经实现：

- 对每个颜色轮廓调用 `cv2.boundingRect`；
- 轮廓触及图像左、上、右或下边框时拒绝该候选；
- `last_debug[color]` 增加 `clipped` 统计；
- 靠近边框但未触边的完整候选继续允许。

这是候选级拒绝，不是整帧同色屏蔽。若一张图中有一个触边橙色轮廓和另一个不触边橙色轮廓，只拒绝触边的那个。

### 4.2 完整候选使用短边测距

已经实现：

- 原来的 `Candidate(width_px=long_side, height_px=short_side)` 保持不变，用于维持原长宽比置信度语义；
- `estimate_from_candidate` 返回后，用 `short_side` 重新计算 `distance_m`；
- 根据新距离同步重新计算 `lateral_m`；
- `confidence`、`yaw_error_rad`、颜色和中心位置不变。

不要把 `Candidate` 的宽高直接交换，否则会同时改变 `candidate_confidence` 的长宽比评分，本轮修改范围会被扩大。

### 4.3 新增测试

文件：

```text
tests/test_opencv_detector.py
```

新增：

- `test_object_clipped_by_image_border_is_rejected`
- `test_object_near_image_border_is_still_accepted`
- `test_complete_candidate_uses_short_side_for_distance`

## 5. 已完成测试

使用 `D:\python.exe`（含 OpenCV 4.13.0）：

```powershell
$env:PYTHONPATH = "$repo\ros2_ws\src\cube_perception;$repo\ros2_ws\src\robogame_core"
```

检测器测试：

```powershell
& "D:\python.exe" -B -m unittest tests.test_opencv_detector -v
```

结果：

```text
10 tests, OK
```

完整回归：

```powershell
& "D:\python.exe" -B -m unittest discover -s tests -v
```

结果：

```text
195 tests, OK
```

### 5.1 空场景端到端验证

```powershell
& "D:\python.exe" -B -m cube_perception.standalone `
  --source "C:\Users\dahli\Pictures\Camera Roll\environment\WIN_20260812_17_46_24_Pro.mp4" `
  --config results/workspace/gf100_environment_empty_20260812/vision_gf100_maxdist_1.2.json `
  --headless `
  --jsonl results/workspace/gf100_environment_empty_20260812/maxdist_test/detections.jsonl
```

结果：

```text
523 帧, 46.0 FPS
0 帧出现橙色确认目标（修复前：176/523，33.65%）
所有橙色轮廓被 far 拒绝（5-6 个/帧）
```

## 6. GF100 候选标定

相机模式：

```text
GF100
1280 x 720
```

棋盘格：

```text
10 x 7 方格
9 x 6 内角点
实际单格边长 16.0 mm
固定在平整刚性板上
```

数据目录（仓库外）：

```text
C:\Users\dahli\Pictures\Camera Roll\fixed_grid
C:\Users\dahli\Pictures\Camera Roll\grid_sides
```

候选结果：

```text
采用 37 张，诊断排除 18 张
RMS reprojection error: 0.821035 px

fx = 2526.983969 px
fy = 2539.211596 px
cx =  620.623269 px
cy =  355.655867 px

k1 = -0.05080662
k2 = -0.29252590
p1 =  0.0
p2 =  0.0
k3 =  0.0
```

详细报告：

```text
docs/vision/GF100_CAMERA_CALIBRATION_CANDIDATE_REPORT_2026-08-12.md
```

当前 `focal_px=2550.0` 与候选 `fx` 相差约 `0.9%`，暂时无需修改。该结果不是正式实验室标定，也不是装车外参。

## 7. 正面方块距离验证

### 橙色

数据：

```text
C:\Users\dahli\Pictures\Camera Roll\0811\0812
orange_0.5m .. orange_1m
每组 5 张，共 30 张
方块正面宽度：100 mm
```

短边测距结果：

```text
检测：30/30
平均绝对误差：0.452%
平均偏差：-0.197%
最大单张绝对误差：1.361%
```

### 紫色

数据：

```text
C:\Users\dahli\Pictures\Camera Roll\0811
purple_0.5m .. purple_1m
每组 5 张，共 30 张
```

短边测距结果：

```text
检测：30/30
平均绝对误差：1.235%
平均偏差：-1.234%
最大单张绝对误差：2.145%
```

限制：比赛规则允许正式方块边长存在 `±5%` 误差，因此当前自有精确 100 mm 方块得到的约 1% 算法误差，不能等价为所有比赛方块的实际距离误差。

## 8. 困难样本结论

数据：

```text
C:\Users\dahli\Pictures\Camera Roll\0811\orange_sides
C:\Users\dahli\Pictures\Camera Roll\0811\purple_sides
```

已证明：

- 旧逻辑会接受触边残缺轮廓并输出距离；
- 置信度不能自动证明轮廓完整；
- 新 `clipped` 判断能拒绝具体触边候选；
- 一张图片仍可能包含其他不触边的同色误检轮廓。

尚未证明：

- 内部遮挡的稳定拒绝边界；
- 明显侧转的最大允许角度；
- 可用单一长宽比阈值区分正面与侧面。

不要根据现有混合 `sides` 样本猜测 `aspect_ratio=1.2` 等阈值。应补采固定 `0.7 m`、`0/15/30/45°` 左右侧转样本，以及独立的 `10/30/50%` 内部遮挡样本。

## 9. 环境和未完成小车负样本

原始数据：

```text
C:\Users\dahli\Pictures\Camera Roll\environment
```

关键负视频：

```text
WIN_20260812_17_46_24_Pro.mp4
1280 x 720
523 帧
约 17.43 秒
expected: absent
```

当前修改后检测结果：

```text
橙色原始单帧候选：472/523 帧（90.2%）
橙色时序确认：176/523 帧（33.652%）
紫色时序确认：0
```

误检来源：未完成小车上的橙色接线端子、线缆和小型橙色零件。

该问题是视觉部署阻塞项：空场景仍能建立稳定橙色错误轨迹。

基线产物（Git 忽略）：

```text
results/workspace/gf100_environment_empty_20260812/detections.jsonl
results/workspace/gf100_environment_empty_20260812/manifest.json
results/workspace/gf100_environment_empty_20260812/report.html
results/workspace/gf100_environment_empty_20260812/distance_filter_analysis.md
```

HTML报告按预期返回 `FAIL`，这是需要保留的修改前失败证据。

## 10. 工作距离过滤离线结论

尚未实现代码或正式配置，只完成了离线分析。

```text
60 张真实正面样本输出距离：0.490..1.000 m
负视频橙色原始候选距离：3.359..15.000 m
```

候选最大距离 `1.2 m`：

```text
橙色正面保留：30/30
紫色正面保留：30/30
负视频仍有橙色原始候选的帧：0/523
```

重要：过滤必须发生在候选进入时序过滤器之前。只过滤最终输出不能避免远距离假候选制造歧义或错误轨迹。

`1.2 m` 只是抓取阶段候选值，不是相机最大检测距离。正式实现前必须确认任务是否需要在 `1.2 m` 之外发现方块。

## 11. 规则手册分析结论

规则文件：

```text
D:\xwechat_files\wxid_obcywqy2j90322_b72d\temp\RWTemp\2026-08\98beac1115289617393180b9b63ce514\RoboGame2026 竞技组规则手册2_1.pdf
规则版本：2026-06-15
```

关键规则：

- 场地 `7.2 m x 4.8 m`；
- 方块 `100 x 100 x 100 mm`，EVA泡棉，尺寸误差 `±5%`；
- 启动区 `600 x 600 mm`；
- 搭建区约 `2.4 m x 0.6 m`；
- 橙色材料区 `1.8 m x 0.3 m`，1.5 m长槽放置 10 个橙色方块；
- 紫色材料区 `2.2 m x 0.4 m`，3个槽放置3个紫色方块；
- 巡线连接启动区、材料区与搭建区；
- 场地墙体有6个视觉标签用于辅助定位和姿态矫正；
- 正式比赛中机器人自主前往材料区正确识别并抓取方块；
- 机器人与建筑不再接触后，建筑保持3秒以上不倒塌才计分。

规则没有规定必须在多远发现方块。因此 `1.2 m` 是否启用属于系统策略选择，不是规则硬要求。

更重要的是：材料区本来就会同时出现多个合法同色方块。当前“多个初始同色候选全部判歧义”不能独立完成材料区目标选择。不要把所有多候选都当成背景错误。

## 12. 需要交给总集成的接口需求

### 12.1 视觉阶段/模式

建议区分：

```text
SEARCH
- 允许远距离观察
- 只提供方向或粗略颜色线索

ACQUIRE
- 机器人已到材料区附近
- 使用候选最大工作距离（当前候选1.2 m）
- 建立抓取轨迹并输出距离

VERIFY
- 抓取、放置和稳定观察
- 使用相应证据规则
```

当前视觉节点是否已有阶段输入，需要由接手 agent 审计；不要直接修改消息接口。

### 12.2 多合法同色目标选择

材料区可能同时出现10个橙色或3个紫色目标。总集成应提供至少一种约束：

```text
desired_color
expected_material_zone
desired_slot/index
expected_grasp_center
或明确的目标选择策略
```

仅靠“最高置信度”或“离画面中心最近”可能选择背景或错误槽位。

### 12.3 距离有效性

当前 `DetectionEstimate` 无法表达“检测到方块，但距离因侧转或遮挡不可信”。候选接口需求：

```text
detected: true
distance_valid: false
distance_invalid_reason: side_rotated | occluded | clipped | out_of_working_range
```

消息接口未扩展前，只能拒绝整个候选，上层会把它理解为没有检测到。

## 13. P0/P1 清单

### P0（2026-08-12 更新）

- [x] 复核当前四个视觉交付文件的最终 diff。
- [x] 独立实现可配置最大抓取距离，并补单元测试。
- [x] 重跑空场景视频、60张正面样本和完整回归（195 项测试，空场景 0 误检）。
- [x] 将触边拒绝、短边测距、最大工作距离和必要文档作为独立 commit 提交并推送。
- [ ] 决定 `CURRENT_STATUS_HANDOFF_REAL_CAMERA_2026-08-11.md` 是否已过时。
- [ ] 由总集成确认 ACQUIRE 阶段是否允许只在 `<=1.2 m` 建立方块轨迹。

### P1

- [ ] 采集系统性侧转角度样本。
- [ ] 采集内部遮挡比例样本。
- [ ] 最终机械结构完成后确定相机安装位、ROI和自遮挡区。
- [ ] 对真实材料区多个同色方块建立目标选择验收。
- [ ] 处理 `0.3 m` 橙色自动曝光/HSV失败。
- [ ] 评估 `0.4 m` 被 `max_area_ratio=0.35` 拒绝是否符合实际抓取阶段需求。

详细清单：

```text
docs/vision/VISION_FIELD_TASKS_2026-08-12.md
```

## 14. 当前下一步（2026-08-12 最终更新）

P0 全部完成并推送。P1-1（侧转角度）和 P1-2（内部遮挡）分析完成，结论：

- 侧转：短边测距天然鲁棒，无需角度过滤。
- 遮挡：10% 安全，30% 距离不可靠，50% 失效。`distance_valid` 字段需求已记录。

GF100 配置已正式启用 `max_working_distance_m=1.2`。全量 195 测试通过。

剩余 P1-3（车载 ROI）和 P1-4（近距离）等待外部条件，不阻塞当前部署。

```text
remote: codex/vision-field-readiness
HEAD: f2b9f5d  (尚未推送文档更新)
```

## 15. 交付边界

如果后续修复必须进入本轮部署：

1. 单独提交；
2. 推送远程 `codex/vision-field-readiness`；
3. 返回完整40位 commit SHA；
4. 给出测试命令、测试数量和结果；
5. 明确是否属于部署阻塞项。

验收只能写软件证据，例如 `SOFTWARE TEST PASS`。没有装车和硬件联合验证时，不得写 `HARDWARE PASS` 或 `REAL CAR PASS`。
