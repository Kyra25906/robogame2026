"""B1: 相机驱动 launch（640x480 @ 30fps MJPG）。

用法：
    ros2 launch robogame_bringup camera.launch.py            # 单独起相机
    ros2 launch robogame_bringup hardware.launch.py          # 已 include 本文件

设计依据（执行队列 2026-08-18 A3/B1）：
- 工作分辨率 640x480 @ 30fps（MJPG）——08-15 性能测试证明 1280x720 在
  树莓派 4B 只有 ~13fps、CPU 270%，扛不住；
- 驱动用 usb_cam（ros-jazzy-usb-cam），``pixel_format:=mjpeg2rgb``；
  v4l2_camera 的 MJPG 有 bug 不可用；
- 焦距 1275.0 = 2550 x (640/1280)，线性缩放，内参无需重标定；
- CameraInfo 经静态标定 yaml 提供，cube_perception 的 ``_on_camera_info``
  读 ``k[0]`` 覆盖 fallback（node.py:106-108）。

现场依赖（B2，非本文件）：装 ros-jazzy-usb-cam、确认 USB 设备路径、
相机安装高度/俯仰/遮挡（ISSUE-002）后冻结。

@reference https://deepwiki.com/ros-drivers/usb_cam/4-configuration-guide
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory("robogame_bringup")
    # 静态相机内参（焦距 1275.0 @ 640x480），image_proc 标定文件格式。
    camera_info = os.path.join(share, "config", "gf100_camera_640x480.yaml")

    camera = Node(
        package="usb_cam",
        executable="usb_cam_node_exe",
        name="usb_cam",
        output="screen",
        parameters=[{
            "video_device": "/dev/video0",
            "image_width": 640,
            "image_height": 480,
            "pixel_format": "mjpeg2rgb",
            "framerate": 30.0,
            "camera_info_url": f"file://{camera_info}",
        }],
        # 便于现场按需启停：remap 到任务约定的话题名。
        remappings=[
            ("/image_raw", "/camera/image_raw"),
            ("/camera_info", "/camera/camera_info"),
        ],
    )
    return LaunchDescription([camera])
