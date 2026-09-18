# Ubuntu + ROS 2 quick start

## Supported baseline

- Ubuntu 24.04 with ROS 2 Jazzy. This is the only supported target: the Raspberry Pi and the Ubuntu VM use `/opt/ros/jazzy`, and 7 of the 9 package READMEs name it today (`cube_perception`, `robot_bridge`, `localization`, `robogame_bringup`, `motion_control`, `robogame_core`, `robogame_interfaces`); `mission_manager/README.md` and `manipulator_client/README.md` do not name a distro. (An earlier revision of this page offered Ubuntu 22.04 + Humble; nothing in the repo is validated on Humble.)
- Python 3, `python3-opencv`, `cv_bridge`, and `pyserial`.
- USB UVC camera and a USB virtual serial link to the MCU.

## Build

```bash
cd ros2_ws
source /opt/ros/$ROS_DISTRO/setup.bash
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
```

Run the hardware-free roof-tower demonstration:

```bash
ros2 launch robogame_bringup mock_demo.launch.py
ros2 topic echo /mission/state
```

Run the single-cube fallback profile:

```bash
ros2 launch robogame_bringup single_cube.launch.py
```

`hardware.launch.py` drives the real MCU, so treat it as a hardware operation: keep
the wheels off the ground and the mechanisms unloaded. The V1 frame mapping now lives
in `docs/field/STM32_SERIAL_PROTOCOL_V1.md` and is implemented in
`ros2_ws/src/robogame_core/robogame_core/serial_protocol.py` (the old
`docs/field/MCU_PROTOCOL.md` was deleted — its message IDs are obsolete, do not cite it).

<!-- docs-audit: allow path-missing docs/field/MCU_PROTOCOL.md -->
<!-- 上面这行是给 tools/docs_audit.py 看的显式豁免：本行**就是在报告**该文件已被删除。 -->

It has been run on the real car once, on 2026-08-18: HELLO→ACK→READY was validated
(`type=0x13`), which covers the handshake only — not the mechanism command payloads.

## Core tests without ROS

```bash
# Run from the repository root (the directory that contains ros2_ws).
# PYTHONPATH must cover every package under ros2_ws/src, not just a couple of them.
PYTHONPATH="$(find ros2_ws/src -mindepth 1 -maxdepth 1 -type d | paste -sd:)" \
python3 -m unittest discover -s tests -v
```

Listing only two packages (an earlier revision of this page used
`PYTHONPATH=ros2_ws/src/robogame_core:ros2_ws/src/cube_perception`) does **not** work:
`tests/test_localization.py` imports `localization.quality` and
`tests/test_line_follow_runner.py` imports `motion_control.line_follow_runner`, so
discovery reports those modules as errors with
`ModuleNotFoundError: No module named 'localization'`. `tools/run_tests.py` builds the
same all-package path automatically.

## First bring-up sequence

1. Keep wheels off the ground and mechanisms unloaded.
2. Verify the physical emergency stop independently of software.
3. Start only `robot_bridge` in mock mode and inspect `/robot/status`.
4. Switch to real serial mode; verify heartbeat and zero-speed behavior.
5. Send small `vx`, `vy`, and `wz` commands separately.
6. Confirm odometry axis signs and SI units.
7. Test grab, release, and lift services with low current limits.
8. Start localization and motion control; test a 0.2 m goal.
9. Start the camera and tune HSV values from recorded images.
10. Only then start the mission manager.
