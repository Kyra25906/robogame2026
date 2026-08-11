# GF100 真实摄像头台架数据采集指南

日期：2026-08-11

适用阶段：小车机械结构和最终相机安装尚未完成，GF100 通过临时刚性台架独立采集。

目标：建立可追溯的相机内参、距离标定和视觉验收数据，为后续车载复验提供依据。

## 1. 当前边界

当前 `focal_px=2550.0` 只是 GF100、1280×720、当前对焦状态下的台架候选值，不是严格相机标定结果，也不是通用常量。

小车结构未完成时可以验证：

- 摄像头枚举、分辨率、帧率和持续采集能力；
- 固定对焦条件下的相机内参；
- 10 cm 方块的候选像素焦距和距离误差；
- 橙色、紫色、空场景、多候选、遮挡和光照变化；
- 离线处理速度、误检、漏检和歧义行为。

当前不能验证：

- 最终抓取 ROI 和放置观察 ROI；
- 相机相对车体的高度、横向偏移和俯角；
- 机械臂或车体遮挡；
- 底盘运动引起的模糊和振动；
- 撤退后是否能看清完整放置区；
- 真车抓取和放置闭环是否通过。

## 2. 准备物品

- GF100 USB 摄像头；
- 三脚架、桌面夹具或其他刚性临时支架；
- 10 cm 橙色方块；
- 10 cm 紫色方块；
- 卷尺或激光测距仪；
- 平整、硬质的棋盘格标定板；
- 白纸或电子记录表；
- 遮挡用纸板；
- 木凳、木桌、橙色背景板、紫色背景板等真实干扰物；
- 光线相对稳定的采集区域。

正式数据不得手持摄像头采集。摄像头通电后先等待约 30 秒，使曝光和白平衡趋于稳定。

## 3. 每轮采集前记录的相机参数

每次采集开始前创建或更新 `camera_metadata.json`，至少记录：

| 字段 | 示例或说明 |
| --- | --- |
| `camera_model` | `GF100` |
| `resolution` | `1280x720` |
| `requested_fps` | 请求帧率，例如 `30` |
| `actual_fps` | 程序实际报告值 |
| `backend` | `DSHOW`、`MSMF` 或 `V4L2` |
| `pixel_format` | `MJPEG`、`YUYV` 或 `unknown` |
| `focus_mode` | `manual`、`auto` 或 `unknown` |
| `focus_value` | 驱动值或 `unknown` |
| `exposure_mode` | `manual`、`auto` 或 `unknown` |
| `exposure_value` | 驱动值或 `unknown` |
| `white_balance_mode` | `manual`、`auto` 或 `unknown` |
| `white_balance_value` | 驱动值或 `unknown` |
| `camera_height_m` | 临时台架高度 |
| `camera_pitch_deg` | 临时台架俯角 |
| `lighting` | 例如“室内冷白灯” |
| `distance_reference` | 建议写 `lens_front_approximation` |
| `capture_date` | 实际采集日期 |

无法查询的值写 `unknown`，不要留空，也不要猜测。正式采集过程中不得改变分辨率、镜头或手动对焦值。

### 3.1 相机记录参数是什么意思

