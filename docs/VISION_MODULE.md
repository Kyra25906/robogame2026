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
