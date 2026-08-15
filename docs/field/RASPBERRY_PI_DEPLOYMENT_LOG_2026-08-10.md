# RoboGame2026 树莓派与真车部署日志

## 记录规则

- 只记录已经由截图、命令输出或实物检查确认的事实。
- 推测、计划和待确认事项必须明确标注，不与已验证事实混写。
- 不记录 Wi-Fi 密码、登录密码、令牌或其他敏感信息。
- 不把照片中的线色当作接线定义，不猜测 STM32 协议字段。
- 树莓派不作为长期项目代码修改环境；发现问题后保存日志和真实数据，回代码开发对话修改、测试并提交，再部署新 commit。

## 2026-08-10：microSD 与写卡工具准备

### 已验证事实

- 树莓派目标硬件：Raspberry Pi 4B，4GB RAM。
- 目标系统路线：Ubuntu Server 24.04 LTS，64-bit ARM（arm64/aarch64），后续安装 ROS 2 Jazzy。
- microSD：SanDisk Ultra 32GB，microSDHC、UHS-I、A1。
- 读卡工具：C286 microSD 读卡器。
- Windows 磁盘管理确认结果：
  - 磁盘编号：磁盘 1；
  - 设备类型：可移动；
  - 容量：29.72GB；
  - 盘符：`E:`；
  - 文件系统：FAT32；
  - 分区：一个状态良好的主分区。