| 参数 | 大白话含义 | 为什么要记录 | 去哪里找 |
| --- | --- | --- | --- |
| `camera_model` | 摄像头型号 | 不同相机不能共用标定值 | 相机外壳标签、包装或 Windows 设备管理器；本项目当前为 GF100 |
| `resolution` | 一帧图像有多少像素，例如 1280×720 | 分辨率变化会改变像素焦距、面积阈值和 ROI | 检测程序启动时的 `size=` 输出；Windows 相机属性；Linux `v4l2-ctl --list-formats-ext` |
| `requested_fps` | 程序希望相机提供的帧率 | 用于检查请求是否被驱动接受 | 启动命令的 `--fps` 参数 |
| `actual_fps` | 相机实际报告或实测的帧率 | 真实帧率影响连续确认、运动模糊和性能 | 检测程序启动时的 `fps=` 输出；ROS 中用 `ros2 topic hz /camera/image_raw` 实测 |
| `backend` | OpenCV 通过哪套系统接口读取相机 | 不同后端支持的格式和控制项可能不同 | 检测程序启动时的 `backend=` 输出；Windows 常见 `DSHOW`/`MSMF`，Linux 常见 `V4L2` |
| `pixel_format` | USB 传输的是 MJPEG、YUYV 等格式 | 格式影响带宽、帧率、解码负载和颜色 | Linux `v4l2-ctl --list-formats-ext`；Windows 可由驱动工具查询，查不到写 `unknown` |
| `focus_mode` | 自动对焦还是手动对焦 | 对焦变化会改变图像清晰度和有效焦距 | Linux `v4l2-ctl --list-ctrls`；Windows 相机驱动属性页；查不到写 `unknown` |
| `focus_value` | 手动对焦控制值 | 以后复现实验需要恢复相同位置 | 同上；只有手动模式且驱动返回数值时才填写 |
| `exposure_mode` | 自动曝光还是手动曝光 | 自动曝光变化会让 HSV 和运动模糊随时间漂移 | Linux `v4l2-ctl --list-ctrls`/`--all`；Windows 驱动属性页 |
| `exposure_value` | 曝光控制值或快门相关驱动值 | 用于复现亮度和运动清晰度 | 驱动属性或 `v4l2-ctl --all`；驱动值不一定直接等于秒数 |
| `white_balance_mode` | 自动或手动白平衡 | 白平衡变化会改变橙色和紫色的 HSV | 驱动属性或 `v4l2-ctl --list-ctrls` |
| `white_balance_value` | 手动白平衡控制值 | 用于复现颜色 | 驱动属性或 `v4l2-ctl --all` |
| `camera_height_m` | 镜头中心距离地面的高度 | 装车后用于复现视角和设计 ROI | 用卷尺测量，不是软件参数 |
| `camera_pitch_deg` | 相机向下或向上的倾斜角 | 影响方块在画面中的位置和透视 | 用量角器或手机水平仪测量；注明正负方向约定 |
| `lighting` | 现场是什么灯光和亮暗条件 | 同一HSV在不同光照下可能表现不同 | 人工记录，例如“室内冷白灯、顶灯全开” |
| `distance_reference` | 从相机哪里量到方块哪里 | 不统一测量起点会制造系统误差 | 人工规定；当前建议镜头前端中心到方块正面，写 `lens_front_approximation` |

注意：驱动中的 `focus_value`、`exposure_value` 等通常是设备控制值，不一定有统一物理单位。它们最重要的作用是复现实验，不应在不知道含义时换算成毫米或秒。

### 3.2 Windows 上先去哪里查看

1. 打开“设备管理器”→“照相机”或“图像设备”，确认设备名称；
2. 用本项目独立检测入口打开摄像头，终端会显示实际后端、分辨率和驱动报告帧率；
3. 如果厂家驱动或采集软件提供“摄像头属性”，记录曝光、对焦和白平衡模式；
4. 如果 Windows 不提供某个控制值，记录 `unknown`，不要用手机拍摄参数代替 GF100 参数。

示例命令：

```powershell
$env:PYTHONPATH = (
    (Resolve-Path "ros2_ws/src/cube_perception").Path +
    ";" +
    (Resolve-Path "ros2_ws/src/robogame_core").Path
)

D:\python.exe -B -m cube_perception.standalone `
  --source 0 `
  --backend dshow `
  --width 1280 `
  --height 720 `
  --fps 30 `
  --config ros2_ws/src/cube_perception/config/vision_default.json
```

重点记录终端中的实际输出，而不只记录命令里请求的数值：

```text
capture opened: backend=DSHOW size=1280x720 fps=30.00 source=0
```

如果编号 0 不是 GF100，再依次尝试 1、2。按 `Q` 或 `Esc` 退出。

### 3.3 Ubuntu/树莓派上去哪里查看

安装 V4L2 工具后可只读查询摄像头能力：

```bash
sudo apt install v4l-utils
v4l2-ctl --list-devices
v4l2-ctl -d /dev/video0 --list-formats-ext
v4l2-ctl -d /dev/video0 --list-ctrls
v4l2-ctl -d /dev/video0 --all
```

这些命令分别用于查看：

