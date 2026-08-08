# Independent cube-vision module

This module can be developed and accepted without the chassis, MCU, gripper, or
ROS graph. It accepts a camera, image, or video and produces:

- orange and purple detections;
- confidence;
- estimated distance;
- left/right offset;
- yaw error;
- annotated video;
- JSON Lines records for later analysis.

## Install on Ubuntu

```bash
sudo apt install python3-opencv
cd ros2_ws
source /opt/ros/$ROS_DISTRO/setup.bash
colcon build --symlink-install --packages-select robogame_core cube_perception
source install/setup.bash
```

The default configuration is installed with the package. During source-tree
development it is at:

```text
ros2_ws/src/cube_perception/config/vision_default.json
```

Copy it for each camera and lighting setup. Do not tune the shared default file
on the competition day.

## Tune HSV thresholds

Use a recorded video first:

```bash
ros2 run cube_perception hsv_tuner \
  --source data/lighting_01.mp4 \
  --config config/my_camera.json \
  --color orange
```

Move the six sliders until the white mask contains the cube surface but little
of the floor, robot, or wall. Press `S` to save. Repeat for purple.

Collect footage under:

- bright and dim lighting;
- front, left, and right views;
- 0.2 m to 1.5 m distance;
- single and adjacent cubes;
- partial gripper occlusion;
- robot parts visible in the frame.

## Run independently

Camera:

```bash
ros2 run cube_perception cube_detector \
  --source 0 \
  --config config/my_camera.json
```

Recorded video with reusable evidence:

```bash
ros2 run cube_perception cube_detector \
  --source data/validation.mp4 \
  --config config/my_camera.json \
  --jsonl results/validation.jsonl \
  --output-video results/validation_annotated.mp4 \
  --headless
```

Press `Q` or `Esc` to stop a preview.

## Calibrate distance

The initial estimate uses the pinhole relation:

```text
distance = real cube width × focal length in pixels / detected pixel width
```

For a 100 mm cube:

1. Place the cube face parallel to the camera at a measured distance.
2. Read its detected pixel width.
3. Compute `focal_px = pixel_width × distance_m / 0.1`.
4. Repeat at three distances and use the average.
5. Put the result in the JSON configuration.

This estimate assumes a visible, nearly frontal cube face. Use a ToF or depth
sensor later when accurate close-range distance is required.

## Module contract

Input:

```text
BGR image
camera focal length
HSV and shape parameters
```

Output for each cube:

```text
color
confidence
distance_m
lateral_m
yaw_error_rad
pixel_x
pixel_y
timestamp
```

The standalone tool writes the same fields as the ROS `/cubes` publisher. That
keeps offline tests and the final robot on one detector implementation.

## Consecutive-frame confirmation

The ROS node and standalone tool pass raw detections through the same temporal
filter. A target is published only after it remains near the previous position
for `confirm_frames` consecutive frames. Missing frames produce no stale target
output and restart confirmation. Confirmed positions are smoothed to reduce
chassis oscillation.

The four controls are stored with the other vision settings:

```text
confirm_frames       frames required before a target is trusted (start with 3)
max_missed_frames    frames retained only for re-matching (start with 3)
match_distance_px    maximum position change still considered one target (80 px at 720p)
smoothing_alpha      new-frame weight; lower is smoother but slower (start with 0.4)
```

Use `--raw-detections` with `cube_detector` when tuning HSV and shape filters.
Remove that flag for acceptance tests so the recorded result matches the ROS
robot behavior. JSONL output contains a `tracking` object showing each color's
current streak, missed-frame count, and confirmation state.

## Acceptance gate

Prepare a validation set that was not used to tune thresholds.

- At least 30 orange and 30 purple examples.
- Recall for each color at least 90%.
- No persistent false detection on the floor, robot, or gripper.
- Average processing rate at least 20 FPS on the chosen upper computer.
- Lateral direction is correct in all test positions.
- Distance error is recorded at 0.3 m, 0.5 m, 0.8 m, and 1.0 m.
- The detector stops reporting a target within 0.5 seconds after removal.

