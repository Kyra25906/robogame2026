cmake_minimum_required(VERSION 3.8)
project(robogame_interfaces)

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/RobotStatus.msg"
  "msg/CubeDetection.msg"
  "msg/CubeDetectionArray.msg"
  "msg/MissionState.msg"
  "msg/CargoState.msg"
  "srv/ExecuteMechanism.srv"
  "srv/SetLiftHeight.srv"
  DEPENDENCIES builtin_interfaces geometry_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()