- 哪个 `/dev/videoN` 是 GF100；
- 支持哪些分辨率、像素格式和帧率；
- 支持哪些曝光、对焦和白平衡控制；
- 当前驱动实际状态。

当前阶段只查询和采集，不连接 STM32，不启动 `robot_bridge`，不给执行器上动力。

### 3.4 ROS 2 中去哪里查看

相机节点启动后，可检查：

```bash
ros2 topic list
ros2 topic hz /camera/image_raw
ros2 topic echo /camera/camera_info --once
```

`/camera/camera_info` 中重点关注：

- `width`、`height`：内参对应的分辨率；
- `k[0]`：`fx`，水平方向像素焦距；
- `k[4]`：`fy`，垂直方向像素焦距；
- `k[2]`、`k[5]`：主点 `cx`、`cy`；
- `d`：镜头畸变参数；
- `distortion_model`：畸变模型。

只有当 CameraInfo 与当前 GF100、当前分辨率和当前对焦状态匹配时，才能把它当成可信内参。消息存在不等于内容正确。

### 3.5 检测配置参数是什么意思

视觉 JSON 和 ROS 参数中常用字段：

| 参数 | 含义 | 当前使用边界 |
| --- | --- | --- |
| `orange_hsv`、`purple_hsv` | 颜色在 HSV 空间中的上下限 | 必须用真实相机和现场光照验证，不能照抄手机参数 |
| `min_area_px` | 允许候选轮廓的最小像素面积 | 太小会增加噪声，太大会漏掉远处方块 |
| `cube_size_m` | 方块真实边长 | 本项目方块为 `0.1` m |
| `focal_px` | 离线检测使用的像素焦距 | GF100 专用配置中的 2550 只是台架候选值 |
| `fallback_focal_px` | ROS 没有可信 CameraInfo 时使用的兜底像素焦距 | 应与具体相机配置绑定，不是通用常量 |
| `min_confidence` | 几何候选进入时序过滤前的最低质量分数 | 不是“任务目标概率” |
| `confirm_frames` | 连续出现多少帧后才确认 | 当前默认 3 帧 |
| `max_missed_frames` | 短暂丢失后保留轨迹用于重新匹配的帧数 | 保留轨迹不等于继续发布旧目标 |
| `match_distance_px` | 新候选距离旧轨迹多近才视为同一目标 | 当前默认 80 px，分辨率改变后需复验 |
| `smoothing_alpha` | 当前帧对平滑结果的权重 | 越小越平滑，但响应越慢 |
| `roi_*_ratio` | 允许检测的归一化画面区域 | 最终安装未冻结前不猜正式 ROI |
| `min_rectangularity` | 轮廓填满最小外接矩形的程度 | 太严格会拒绝裁切或遮挡目标 |
| `min_solidity` | 轮廓相对凸包的完整程度 | 用于排除破碎、凹陷轮廓 |
| `max_rotated_aspect_ratio` | 旋转外接矩形允许的最大长宽比 | 中央紫色被画面裁切时曾因该条件失败 |

当前配置文件位置：

```text
ros2_ws/src/cube_perception/config/vision_default.json
ros2_ws/src/cube_perception/config/vision_demo_roi.json
ros2_ws/src/cube_perception/config/vision_gf100_1280x720_bench.json
ros2_ws/src/robogame_bringup/config/robot.yaml
ros2_ws/src/robogame_bringup/config/robot_field.yaml
```

参数来源必须分别标记为：设备查询、人工测量、棋盘格标定、方块距离拟合或现场经验。不能把“当前文件里有这个数”当成已经完成真实验收。

## 4. 距离和方块摆放规则

距离从摄像头镜头前端中心附近量到方块朝向摄像头的正面平面，并在记录中注明这是光心的近似基准。

用于正常标定的方块应满足：

- 正面尽量平行于图像平面；
- 完整进入画面；
- 不倾斜、不遮挡；
- 周围没有第二个同色目标；
- 背景不使用相似颜色；
- 相机、支架和对焦保持不变。

裁切、倾斜、遮挡和相似背景应另建失败边界数据，不能混入正常距离标定集。

## 5. 第一组：棋盘格相机内参

用途：计算 `fx`、`fy`、`cx`、`cy` 和镜头畸变参数。

