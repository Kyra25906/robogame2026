# Windows 本地 Python 环境说明

> 适用：在 Windows 上跑视觉、单元测试、文档生成时，该用哪个 Python、怎么跑。
> 日期：2026-08-13

## 一、两个 Python，各管各的

这台 Windows 电脑上有两个**独立**的 Python，各自的第三方包互不影响：

| 位置 | 版本 | 有 OpenCV(cv2)？ | 用途 |
|---|---|---|---|
| `D:\python.exe` | 3.14.3 | ✅（4.13.0，还有 imageio_ffmpeg） | **视觉 + 测试 + 文档脚本——本项目 Windows 侧统一用这个** |
| PATH 上的 `python`（`AppData\...\Python311\python.exe`） | 3.11.9 | ❌ | 系统默认，其他用途（数模等） |

唯一要小心的是：**裸敲 `python` / `pip` 时，系统按 PATH 找到的是 3.11.9（没有 cv2）**。所以视觉相关命令必须明确写 `D:\python.exe`，否则会报 `ModuleNotFoundError: No module named 'cv2'`。

## 二、约定

- Windows 上跑本项目相关 Python 一律用 **`D:\python.exe`**，不要裸敲 `python`。
- 示例：`D:\python.exe -B -m unittest discover -s tests`（并需把 `ros2_ws/src/*/` 加入 `sys.path`，见 GETTING_STARTED.md 的 "Core tests without ROS"）。

## 三、为什么本项目不用 venv

本项目有"两个世界"，依赖管理方式不同：

1. **ROS2 主体（Ubuntu / 树莓派）**：用 ROS2 自带环境系统——`package.xml` 声明依赖、`rosdep` 安装、`colcon` 编译、`source setup.bash` 激活。**不用也不该用 venv**（venv 会隔离掉 ROS2 自带的 `rclpy` 等包）。
2. **Windows 辅助（视觉/测试/文档）**：纯 Python、不碰 ROS2，本可建 venv + `requirements.txt`，但当时是临时增量开发、未建。目前靠"统一用 `D:\python.exe`"这条约定兜底。

## 四、结论

| 部分 | 该不该用 venv | 现状 |
|---|---|---|
| ROS2 主体（Ubuntu） | 不该用，ROS2 有自己的环境系统 | 正确 |
| Windows 视觉/测试 | 应该用（或至少 requirements.txt） | 缺失，暂用约定兜底 |

如需规范化，最小改动是加一个 `requirements.txt`（opencv-python、imageio-ffmpeg、numpy），再用 `D:\python.exe -m venv .venv` 建环境。
