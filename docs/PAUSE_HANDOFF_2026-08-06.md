# RoboGame2026 暂停工作交接记录

记录时间：2026-08-06

当前分支：`codex/manipulator-mock-closed-loop`

## 1. 暂停时的项目状态

单方块 ROS2 模拟闭环已经完成并推送，包括：

```text
PICK
→ 抓取证据验证
→ LIFT
→ RELEASE
→ 放置验证
→ 局部撤退
→ 稳定观察
→ STABLE / FAILED / INCONCLUSIVE / CANCELLED
```

已完成并推送的主要检查点：

```text
c8140dc  feat(manipulator): add verified mock pick-place loop
917b328  feat(manipulator): verify post-retreat placement stability
006708f  docs: summarize mock closed-loop development
```

## 2. 本轮新增但暂停前尚未人工验收的功能

### 2.1 视觉批量验收核心

新增：

```text
cube_perception.batch_report
vision_batch_report 命令入口
```

功能：

- 读取 manifest schema v1；
- 读取 timeline JSONL schema v2；
- 处理多个数据集和多个时间段；
- 支持 `orange`、`purple`、`absent`；
- 检查正确颜色检出率和意外检测率；
- 生成 UTF-8 HTML 汇总报告；
- 失败时返回退出码 2，但仍保存报告。

已经使用遗留的 `vision_batch_smoke/vision_acceptance.json` 成功复现：

```text
2 of 2 segments passed across 2 datasets
```

### 2.2 本地时间段标注菜单

新增：

```text
cube_perception.segment_annotator
vision_segment_annotator 命令入口
```

功能：

- 在本机浏览器播放视频；
- 设置开始/结束时间；
- 选择 `orange`、`purple` 或 `absent`；
- 添加、删除多个时间段；
- 保存或重新打开 manifest；
- 更新匹配数据集时保留其他数据集；
- 视频和 JSONL 使用相对路径；
- 服务仅绑定 `127.0.0.1`；
- 支持 HTTP Range，浏览器可以拖动视频；
- 前端和服务端都进行输入校验。

## 3. 自动验证结果

暂停前最后一次完整测试：

```text
Ran 134 tests
OK
```

其中新增：

- 批量报告专项测试：7 项；
- 时间段菜单专项测试：8 项；
- 菜单端到端 HTTP 测试覆盖页面、视频 Range 和 manifest 保存。

Python 语法检查和 `git diff --check` 均通过。

## 4. 已准备的人工验收数据

原始视频：

```text
%USERPROFILE%\Pictures\Camera Roll\WIN_0803_orange_distance_test.mp4
```

旧的 `distance_acceptance_orange/detections.jsonl` 缺少 schema v2 字段，且时间线与视频不一致，因此没有继续使用。

已经用当前检测器重新处理全部 531 帧，生成：

```text
results/orange_distance_20260806/annotated.mp4
results/orange_distance_20260806/detections.jsonl
```

时间线：

```text
视频约 17.66 秒
JSONL 0.000–17.629 秒
source_fps 约 30.064
```

校验值：

```text
7993f364d7c2c6c54bd880b68d8a1b296aad4aa3ef7e81ef3eaa003bc73a44fd  results/orange_distance_20260806/annotated.mp4
e97eb6c7c7133c7b76c2c8a2d0a22c318c08d6b3d05ac76324048400cdfcde9a  results/orange_distance_20260806/detections.jsonl
```

这些是本地实验产物，不提交 Git。暂停期间不要删除 `results/orange_distance_20260806`。

## 5. 尚未完成的人工步骤

暂停时尚未生成：

```text
results/orange_distance_20260806/vision_acceptance.json
```

这表示标注菜单还没有点击“保存 manifest”。没有已经完成但未保存的标注记录可恢复；下次需要重新在视频中选择时间段。

尚待完成：

1. 使用浏览器菜单标注至少一个橙色可见区间；
2. 如果视频中存在明确空场景，再标注一个 `absent` 区间；
3. 保存 manifest；
4. 运行批量报告；
5. 人工检查 HTML 与视频内容是否一致；
6. 在 Ubuntu 重新构建 `cube_perception` 并验证两个 ROS2 命令入口。

## 6. Windows 恢复命令

打开 PowerShell：

```powershell
$projectRoot = Join-Path $env:USERPROFILE "Documents\机器人算法开发"
Set-Location -LiteralPath $projectRoot

$env:PYTHONPATH = "$PWD\ros2_ws\src\cube_perception;$PWD\ros2_ws\src\robogame_core"

python -m cube_perception.segment_annotator `
  --video ".\results\orange_distance_20260806\annotated.mp4" `
  --jsonl ".\results\orange_distance_20260806\detections.jsonl" `
  --manifest ".\results\orange_distance_20260806\vision_acceptance.json" `
  --dataset-name "Orange distance validation"
```

浏览器操作：

1. 拖到明确开始帧，点击开始或按 `[`；
2. 拖到明确结束帧，点击结束或按 `]`；
3. 填写名称并选择期望结果；
4. 添加时间段；
5. 点击保存；
6. 回到终端按 `Ctrl+C`。

生成报告：

```powershell
python -m cube_perception.batch_report `
  --manifest ".\results\orange_distance_20260806\vision_acceptance.json" `
  --output ".\results\orange_distance_20260806\vision_batch_report.html"

Start-Process ".\results\orange_distance_20260806\vision_batch_report.html"
```

## 7. 暂停时应提交与不应提交的文件

应保存到 Git 的本轮正式文件：

```text
docs/VISION_MODULE.md
docs/BEGINNER_PROJECT_LEARNING_GUIDE.md
docs/PAUSE_HANDOFF_2026-08-06.md
ros2_ws/src/cube_perception/setup.py
ros2_ws/src/cube_perception/cube_perception/batch_report.py
ros2_ws/src/cube_perception/cube_perception/segment_annotator.py
tests/test_vision_batch_report.py
tests/test_vision_segment_annotator.py
```

不应提交：

```text
results/
camera_acceptance_*/
distance_acceptance_*/
vision_batch_smoke/
vision_report_smoke/
*.mp4
*.jsonl 实验输出
Word 临时文件
课程实验输出
```

其他未跟踪的开发计划、Word 文档和生成脚本属于独立工作，不纳入本次视觉工具 checkpoint。

## 8. 恢复后的建议顺序

```text
检查当前分支和 Git 状态
→ 运行 134 项测试
→ 打开菜单完成橙色视频标注
→ 生成批量 HTML
→ 人工核对时间段和判定结果
→ 同步 Ubuntu
→ colcon build --packages-select cube_perception
→ 验证 ros2 run 命令
→ 决定是否合并或创建 PR
```

下一步不要同时修改真实视觉稳定证据和菜单界面。先完成这条离线人工验收链，确保远程数据工作流可靠。