采集 25～30 张清晰照片，覆盖：

- 画面中央、左上、右上、左下、右下；
- 较近、中等和较远距离；
- 向左、向右、向上和向下倾斜；
- 标定板约占画面 30%、50% 和 70%。

要求棋盘格完整可见、平整、不反光。不得用 30 张近乎相同的画面代替多姿态采集。

命名示例：

```text
gf100_intrinsic_1280x720_001.jpg
gf100_intrinsic_1280x720_002.jpg
...
gf100_intrinsic_1280x720_030.jpg
```

## 6. 第二组：距离标定集

用途：计算 10 cm 方块对应的候选 `focal_px`。

标定距离：

- 0.40 m；
- 0.60 m；
- 0.80 m；
- 1.00 m。

每个距离、每种颜色采集 5 张，方块位于画面中央：

```text
4 个距离 × 2 种颜色 × 5 次 = 40 张
```

命名示例：

```text
gf100_cal_orange_040cm_center_01.jpg
gf100_cal_purple_080cm_center_03.jpg
```

每个合格轮廓可以计算：

```text
focal_px_i = detected_width_px × true_distance_m ÷ 0.1
```

应查看多个样本的分布，优先使用中位数或正式拟合结果，不能只用一张照片定值。

## 7. 第三组：独立距离验证集

用途：检查候选焦距对未参与标定的数据是否有效。

验证距离：

- 0.30 m；
- 0.50 m；
- 0.70 m；
- 0.90 m；
- 1.20 m。

每个距离、每种颜色采集 3 张：

```text
5 个距离 × 2 种颜色 × 3 次 = 30 张
```

命名示例：

```text
gf100_val_orange_050cm_center_01.jpg
gf100_val_purple_090cm_center_02.jpg
```

必须分别计算绝对误差和相对误差：

```text
absolute_error_m = |estimated_distance_m - true_distance_m|
relative_error = absolute_error_m / true_distance_m
```

检测到碎片、错误目标或没有形成完整轮廓的样本应记录为检测失败，不得从报告中删除后只统计成功样本。

## 8. 第四组：画面位置与边缘

固定建议距离为 0.70 m。每种颜色分别放在：

- 中央；
- 左侧、右侧；
- 上侧、下侧；
- 左上、右上、左下、右下。

每个位置采集 3 张：

```text
9 个位置 × 2 种颜色 × 3 次 = 54 张
```

正常位置样本必须完整进入画面。另建故意裁切样本：

- 左边裁掉约 10%；
- 右边裁掉约 10%；
- 上边裁掉约 10%；
- 下边裁掉约 10%。

命名示例：

```text
gf100_position_purple_070cm_left_01.jpg
gf100_position_orange_070cm_top_right_02.jpg
gf100_edge_purple_070cm_top_crop10_01.jpg
```

故意裁切样本用于验证失败边界，不进入正常召回率统计。

## 9. 第五组：空场景和背景误检

至少覆盖：

- 只有地面；
- 木凳和木桌；
- 橙色背景板；
- 紫色背景板；
- 工具和机械结构；
- 人员经过；
- 水瓶；
- 强顶灯；
- 金属导轨反光；
- 阴影；
- 手指或纸板靠近镜头。

每个场景采集 3 张照片，并录制 5～10 秒视频。

特别重要的负样本：

```text
没有真实方块 + 有橙色背景板
没有真实方块 + 有紫色背景板
```

人工期望统一标注为 `absent`。

## 10. 第六组：多候选歧义

每个条件录制 5～10 秒视频：

1. 一个橙色方块；
2. 两个橙色方块；
3. 橙色方块加木凳；
4. 橙色方块加橙色背景板；
5. 一个紫色方块；
6. 两个紫色方块；
7. 紫色方块加紫色背景板；
8. 橙色和紫色各一个；
9. 两个目标距离不同；
10. 一个目标在中央、另一个位于边缘。

当前没有真实 ROI 时，期望行为为：

```text
单个同色候选 → 允许连续确认
多个同色候选 → ambiguous，不猜测目标
```

多候选视频不进入单目标召回率统计。

## 11. 第七组：遮挡、消失和重新出现

每种颜色分别录制以下过程。

部分遮挡：

