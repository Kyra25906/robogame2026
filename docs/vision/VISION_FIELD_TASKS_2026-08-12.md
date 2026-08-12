# RoboGame2026 视觉现场任务清单（2026-08-12）

## 1. 使用范围

本清单只维护视觉模块现场工作，不代表总集成结论，也不授权树莓派部署或修改非视觉模块。

固定视觉集成基线：

```text
branch: codex/vision-field-readiness
commit: f6f736ea9c4dce44f49c0b2c6009bcdc0d4aa16b
```

当前允许优先修改：

- `ros2_ws/src/cube_perception/`
- 视觉相关测试
- `docs/vision/`

涉及 `robot.yaml`、launch、消息接口、`localization` 或 `mission_manager` 时，先记录接口需求，不直接覆盖集成版本。

## 2. 当前状态摘要

### 已完成并已进入固定基线

- [x] 同色多个初始候选时不猜测目标。
- [x] 已有轨迹附近出现多个候选时中断确认并标记歧义。
- [x] GF100 `1280 x 720` 候选配置与通用默认配置分离。
- [x] `field` 配置禁止接受模拟证据。
- [x] 新视频处理、取消、恢复、JSONL、人工标注和 HTML 报告已有自动测试。
- [x] 视觉稳定观察支持证据新鲜度、短暂缺口和 `INCONCLUSIVE`。

### 本轮视觉修复交付内容（触边拒绝 + 短边测距）

- [x] 接触图像边框的轮廓不再参与检测和距离估算。
- [x] 调试统计增加 `clipped` 拒绝原因。
- [x] 完整候选使用旋转外接矩形短边估算距离。
- [x] 保持原长宽比置信度计算，避免测距改动意外改变置信度。
- [x] 新增触边拒绝、近边缘保留和短边测距单元测试。

### 最大工作距离过滤（2026-08-12 第二轮）

- [x] `DetectorConfig` 新增 `max_working_distance_m: float | None = None`。
- [x] 超过上限的候选在进入时序过滤器之前被拒绝。
- [x] 调试统计增加 `far` 拒绝原因。
- [x] `max_working_distance_m=None` 时过滤禁用，向后兼容。
- [x] 新增 3 项单元测试：超限拒绝、未超限接受、None 不过滤。
- [x] 空场景视频端到端验证：`max_working_distance_m=1.2` 时 0/523 帧出现橙色确认目标（修复前 176/523，33.65%）。
- [x] 195 项全量测试通过。

本轮额外源码范围：

```text
M ros2_ws/src/cube_perception/cube_perception/node.py
M ros2_ws/src/cube_perception/cube_perception/opencv_detector.py (DetectorConfig + filter)
M tests/test_opencv_detector.py (+3 tests)
```

本轮必要文档范围：

```text
?? docs/vision/GF100_CAMERA_CALIBRATION_CANDIDATE_REPORT_2026-08-12.md
?? docs/vision/VISION_FIELD_TASKS_2026-08-12.md
```

## 3. 已完成的真实数据验证

### GF100 候选内参

- [x] 使用平整硬板棋盘格，内角点 `9 x 6`，实测单格 `16.0 mm`。
- [x] `fixed_grid` 43 张和 `grid_sides` 12 张均检测到完整角点。
- [x] 诊断筛选后采用 37 张，保守两项径向模型 RMS 为 `0.821 px`。
- [x] 候选结果：`fx=2526.98`、`fy=2539.21`、`cx=620.62`、`cy=355.66`。
- [x] 当前 `focal_px=2550.0` 与棋盘格结果相差约 `0.9%`，继续保留为候选值。
- [ ] 尚未写入正式相机内参或 ROS `camera_info`。

详细记录见：

```text
docs/vision/GF100_CAMERA_CALIBRATION_CANDIDATE_REPORT_2026-08-12.md
```

### 正面方块距离验证

- [x] 橙色正面宽度人工确认：`100 mm`。
- [x] 紫色正面宽度使用：`100 mm`。
- [x] 新橙色 `0.5..1.0 m`：30/30 检测，短边测距平均绝对误差 `0.452%`。
- [x] 紫色 `0.5..1.0 m`：30/30 检测，短边测距平均绝对误差 `1.235%`。
- [x] 触边困难样本证明当前旧逻辑会对残缺轮廓输出距离，已实现候选级拒绝。
- [ ] `0.3 m` 橙色因近距离曝光变化，当前 HSV 未形成有效轮廓。
- [ ] `0.4 m` 橙色因目标面积超过 `max_area_ratio=0.35` 被拒绝。

### 环境与未完成小车样本

数据目录：

```text
C:\Users\dahli\Pictures\Camera Roll\environment
```

- [x] 5 张照片和 2 段视频完成只读审计。
- [x] 第一段视频：真实方块与未完成小车同时入镜。
- [x] 第二段视频：主要为未完成小车，可作为空场景负样本。
- [x] 第二段共 523 帧，橙色单帧候选率 `90.2%`。
- [x] 第二段橙色时序确认率 `33.7%`，证明稳定车体背景仍可建立错误轨迹。
- [x] 误检来源主要包括橙色接线端子、线缆和小型车体零件。
- [x] 已生成该空场景视频的正式 JSONL、`expected=absent` manifest 和 HTML 基线报告。
- [x] 基线产物保存在 `results/workspace/gf100_environment_empty_20260812/`，由 Git 忽略。