- 已完成一次 Windows 安全弹出，未在磁盘管理中执行格式化、初始化、删除卷或新建卷。
- 已从 Raspberry Pi 官方网站安装并打开 Raspberry Pi Imager `v2.0.10`。
- Imager 中已确认目标设备型号：`Raspberry Pi 4`。
- Imager 中已找到并确认目标系统选项：`Ubuntu Server 24.04.4 LTS (64-bit)`，在线下载约1.2GB。
- 重新插入读卡器后，Imager 显示唯一候选存储设备：
  - 名称：`Mass Storage Device USB Device`；
  - 容量：29.7GB；
  - 挂载盘符：`E:\`。
- Imager 的29.7GB、`E:`设备与磁盘管理中的磁盘1/29.72GB/`E:`一致，可确认为目标microSD。
- 参赛队伍：第26队。
- Imager 已接受自定义主机名：`robogame-t26-rpi4`；计划使用的局域网名称为 `robogame-t26-rpi4.local`。
- Imager 本地化设置已确认：地区代表城市 `Beijing (China)`、时区 `Asia/Shanghai`、键盘布局 `us`。
- Imager 用户账户已配置：用户名 `rg26`；密码已由用户本地设置，未记录密码内容。
- Imager Wi-Fi 设置已配置；未记录SSID或密码等敏感信息。
- Imager 已启用SSH，并选择密码认证；计划首次连接命令为 `ssh rg26@robogame-t26-rpi4.local`。
- 写入前汇总页已确认显示：Raspberry Pi 4、Ubuntu Server 24.04.4 LTS (64-bit)、`Mass Storage Device USB Device`，并列出主机名、本地化、用户、Wi-Fi和SSH均已配置。
- 用户报告 Raspberry Pi Imager 写入流程已完成；Ubuntu Server 24.04.4 LTS (64-bit) 镜像已写入目标microSD，并由Imager完成流程校验。
- microSD已从Windows/C286读卡器安全移除，目前尚未插入树莓派。
- 已通过板卡正反面照片确认实物为 `Raspberry Pi 4 Model B`；板上可见 `Raspberry Pi 4 Model B 2018` 丝印。
- 板卡照片中未见明显烧焦、腐蚀、金属异物或GPIO针脚互相接触；microSD卡槽外观可见。
- 电源适配器铭牌可见：型号 `0530`、输入 `100–240VAC 50/60Hz`、输出 `5V⎓3A`。
- 当前照片中未安装风扇或散热片；此前“有风扇”仅表示配件可能存在，风扇实物、接线和安装状态尚未核验。
- 电源线末端照片不足以确认是否为 Raspberry Pi 4 所需的 USB-C 插头，暂不连接或上电。
- 用户已确认5V/3A电源及其物理接口可用于本机；首次上电前仍保持断电。
- 已找到散热套件：两线小风扇、红色两针插头、金属散热支架、蓝色导热垫和安装紧固件。
- 当前照片未显示风扇额定电压或明确引脚定义；不得仅凭红/黑线色接入GPIO，风扇电气连接暂缓。
- 用户已确认该两线风扇要求GPIO排针5V供电。按Raspberry Pi官方针脚定义，候选连接为红线到物理针脚4（5V）、黑线到物理针脚6（GND）；尚未完成插头朝向实物复核，因此仍未接线。
- 已通过近距离照片复核风扇接头：顶部针脚2保持露出，红线连接物理针脚4（5V），黑线连接物理针脚6（GND）；电气位置正确。风扇尚未完成机械固定，因此仍不得上电。
- 用户报告已完成风扇机械安装；尚未通过上电观察验证风扇转动、风向、噪声或结构稳定性。
- 用户报告已连接显示器；尚未首次上电，显示输出未验证。
- 用户报告已在断电状态插入完成Ubuntu写入和校验的SanDisk 32GB microSD；插卡状态尚未通过首次启动验证。
- 首次最小化上电观察：风扇转动，红灯稳定常亮，绿灯闪烁；未报告焦味、烟雾或异常发热。该灯态支持“供电稳定且microSD存在访问活动”，但尚不足以单独证明Ubuntu已完成启动。
- 首次上电时显示器呈白屏，尚未确认是Ubuntu首次初始化、HDMI输入/线缆问题还是显示设备兼容问题。
- 首次上电期间，用户手机热点报告有设备新连接；这支持树莓派已启动并应用预设Wi-Fi配置，但尚未通过IP、ping或SSH确认设备身份。
- 手机热点已连接设备列表明确显示主机名 `robogame-t26-rpi4`，MAC地址 `98:fe:54:31:1b:9f`；另有Windows设备 `dahlia` 同时连接。由主机名完整匹配，可确认树莓派已启动并成功接入预设手机热点。
- 一次Windows ping使用了拼写错误的主机名 `robogame-t26-roi4.local`（正确应为 `robogame-t26-rpi4.local`），并解析到 `198.18.0.111`、0%丢包。由于错误名称仍获得响应且该地址位于常见代理Fake-IP范围，此结果判定为无效诊断，不记录为树莓派真实IP或在线证明。
- Windows `arp -a` 暂未出现树莓派MAC `98-fe-54-31-1b-9f`。热点网络接口地址为 `10.101.192.18`，另有Clash虚拟接口 `198.18.0.1`；缺少ARP条目说明Windows尚未与树莓派真实地址直接通信，不代表树莓派离线。
- 完全退出Clash后，Windows对正确名称 `robogame-t26-rpi4.local` 的IPv4 ping提示找不到主机；判定为Windows当前缺少可用`.local`名称解析，不判定为树莓派离线。
- 通过热点网段邻居发现和已知MAC精确匹配，树莓派本次动态IPv4地址确认为 `10.101.192.213`，对应MAC `98-fe-54-31-1b-9f`。该地址由手机热点DHCP分配，重连后可能变化。
- 首次SSH登录成功：`ssh rg26@10.101.192.213`，提示符为 `rg26@robogame-t26-rpi4:~$`。
- SSH欢迎信息实测确认：
  - 系统：Ubuntu 24.04.4 LTS；
  - 内核：`6.8.0-1047-raspi`；
  - 架构：`aarch64`；
  - 根分区：28.68GB，已用7.4%，说明镜像根文件系统已扩展到目标microSD；
  - 内存使用率：6%；Swap使用率：0%；
  - CPU温度：37.0°C；
  - `wlan0` IPv4：`10.101.192.213`；
  - 系统时间：2026-08-10 17:41:46 CST；
  - 系统提示有132个标准安全更新可用。
- 基础系统安装、首次启动、Wi-Fi和SSH验收通过。显示器仍为纯白，作为独立HDMI显示问题暂存，不阻塞无头SSH部署。
- 客服截图显示该显示设备可能是需要专用驱动的3.5英寸LCD，说明面向Raspbian，并要求执行 `LCD35-show` 类脚本。此前将其暂称为“HDMI显示问题”不够准确；实际接口类型（HDMI或GPIO/SPI）和准确型号尚待实物确认。
- 客服命令包含 `sudo rm -rf LCD-show`、从GitHub克隆第三方仓库、递归改权限及以sudo执行安装脚本。由于当前目标系统为Ubuntu Server 24.04.4 arm64，而说明针对Raspbian，当前禁止执行这些命令，以免修改启动配置、内核模块或破坏已验证系统。
- 屏幕驱动只读调研结果：
  - 客服所示 `LCD35-show` 与 goodtft/lcdwiki 驱动族一致，其中 `LCD35-show` 对应3.5英寸RPi Display（仓库称MPI3501），但尚未由实物型号标签确认本屏就是MPI3501；
  - goodtft主仓库将该流程列在Raspbian安装说明下，并把Ubuntu引向单独的 `lcdwiki/LCD-show-ubuntu` 仓库；
  - `LCD-show-ubuntu` 的公开说明最后明确更新到Ubuntu 20.04（2020-09-27），项目简介面向早期Raspberry Pi/Pi 2/Pi 3，没有看到Ubuntu 24.04、Raspberry Pi 4与当前6.8内核的明确兼容声明；
  - 驱动仓库包含boot、etc、usr文件、系统备份/配置/恢复脚本以及特定版本的Xorg输入包，执行安装脚本会自动重启，属于高影响系统修改；
  - Ubuntu Server默认没有桌面环境，即使SPI帧缓冲驱动成功，目标也应先定义为显示Linux控制台，而不是假定会出现图形桌面；
  - GPIO/SPI屏可能占用40针排针和SPI/触摸相关资源，必须与风扇供电、后续STM32串口及其他GPIO规划共同核对。
- 当前屏幕处理决策：正式Ubuntu卡上不直接运行客服脚本；SSH继续作为主管理通道。若必须验证屏幕，优先在备用microSD/系统副本上做隔离实验，或改用标准HDMI显示器。
- 根据用户提供的26针蓝色背板图片与LCDwiki资料，当前屏幕高度疑似 `MPI3501 3.5inch RPi Display`，但仍以“待实物丝印最终确认”标记。MPI3501公开规格为：ILI9486显示控制器、XPT2046电阻触摸控制器、SPI最高32MHz、480×320、约0.13A@5V。
- MPI3501公开接口表显示主要占用：物理针脚11（触摸IRQ）、18（LCD RS）、19（SPI MOSI）、21（触摸MISO）、22（复位）、23（SPI时钟）、24（LCD CS）、26（触摸CS），以及电源/地；物理针脚8和10标为NC，因此UART TX/RX理论上未被该屏电气占用，但26针插座会物理遮挡排针，后续可能需要透传/转接。
- Linux内核存在ILI9486 TinyDRM类驱动，说明“避免旧LCD-show整包脚本、改用内核驱动与最小设备树配置”在技术上有探索价值；当前Ubuntu 6.8 raspi内核是否实际启用该模块及是否附带匹配overlay仍待只读检查。
- 外网软件源探测：`archive.ubuntu.com` 成功解析为IPv6 `2620:2d:4000:1::103`；4次ping收到3次，丢包25%，往返延迟约410.847–539.052ms，平均465.944ms。外网可达但当前IPv6链路质量偏差，暂不开始132项系统更新。
- IPv4软件源探测：`archive.ubuntu.com` 解析为 `91.189.91.81`；4次ping全部收到，0%丢包，延迟约316.493–451.626ms，平均385.892ms。IPv4稳定性明显优于当前IPv6，虽延迟偏高但可用于软件包索引更新。
- `sudo apt update` 成功完成：`noble` 与 `noble-security` 软件源均正常命中，无DNS、签名或仓库错误；刷新后显示130个软件包可升级。
- `sudo apt upgrade` 在确认前完成升级规模计算：130个软件包升级、2个新装、0删除、0保留；全部为标准LTS安全更新。预计下载867MB，额外占用222MB；包含新内核 `linux-image-6.8.0-1060-raspi` 和对应模块。尚未确认安装。
- 用户已在APT确认提示选择继续，867MB系统安全更新现已进入下载/安装流程；当前状态为进行中，禁止断电、拔卡、切断热点或启动其他安装任务。

### 尚未执行

- 已在Imager中选择目标存储设备，并已进入自定义设置。
- 主机名、本地化、用户、Wi-Fi和SSH均已配置。
- microSD镜像写入、树莓派首次启动及SSH基础验收均已完成。
- 尚未把microSD插入树莓派，也未给树莓派首次上电。
- 尚未安装ROS 2 Jazzy或部署任何Git commit。
- 尚未连接摄像头、STM32或任何执行器。
- 尚未启动`robot_bridge`、launch文件或任何真车控制流程。

### 当前安全状态

- STM32未连接。
- 底盘、夹爪、升降机构不得接通动力。
- 未运行任何可能让小车运动的程序。

### 下一验收点

保持树莓派5V供电、风扇和手机热点稳定，等待APT完整结束。若出现配置选择、错误或SSH断开，保留现场并按具体状态处理；成功返回shell后先检查结果，不立即重启。

## 2026-08-11：统一集成版本树莓派无硬件部署验收

> 本节记录后续实际结果，并取代上文“尚未安装ROS 2/尚未部署代码/系统升级进行中”等较早状态描述。历史内容保留用于追溯当时进度。

### 系统与工具链复核

- 系统服务状态：`running`。
- 软件包审计：`sudo dpkg --audit` 无输出。
- 重启标记：`NO_REBOOT_REQUIRED`。
- 操作系统：Ubuntu 24.04.4 LTS，代号 `noble`。
- 当前内核：`6.8.0-1060-raspi`。
- 架构：`aarch64` / Ubuntu包架构 `arm64`。
- Python：`3.12.3`。
- ROS发行版：Jazzy；`/opt/ros/jazzy/setup.bash` 存在并可正常加载。
- 根分区：29GB，已用3.6GB，可用24GB，使用率13%。
- 内存：总计3.7GiB，可用约3.4GiB；未配置Swap。
- CPU温度：33.1°C。
- Git、colcon、rosdep与Python 3均可用。
- rosdep已完成系统初始化和用户缓存更新；由于手机热点IPv6访问GitHub超时，初始化期间曾临时关闭运行时IPv6。重启后该临时设置自动失效，未写入永久系统配置。

### 代码传输与版本追溯

- GitHub仓库：`https://github.com/Kyra25906/robogame2026.git`。
- 远程集成分支：`integration/robogame-t26`。
- 初始部署commit `5923d900e02be849849a4e96c8b44a5ac53fc60b` 在树莓派执行 `rosdep check` 时发现8个Python包错误声明 `<buildtool_depend>ament_python</buildtool_depend>`，因此停止部署；未用 `--skip-keys` 绕过，未在树莓派修改源码。
- 初始失败版本目录 `/home/rg26/robogame_deploy_5923d90` 与bundle `/home/rg26/robogame_5923d90.bundle` 保留用于追溯。
- 修复后的唯一部署commit：`2d70649ac3c30e0cd754dca1a0318fbc3042b396`。
- 修复提交说明：`fix(manifests): remove invalid ament_python rosdep keys`。
- 由于树莓派直连GitHub不稳定，在Windows通过独立干净仓库生成Git bundle，并完成 `git bundle verify`；再通过SCP传至树莓派。
- 新bundle：`/home/rg26/robogame_2d70649.bundle`。
- 新部署目录：`/home/rg26/robogame_deploy_2d70649`。
- 以detached HEAD检出完整SHA；最终 `git rev-parse HEAD` 与批准SHA完全一致。
- 构建、测试完成后的 `git status --short` 无输出，正式源码与配置保持干净。