```text
完整可见 3 秒 → 遮挡约 25% 持续 2 秒 → 恢复 3 秒
```

大面积遮挡：

```text
完整可见 3 秒 → 遮挡约 75% 持续 2 秒 → 恢复 3 秒
```

完全消失：

```text
完整可见 3 秒 → 移出画面 3 秒 → 重新放回 3 秒
```

快速经过：

```text
空场景 2 秒 → 方块快速经过 → 空场景 2 秒
```

验证目标：

- 移除目标后 0.5 秒内停止报告；
- 遮挡期间不发布旧目标；
- 重新出现后重新累计确认帧；
- 多候选时记录 `ambiguous=true`。

## 12. 第八组：光照变化

至少覆盖：

- 正常室内灯；
- 较暗和较亮；
- 顶灯直射；
- 方块表面高光；
- 侧面阴影；
- 背光；
- 人员经过引起的光照遮挡；
- 自动曝光开启时的亮暗变化。

每种颜色在每种光照条件下采集 3 张中央位置照片，并录制约 5 秒视频。

调 HSV 时只能使用其中一部分，必须保留未参与调参的独立光照验证样本。

## 13. 推荐目录结构

原始数据目录：

```text
GF100_DATASET_2026-08-11/
├─ camera_metadata.json
├─ manifest.csv
├─ intrinsic/
├─ distance_calibration/
│  ├─ orange/
│  └─ purple/
├─ distance_validation/
│  ├─ orange/
│  └─ purple/
├─ positions/
├─ edge_crops/
├─ absent/
├─ multi_candidate/
├─ occlusion/
├─ lighting/
└─ videos/
```

算法结果另存：

```text
results/GF100_DATASET_2026-08-11/
├─ detections/
├─ annotated/
├─ reports/
└─ vision_acceptance.json
```

原始输入、JSONL、人工标注和 HTML 报告分别保存。算法输出不得覆盖原始文件，`results/` 和大视频不得提交普通 Git。

## 14. manifest.csv 字段

建议至少包含：

```csv
sample_id,file,split,media_type,expected,cube_size_m,true_distance_m,distance_reference,target_position,target_count,occlusion,lighting,camera_model,width,height,requested_fps,actual_fps,backend,pixel_format,focus_mode,focus_value,exposure_mode,exposure_value,white_balance_mode,white_balance_value,expected_result,notes
```

关键取值：

- `split`：`calibration` 或 `validation`；
- `expected`：`orange`、`purple` 或 `absent`；
- `target_count`：画面中的真实方块数量；
- `expected_result`：`detected`、`absent` 或 `ambiguous`；
- `target_position`：`center`、`left`、`right`、`top` 等；
- `notes`：记录裁切、倾斜、反光、对焦异常等情况。

## 15. 最低采集量

| 类别 | 最低数量 |
| --- | ---: |
| 棋盘格内参照片 | 25 张 |
| 距离标定照片 | 40 张 |
| 独立距离验证照片 | 30 张 |
| 位置变化照片 | 54 张 |
| 空场景 | 10 类，每类 3 张和 1 段视频 |
| 单目标视频 | 橙色、紫色各 4 段 |
| 多候选视频 | 至少 6 段 |
| 遮挡/消失视频 | 橙色、紫色各 3 段 |
| 光照视频 | 至少 6 段 |

如果现场时间不足，第一轮只完成：

```text
固定相机设置
→ 记录 camera_metadata.json
→ 采集距离标定 40 张
→ 采集独立验证 30 张
→ 检查文件、距离和命名是否完整
```

第一轮采集完成前不要调整 HSV。先保留原始事实，再使用当前算法生成 GF100 基线报告。

## 16. 装车后的复验要求

如果摄像头型号、分辨率、镜头和手动对焦都没有变化，内参通常不需要从零重做，但必须使用独立距离样本复验。

装车后必须补充：

- 最终相机高度、俯角和相对车体位置；
- 抓取 ROI 和放置观察 ROI；
- 机械臂与车体遮挡范围；
- 运动模糊和支架振动；
- 撤退后完整放置区的可观察性。

如果改变分辨率、镜头或对焦状态，必须重新标定相机内参和候选 `focal_px`。
