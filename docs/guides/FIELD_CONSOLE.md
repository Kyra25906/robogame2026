# 单终端现场联调控制台

## 目的

`tools/field_console.py` 把底盘、机械臂和巡线相关 ROS 2 进程放在一个终端管理，
并给每行输出加模块标签。它解决“同时开很多终端、分不清日志属于谁”的问题。

控制台**不会发送速度、抓取、升降等动作命令**。真实硬件动作仍按现场检查清单单独授权和执行。

## 启动

必须在仓库根目录运行（树莓派上是 `~/robogame`）：

```bash
cd ~/robogame
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
python3 tools/field_console.py
```

也可以进入控制台时启动一个分组：

```bash
python3 tools/field_console.py --group base
```

## 常用命令

```text
status                  查看全部进程状态和 PID
start bridge            只启动串口桥接
start-group base        依次启动 bridge、localization、motion
start-group arm         启动机械臂客户端（bridge 需已运行）
start-group line        启动巡线控制器（bridge 需已运行）
start-group observe     观察状态/里程计频率以及底盘、机械臂、巡线结果
focus bridge            屏幕只显示 bridge 的新日志
focus all               恢复显示全部新日志
tail bridge 30          重看 bridge 最近 30 行日志
stop line               停止巡线控制器
quit                    停止由控制台启动的全部进程并退出
```

进程定义位于 `tools/field_console.json`。如果现场仓库路径不一致，无需修改命令；
配置使用仓库根目录下的相对路径。控制台不会清理在其他终端启动的旧实例，启动前仍应先确认没有残留 ROS 2 节点。

## 推荐联调顺序

1. 架空车轮、机构卸载，独立确认物理急停有效。
2. `start bridge`，看到握手完成后检查 `status`。
3. 使用 `start-group observe` 集中观察关键话题；输出太多时用 `focus` 切换。
4. 底盘联调使用 `start localization`、`start motion`。
5. 机械臂联调使用 `start-group arm`。
6. 巡线联调使用 `start-group line`。
7. 日志太多时用 `focus`，需要回看时用 `tail`。
8. 结束时用 `quit`，控制台会停止由它启动的全部子进程。

## 当前边界

- 这是进程与日志控制台，不是 ROS 2 图形仪表盘。
- 它能显示节点是否退出，不能仅凭“进程在运行”证明话题频率、通信质量或真实机构正常。
- 巡线真实 `0x14` 遥测协议尚未冻结时，巡线节点仍只能使用当前可用的数据源。
