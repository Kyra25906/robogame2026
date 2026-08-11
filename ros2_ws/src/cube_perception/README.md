# cube_perception 使用说明

## 1. 这个包负责什么

它相当于机器人的“眼睛”：从相机画面中找橙色、紫色方块，输出颜色、置信度、距离、左右偏差和像素位置。当前路线是 HSV 颜色分割、形态学去噪、ROI、轮廓形状过滤、针孔模型估距和连续帧确认，不需要训练模型。

## 2. 文件分工

- `cube_perception/node.py`：ROS2 正式节点，订阅相机图像，发布 `/cubes`。
- `cube_perception/opencv_detector.py`：单帧 HSV、ROI、轮廓过滤和距离估算。
- `cube_perception/temporal_filter.py`：连续多帧确认、目标匹配和数值平滑。
- `cube_perception/standalone.py`：不依赖相机 ROS 驱动，直接测试摄像头、照片或视频。
- `cube_perception/hsv_tuner.py`：用滑块调橙色或紫色 HSV 阈值，按 `S` 保存。
- `cube_perception/mock_node.py`：持续发布理想的假目标，供整车流程联调。
- `config/vision_default.json`：常规视觉参数。
- `config/vision_demo_roi.json`：带演示 ROI 的参数样例。
- `setup.py`：登记四个 `ros2 run` 程序入口。

## 3. 编译和加载

```bash
cd ~/robogame/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-up-to cube_perception
source install/setup.bash
```

## 4. 最适合新手的独立测试

直接打开 USB 摄像头：

```bash
ros2 run cube_perception cube_detector \
  --source 0 \
  --config ~/robogame/ros2_ws/src/cube_perception/config/vision_default.json
```

Windows 或需要固定采集格式时，可以显式指定后端和请求参数：

```powershell
python -m cube_perception.standalone `
  --source 0 --backend msmf `
  --width 1280 --height 720 --fps 30 `
  --config .\ros2_ws\src\cube_perception\config\vision_default.json
```

程序启动后会打印驱动实际接受的后端、分辨率和帧率。请求值不一定会被摄像头接受，应以打印的实际值为准。`msmf` 无法打开时可测试 `dshow`；笔记本内置 MIPI/IPU 摄像头可能只对 Windows 相机应用开放，此时应先用相机应用录制 MP4，再把文件路径交给 `--source`。

测试照片或视频时，把 `--source 0` 换成文件路径。窗口中：黄色框是 ROI；圆和文字是已确认目标；按 `Q` 或 `Esc` 退出。

保存检测结果和标注视频：

```bash
ros2 run cube_perception cube_detector \
  --source ~/Downloads/test.mp4 \
  --config ~/robogame/ros2_ws/src/cube_perception/config/vision_default.json \
  --headless \
  --jsonl ~/Downloads/detections.jsonl \
  --output-video ~/Downloads/annotated.mp4
```

录像输入写入 JSONL 的 `timestamp_s` 来自录像时间轴；摄像头输入则使用运行时单调时钟。这样可以用录像准确验收“目标移除后多久停止报告”。

生成汇总验收报告：

```bash
ros2 run cube_perception vision_report \
  --jsonl ~/Downloads/detections.jsonl \
  --output ~/Downloads/vision_report.md
```

如果已经确认某个时间段内橙色方块始终在画面中，可以检查该时间段的逐帧检测比例：

```bash
ros2 run cube_perception vision_report \
  --jsonl ~/Downloads/detections.jsonl \
  --start-s 5.0 --end-s 15.0 \
  --expected-color orange --min-detection-ratio 0.90
```

报告包含总帧数、录像时间轴帧率、每种颜色的检测帧比例、最长连续检测、置信度和距离统计。只有当所选时间段内目标确实每帧都存在时，检测比例才可以近似用于召回率验收；目标本来不在画面中的帧不能算作漏检。

### JSONL 版本和时间轴

当前 `cube_detector` 写出版本 2 的 JSONL。每一帧仍然占一行，并携带自己的格式和时间信息，例如：

```json
{
  "schema_version": 2,
  "frame": 1,
  "timestamp_s": 0.0333,
  "timestamp_kind": "media",
  "source_fps": 30.064,
  "detections": []
}
```

- `schema_version`：JSONL 格式版本；当前报告工具只接受版本 2。
- `timestamp_s`：当前帧在对应时间轴中的秒数。
- `timestamp_kind: media`：输入是图片或录像，时间来自原媒体，可以用 `--start-s` 和 `--end-s` 选择录像区间。
- `timestamp_kind: monotonic`：输入是实时摄像头，时间表示程序启动后经过的时间，不是录像媒体时间。
- `source_fps`：录像文件或摄像头驱动报告的名义帧率；驱动无法提供时为 `null`。

版本 2 以前的 JSONL 没有明确记录时间类型，旧 `timestamp_s` 可能是电脑处理录像所花的时间，而不是原录像时间。为了避免错误选择验收区间或误报媒体帧率，`vision_report` 会拒绝旧格式：

```text
error: legacy JSONL is not supported;
regenerate it with the current cube_detector
```

不要手工补版本号或转换旧 JSONL。应保留原始录像，并用当前代码重新生成。例如：

```powershell
python -m cube_perception.standalone `
  --source "C:\path\to\validation.mp4" `
  --config "$PWD\ros2_ws\src\cube_perception\config\vision_default.json" `
  --headless `
  --jsonl "$PWD\validation\detections.jsonl" `
  --output-video "$PWD\validation\annotated.mp4"
```

报告中的两个速率含义不同：

```text
Source FPS: 30.06
Observed record rate: 30.03 Hz
```