Do not connect the detector to chassis motion until this gate passes.

## Batch acceptance from annotated time segments

Use the batch tool when one or more recorded timelines contain several known
target-present or target-absent intervals. The manifest schema is version 1;
the referenced detector JSONL files must use timeline schema version 2.

Create or edit those intervals with an empty local annotation workspace:

```bash
ros2 run cube_perception vision_segment_annotator \
  --workspace results/front_camera_validation \
  --dataset-name "Front camera validation"
```

The command starts a server bound only to `127.0.0.1` and opens the default
browser. Select and submit the original video plus its matching schema v2 JSONL
in the menu. Files are copied only into the local workspace's `uploads` folder;
nothing is sent to the internet.

You may still preload known paths from the command line:

```bash
ros2 run cube_perception vision_segment_annotator \
  --video results/validation.mp4 \
  --jsonl results/validation.jsonl \
  --manifest results/vision_acceptance.json \
  --dataset-name "Front camera validation"
```

Prefer the original H.264 camera MP4 for browser playback. OpenCV currently
writes annotated MP4 with the `mp4v` codec, which common browsers may reject;
the original video and regenerated JSONL still share the same media timeline.

In the menu:

1. Choose the video and matching JSONL, then submit them to the local workspace.
2. Play, pause, or drag the video to the first unambiguous frame.
3. Click **Set current position as start**, or press `[`.
4. Move to the last unambiguous frame and click **Set current position as end**,
   or press `]`.
5. Enter a segment name and select `orange`, `purple`, or `absent`.
6. Add the segment, review the table, and save the manifest.
7. Press `Ctrl+C` in the terminal after annotation is finished.

Opening the same manifest and JSONL again reloads that dataset's existing
segments. Saving updates the matching dataset while preserving other datasets
and the existing default thresholds. Video and JSONL paths are stored relative
to the manifest so the whole results folder can be moved or shared.

Example manifest:

```json
{
  "schema_version": 1,
  "name": "Camera acceptance run",
  "defaults": {
    "min_detection_ratio": 0.9,
    "max_unexpected_detection_ratio": 0.0
  },
  "datasets": [
    {
      "name": "Front-camera video",
      "jsonl": "detections.jsonl",
      "segments": [
        {
          "name": "Orange cube visible",
          "start_s": 2.5,
          "end_s": 7.0,
          "expected": "orange"
        },
        {
          "name": "Empty build area",
          "start_s": 8.0,
          "end_s": 10.0,
          "expected": "absent"
        }
      ]
    }
  ]
}
```

Paths in `jsonl` are resolved relative to the manifest. A segment may override
either default threshold. `orange` and `purple` segments check both the expected
color ratio and the other color's unexpected ratio. An `absent` segment treats
every confirmed detection as unexpected.

Generate the report after building and sourcing the ROS2 workspace:

```bash
ros2 run cube_perception vision_batch_report \
  --manifest results/vision_acceptance.json \
  --output results/vision_batch_report.html
```

The command returns `0` when all segments pass and `2` when any segment fails.
It saves the HTML report in both cases so a remote teammate can inspect the
evidence. Do not include transition frames whose expected state is ambiguous.

## Post-placement observability and camera checklist

The current stability decision assumes that, after the local clearance retreat,
the camera can see the complete build area without the arm or gripper supporting
or obscuring the cube. This assumption has not yet been verified against the
final mechanical design.

An `INCONCLUSIVE` result means that the available evidence is insufficient. It
does **not** mean the physical cube is unstable. A cube may actually remain
stable for three seconds while the robot cannot confirm it because of occlusion,
camera vibration, poor lighting, or an unsuitable viewing angle.

Recommended single-camera arrangement:

- Mount the camera on the upper front of the chassis, above the main gripper
  working height where practical.
- Tilt it downward so that it sees the cube, its support surface, and the
  surrounding placement boundary.
- Keep the arm's normal retract path outside the acceptance region of interest.
- Use the shortest retreat that removes contact and exposes the complete region;
  do not assume a large fixed retreat is necessary.