### 依赖、构建与软件验收

- 新commit的 `rosdep check` 不再出现 `ament_python` 无法解析错误。
- 首次检查仅缺少真实系统依赖：`ros-jazzy-cv-bridge`、`python3-opencv`。
- 通过rosdep正常安装上述依赖；未使用 `sudo pip` 或其他绕过方式。
- 安装后复查结果：`All system dependencies have been satisfied`。
- ROS 2源码包数量：9。
- 包清单：`cube_perception`、`localization`、`manipulator_client`、`mission_manager`、`motion_control`、`robogame_bringup`、`robogame_core`、`robogame_interfaces`、`robot_bridge`。
- ARM64构建命令：`colcon build --symlink-install`。
- 构建结果：`Summary: 9 packages finished [1min 41s]`，无失败或中止。
- 加载新工作区后，9个包均通过 `ros2 pkg prefix` 检查；`localization`实际路径为 `/home/rg26/robogame_deploy_2d70649/ros2_ws/install/localization`。
- 配置验证：`CONFIG PASS: errors=0 warnings=0`。
- 单元测试：`Ran 231 tests in 2.418s`，最终结果 `OK`。
- 测试中出现的非法 `.data` 报告格式usage/error属于预期负向用例；最终测试集仍为231/231通过。

### 最终安全审计

