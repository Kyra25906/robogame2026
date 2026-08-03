# RoboGame 2026 robot software

ROS 2 workspace for the one-month two-person sprint. The repository is split into
hardware-independent logic (`robogame_core`) and thin ROS 2 nodes so protocol,
navigation, and mission behavior can be tested without a robot.

See [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) for Ubuntu setup and the
first-week bring-up procedure.

## 九个功能包使用说明

- [cube_perception](ros2_ws/src/cube_perception/README.md)：视觉识别、HSV 调参、ROI 与连续帧确认。
- [localization](ros2_ws/src/localization/README.md)：轮式里程计、IMU 输入、统一位姿和 TF。
- [motion_control](ros2_ws/src/motion_control/README.md)：固定目标点控制、限速、超时和越界保护。
- [manipulator_client](ros2_ws/src/manipulator_client/README.md)：视觉对准与抓取、升降、释放协调。
- [mission_manager](ros2_ws/src/mission_manager/README.md)：总任务状态机、路点调度、重试和安全停止。
- [robot_bridge](ros2_ws/src/robot_bridge/README.md)：ROS2 与电控串口、底盘和机构服务之间的桥接。
- [robogame_interfaces](ros2_ws/src/robogame_interfaces/README.md)：全系统共用消息和服务合同。
- [robogame_core](ros2_ws/src/robogame_core/README.md)：不依赖硬件的导航、视觉、任务和协议纯逻辑。
- [robogame_bringup](ros2_ws/src/robogame_bringup/README.md)：统一参数和整套系统启动入口。