- Make the camera mount rigid enough that images settle quickly after motion.

Items that must be confirmed with the mechanical team or on site:

- [ ] Camera model, resolution, frame rate, and horizontal/vertical field of view.
- [ ] Camera mounting height, pitch angle, and distance to the build area.
- [ ] Arm and gripper outline at the released and fully retracted positions.
- [ ] Minimum local retreat distance that removes contact and visual occlusion.
- [ ] Whether the complete cube/support contact area is visible after retreat.
- [ ] Placement ROI and the allowed translation/tilt thresholds.
- [ ] Image-settling time caused by chassis or camera-mount vibration.
- [ ] Expected lighting changes, reflections, and competition-field occluders.
- [ ] Maximum acceptable temporary evidence gap, determined from recorded video.
- [ ] Recovery action after `INCONCLUSIVE`: observe again, change view, or stop.

The stability observer uses time rather than frame counts. Its controls are:

```text
placement_stable_duration_s         qualified evidence required (default 3.0 s)
placement_observation_timeout_s     total time before INCONCLUSIVE (default 6.0 s)
placement_max_unavailable_gap_s     tolerated temporary evidence gap (default 0.0 s)
```

A tolerated gap preserves earlier progress but the unseen interval is paused and
does not count toward the required stable duration. A longer gap clears progress.
Keep the default at `0.0` until real recordings establish a defensible value;
then tune it through YAML or launch parameters without changing code.

### Remote validation observation (2026-08-04)

During the Ubuntu VMware mock validation, one of two `INCONCLUSIVE` runs was
preempted by `COMMUNICATION_ERROR` because `RobotStatus` was not delivered for
more than the configured `status_stale_s=0.30` seconds. The immediate rerun
completed correctly at the configured 0.60-second observation timeout. The
normal three-second `STABLE` run and the explicit `FAILED` run were unaffected.

Treat this as a virtual-machine scheduling observation, not yet as proof of a
robot-bridge defect. Do not loosen the real-hardware stale-status threshold from
this single sample. During later load and hardware tests, record the maximum and
percentile intervals between received status messages. If the jitter is unique
to VMware, override the threshold only in the mock launch configuration.
### 网页内处理新视频并生成 HTML 报告

新视频不再需要先在 PowerShell 中单独生成检测时间线。启动标注菜单后：

1. 选择并提交原始视频；
2. 点击“处理新视频并生成检测时间线”；
3. 等待逐帧检测完成，页面会自动关联生成的 schema v2 JSONL；
4. 标注 `orange`、`purple` 或 `absent` 时间段并保存 manifest；
5. 点击“生成 HTML 验收报告”，再点击页面出现的报告链接。

手机录制的 HEVC/H.265 视频会保留为检测输入，并自动生成 H.264/YUV420p 网页预览。
因此浏览器不需要安装 HEVC 扩展。该功能需要在运行标注菜单的 Python 环境安装
`imageio-ffmpeg==0.6.0`；处理按钮执行期间会被禁用，以防止重复任务。

处理区会显示当前阶段、已处理帧数和进度百分比。点击“取消当前处理”后，检测循环或
FFmpeg 转码会尽快停止，未完成的 JSONL/预览会被删除，已经保存的 manifest 不受影响。

标注与报告默认保存在 `--workspace` 指定的目录：

```text
WORKSPACE/vision_acceptance.json   # 人工时间段、期望结果和数据文件路径
WORKSPACE/vision_batch_report.html # HTML 验收报告
WORKSPACE/uploads/                 # 原视频、检测时间线和浏览器预览
```

以后使用同一个 `--workspace` 再次启动，菜单会从 manifest 的最后一个数据集自动恢复
视频、JSONL 和已保存时间段。也可以使用 `--manifest <vision_acceptance.json>` 明确打开。
未点击“保存 manifest”的临时页面修改无法在关闭后恢复。

视频处理使用 `config/vision_default.json`。如需使用另一套现场参数，可在启动菜单时增加
`--config <配置文件路径>`。视频、JSONL、manifest 和 HTML 都保存在本地工作目录，不会上传互联网。