- 最终完整HEAD：`2d70649ac3c30e0cd754dca1a0318fbc3042b396`。
- 最终工作树：干净，`git status --short` 无输出。
- 最终ROS 2源码包数量：9。
- `pgrep -af 'robot_bridge|ros2 launch|mechanism_acceptance'` 未发现相关进程，输出 `NO HARDWARE-RELATED PROCESS`。

## 2026-08-14 更新部署与真实STM32通信

- 当前有效部署 commit：`2d9514d5b3ad1d2bab91a292e3c20bae730cacc9`；
- 当前部署目录：`/home/rg26/robogame_deploy_2d9514d`；
- 离线 bundle：`/home/rg26/robogame_2d9514d5b3ad.bundle`；
- 旧 `/home/rg26/robogame_deploy_2d70649` 保留为回退，不覆盖；
- ARM64九包构建、配置和测试由现场执行并报告通过；明确输出 `CONFIG PASS: errors=0 warnings=0`；
- 源码与install后的field配置均固定使用
  `/dev/serial/by-id/usb-STMicroelectronics_STM32_Virtual_ComPort_307A39653433-if00`；
- 用户 `rg26` 已加入 `dialout`，可正常打开STM32 VCP；
- 真实safe-suite：ACK通过、STATUS约51Hz、看门狗clear/set通过、非法载荷0；ODOM和IMU均为0帧；
- 正式 `robot_bridge` 解码真实STATUS：communication_ok=true、physical_start=false、
  error_code=3001、imu_valid=false、boot_id=1；
- 曾发现残留mock与real `robot_bridge` 同时发布 `/robot/status`；清理后完成真实状态复验，最终无残留进程；
- 本日未烧录STM32、未发送真实非零速度、未进行底盘或机构动作。
- 未连接STM32。
- 未启动`robot_bridge`。
- 未运行任何整车launch。
- 未发布`/cmd_vel`，未调用夹爪、升降、STOP或RETREAT服务。
- 底盘、夹爪和升降机构未接通动力，小车未运动。

### 本轮正式结论

- `ROSDEP PASS`
- `ARM64 BUILD PASS`
- `SOFTWARE TEST PASS`
- `NO-HARDWARE PASS`
- `HARDWARE NOT RUN`
- `REAL-CAR NOT RUN`

### 下一验收点（更新）

本轮仅完成树莓派ARM64无硬件部署验收。下一阶段仍需单独制定首次STM32连接、串口只读观察、STOP/急停/失联安全验证顺序；在双方确认安全清单前，不连接STM32、不启动`robot_bridge`、不运行整车launch，也不给执行器接通动力。