## 4. P0 部署阻塞项

### P0-1 未完成小车空场景持续确认橙色假目标

状态：`SOFTWARE VERIFIED / PENDING INTEGRATION CONFIRMATION`

事实：

```text
空场景负样本视频：WIN_20260812_17_46_24_Pro.mp4
总帧数：523
```

修复前：

```text
单帧橙色候选：472 帧（90.2%）
时序确认橙色目标：176 帧（33.7%）
```

修复后（`max_working_distance_m=1.2`）：

```text
单帧橙色候选（accepted）：0 帧
时序确认橙色目标：0 帧
橙色轮廓被 far 拒绝：5-6 个/帧
```

风险已消除：所有车体橙色假候选（接线端子、线缆、装饰件）估算距离均在 3.359 m 以上，被 1.2 m 上限在进入时序过滤器前拦截。

60 张正面橙色和紫色样本（0.5-1.0 m）全部保留。

### P0-2 触边与短边测距修复交付

状态：`DELIVERED`

- [x] 检测器单元测试：10 项通过（含 3 项距离过滤测试）。
- [x] 完整回归测试：195 项通过。
- [x] 新橙色真实照片：30/30，平均绝对误差 `0.452%`。
- [x] 紫色真实照片：30/30，平均绝对误差 `1.235%`。
- [x] 远程分支：`codex/vision-field-readiness`，commit `f6f736e`，已推送。
- [x] 空场景视频端到端验证通过。

## 5. P1 待采集与待验证

### P1-1 明显侧转的距离有效边界

状态：`COMPLETED / NO FILTER NEEDED`

结论：42/42 检出（7 角度 × 2 颜色 × 3 张）。短边测距天然抗 yaw 旋转，距离稳定在 0.60–0.64m（3cm 浮动），置信度最低 0.740 仍远超 0.45 门槛。不需要增加角度拒绝规则。

详细报告：`docs/vision/P1_ANGLE_AND_OCCLUSION_ANALYSIS_2026-08-12.md`

### P1-2 画面内部遮挡

状态：`COMPLETED / KNOWN LIMITATION`

结论：10% 遮挡安全（6/6 检出，距离准确）；30% 仍检出但距离高估约 30%；50% 完全丢失（0/6）。当前接口无法表达"检测到但距离不可信"，30% 场景只能整体拒绝候选。接口需求已记录，不在此轮修改。

详细报告：`docs/vision/P1_ANGLE_AND_OCCLUSION_ANALYSIS_2026-08-12.md`

### P1-3 最终车载 ROI 与自遮挡

状态：`WAITING FOR FINAL MECHANICAL LAYOUT`

- [ ] 相机最终安装高度、俯角和方向确认后再采集。
- [ ] 明确车体永久占据区域、机械臂运动扫掠区域和实际放置区。
- [ ] 验证撤退完成后相机是否真正看清放置区域。
- [ ] 再确定 ROI，不使用当前未完成车体画面猜最终 ROI。

### P1-4 近距离橙色检测

状态：`OPEN / NON-BLOCKING UNTIL REQUIRED WORKING RANGE IS CONFIRMED`

- [ ] 确认任务是否要求 `0.3 m` 和 `0.4 m` 仍由视觉持续测距。
- [ ] 若要求 `0.3 m`，单独记录自动曝光导致的 HSV 漂移。
- [ ] 若要求 `0.4 m`，评估大目标面积上限与背景误检的取舍。
- [ ] 不与空场景误检修复同时调整。

## 6. 接口需求（只记录，不直接修改）

### 距离有效性

当前 `DetectionEstimate` 无法表达“看到了方块，但距离因侧转或遮挡不可信”。后续若数据证明必须区分，建议向总集成提出：

```text
detected: true
distance_valid: false
distance_invalid_reason: side_rotated | occluded | clipped
```

在消息接口未扩展前，视觉模块只能在距离不可信时拒绝整个候选，上层会把它理解为“没有检测到”。

本清单不授权直接修改消息定义、`mission_manager`、`localization` 或 launch。

## 7. 数据保存与 Git 边界

以下内容保留在项目仓库之外或 `results/`，不进入普通 Git：

- 原始照片和视频；
- JSONL 检测时间线；
- HTML 运行报告；
- 人工标注产物；
- 渲染缩略图和临时分析图。

允许进入视觉交付 commit：

- `cube_perception` 源码；
- 视觉单元测试；
- 必要的候选配置；
- `docs/vision` 中的必要 Markdown 报告和任务清单。

## 8. 当前下一步

P0 全部完成。P1-1（侧转）和 P1-2（遮挡）分析完成。GF100 配置已正式启用 `max_working_distance_m=1.2`。

P1 剩余（等待外部条件）：

- P1-3：最终车载 ROI 与自遮挡（等待机械结构确定）；
- P1-4：近距离橙色检测（等待工作距离确认）。

故意不在同一轮做：

- HSV 调参；
- ROI 调参；
- 面积/边长阈值调参；
- 时序确认参数调参；
- 消息接口修改；
- 树莓派部署或真车动作。