- `Source FPS`：文件或驱动报告的名义帧率。
- `Observed record rate`：根据相邻记录时间戳推算的记录频率。
- 两者接近通常说明时间轴正常，但都不代表纯算法计算速度。
- 算法处理性能以 `cube_detector` 结束时打印的 `average ... FPS` 为准。

### 查看逐帧 JSONL

JSONL 面向程序读取，标准格式是一帧一行，不应为了显示美观改成多行存储。PowerShell 中可以只格式化显示，不修改原文件。

缩进查看前 3 帧的完整结构：

```powershell
Get-Content "$PWD\validation\detections.jsonl" -TotalCount 3 |
ForEach-Object {
  $_ | ConvertFrom-Json | ConvertTo-Json -Depth 10
}
```

用表格查看前 10 帧摘要：

```powershell
Get-Content "$PWD\validation\detections.jsonl" -TotalCount 10 |
ForEach-Object {
  $record = $_ | ConvertFrom-Json
  [PSCustomObject]@{
    Frame      = $record.frame
    Time_s     = $record.timestamp_s
    Timeline   = $record.timestamp_kind
    Detections = $record.detections.Count
    Colors     = $record.detections.color -join ", "
  }
} | Format-Table -AutoSize
```

### 生成和查看验收报告

报告支持 HTML、Markdown 和纯文本三种格式。默认根据 `--output` 的扩展名自动选择：

```powershell
# 浏览器直接打开，最适合人工验收
python -m cube_perception.report `
  --jsonl "$PWD\validation\detections.jsonl" `
  --output "$PWD\validation\vision_report.html"

# Markdown 源文件，适合放入 Git 或在 VS Code 中预览
python -m cube_perception.report `
  --jsonl "$PWD\validation\detections.jsonl" `
  --output "$PWD\validation\vision_report.md"

# 普通文本，记事本可直接阅读
python -m cube_perception.report `
  --jsonl "$PWD\validation\detections.jsonl" `
  --output "$PWD\validation\vision_report.txt"
```

不提供 `--output` 时，报告以纯文本打印到终端。`.html`、`.htm`、`.md`、`.markdown` 和 `.txt` 会自动识别；其他扩展名必须显式指定 `--format html`、`--format markdown` 或 `--format text`。

设置验收门槛时，PASS 返回退出码 0；FAIL 返回退出码 2。即使 FAIL，报告文件也会正常保存，便于查看失败原因：

```powershell
python -m cube_perception.report `
  --jsonl "$PWD\validation\detections.jsonl" `
  --start-s 3 --end-s 18 `
  --expected-color purple `
  --min-detection-ratio 0.90 `
  --output "$PWD\validation\purple_report.html"
```

HTML 是单个离线文件，不依赖网络、JavaScript 或外部样式，双击即可用浏览器打开。

Windows 记事本只能显示 Markdown 源码，不能渲染标题和表格。推荐使用 VS Code：

```powershell
code "$PWD\validation\vision_report.md"
```

打开后按 `Ctrl+Shift+V` 显示 Markdown 预览。不要只修改已经生成文件的扩展名；应重新运行命令，让报告工具按目标格式生成内容。

调 HSV：

```bash
ros2 run cube_perception hsv_tuner \
  --source 0 \
  --config ~/robogame/ros2_ws/src/cube_perception/config/vision_default.json \
  --color orange
```

先让目标在二值图里变白、背景尽量黑，再按 `S` 保存。紫色测试时把最后一项改为 `purple`。

## 5. ROS2 正式用法

需要其他相机节点发布 `/camera/image_raw`，最好同时发布 `/camera/camera_info`：

```bash
ros2 run cube_perception cube_perception --ros-args \
  --params-file ~/robogame/ros2_ws/src/robogame_bringup/config/robot.yaml
```

查看结果：

```bash
ros2 topic echo /cubes
ros2 topic hz /cubes
```

没有相机时可运行：

```bash
ros2 run cube_perception mock_perception
```

## 6. 重点参数怎么理解

- `orange_hsv`、`purple_hsv`：允许通过的颜色范围。
- `roi_*_ratio`：只在画面的指定区域寻找，范围是 0～1。
- `min_area_px`：太小的色块不要。
- `max_area_ratio`：占满画面的大片同色背景不要。
- `min_rectangularity`、`min_solidity`、`max_rotated_aspect_ratio`：排除不像方块的轮廓。
- `confirm_frames`：连续看到多少帧才对外发布。
- `max_missed_frames`：短暂丢失多少帧后删除跟踪记录。
- `fallback_focal_px`／JSON 中的 `focal_px`：没有相机内参时使用的像素焦距。
  通用配置保留开发兜底值 `700.0`；GF100、1280×720、当前手动对焦状态的台架
  候选值 `2550.0` 单独保存在 `config/vision_gf100_1280x720_bench.json`，现场模式
  由 `robot_field.yaml` 显式覆盖。改变分辨率、镜头或对焦后必须重新标定。

## 7. 当前完成度与现场待办

已完成照片、视频、USB 相机独立入口以及自动测试。现场必须重新固定曝光、采集真实方块数据、调 HSV/ROI、完成相机内参标定，并统计至少 30 段/张每种颜色的召回率。

当前距离来自“已知方块边长 + 针孔模型”，斜视时会产生误差；代码尚未进行畸变矫正、透视/仿射矫正，也没有 ToF 融合。只有真实视角证明误差影响抓取时再增加矫正，不要先盲目增加复杂度。

## 8. 自动测试

```bash
cd ~/robogame
python3 -m unittest tests.test_perception tests.test_opencv_detector tests.test_temporal_filter -v
```
