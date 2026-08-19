# 现场上车 Checklist（2026-08-18 联调复盘沉淀）

> 用途：每次上车联调前/中/后对照执行，避免重复踩坑。
> 来源：2026-08-18 树莓派真车联调复盘——当晚问题归为 4 类，操作类占 ~40%。
> 关联：`docs/field/RASPBERRY_PI_DEPLOYMENT_LOG_2026-08-18.md`（当晚详细记录）。

---

## 0. 四类问题总览（先看这个）

| 类 | 占比 | 本质 | 对策 |
|---|---|---|---|
| **① 操作/环境** | ~40% | 没有标准流程，靠临时记忆 | 本 checklist |
| **② 我们代码的真实时序 bug** | ~30% | mock 测不出真车实时性/超时 | 提前按真车保守值 + 保活独立化 |
| **③ 固件/硬件** | ~20% | 参数未标定、硬件不稳 | 提前要基线 + 及时报电控 |
| **④ ROS/DDS 环境** | ~10% | FastDDS 投递问题、环境脏 | 验证后换 RMW |

---

## 1. 上车前（10 分钟准备）

- [ ] **向电控要硬件基线**：轮径/编码器参数（PPR/减速比）、IMU/电压检测是否接入、odom 是否稳定——**避免像「1m 走 10cm」那样上车才发现**
- [ ] 确认树莓派 IP / SSH 方式（手机热点 IP 动态，重连后要重新找）
- [ ] 打印本 checklist
- [ ] 准备测试脚本（**50Hz 发送**，`time.sleep(0.02)`，**时长 ≥20 秒**）

## 2. 启动节点（严格按顺序，单实例）

- [ ] **启动前**：`ps aux | grep <节点>` 确认**无残留**，有就 `kill`——**双实例会互相打架**（本次多次踩坑）
- [ ] **顺序**：robot_bridge → localization → motion_control（**依赖顺序，不能乱**）
- [ ] 每个终端**贴标签**（RB / LOC / MC / CMD），避免「终端 3 是哪个」
- [ ] 每终端先 `source`：`cd ~/robogame/ros2_ws && source /opt/ros/jazzy/setup.bash && source install/setup.bash`
- [ ] robot_bridge 等 `handshake complete` 再启动下一个
- [ ] 启动后 `ros2 topic list` 确认话题全（/robot/status、/wheel_odom、/pose、/motion/goal、/motion/result、/cmd_vel）

## 3. 测试中（每步确认）

- [ ] **授权**：`ros2 topic echo /robot/status --once | grep physical_start` → true（false 就长按 PB2）
- [ ] **发命令用 50Hz + 长时长**（不是 `--once` 一帧、不是 10Hz）
- [ ] **改配置后验证**：`ros2 param get <节点> <参数>` 确认生效（避免「改了没生效」）
- [ ] 发 goal 同时**盯 /motion/result**（SUCCESS / LOCALIZATION_ERROR / COMMUNICATION_ERROR 区分问题）
- [ ] 观察车：**连续走 / 走走停停 / 走几步停**——三种不同的病
- [ ] **架空 vs 落地分开测**：架空只能验方向/链路；距离/直线性必须落地

## 4. 遇到问题先分类（再动手）

| 现象 | 先查 | 属于哪类 |
|---|---|---|
| 车不动 / 命令没反应 | 节点进程在吗？`ps aux` | ① 或 ② |
| 走走停停 | `error_code` 是否 4001；`/cmd_vel` 是否偶发零 | ② |
| LOCALIZATION_ERROR | `/pose` hz 是否断流（max 间隔） | ③ 或 ② |
| 方向反 / 走不准 | 轮径/编码器参数 | ③ |
| goal 到了订阅但车不动 | DDS 投递（Python 直发验证） | ④ |
| 配置改了对不上 | 双实例 / 路径 / 没重启 | ① |

## 5. 收尾

- [ ] **记录**：现象 + 根因 + 修复，写入 `RASPBERRY_PI_DEPLOYMENT_LOG_YYYY-MM-DD.md`
- [ ] **报电控**：固件/硬件问题及时上报（odom 跳变、重启、编码器、电压、轮径）
- [ ] **提交 git**：代码/配置改动 commit + push（push 需手动，HTTPS 凭证）

---

## 6. 我们代码侧的长效修复（已做 + 待做）

### 已做（2026-08-18）
- ✅ 心跳独立于 communication_ok（`801bc44`）——odom 跳变不再导致看门狗误触发
- ✅ command_timeout_s 0.15→0.5（`d5cca56`）——容忍负载偶发延迟，不再插零速
- ✅ pose_stale_s 0.25→1.5（`2d9522e`）——容忍固件 odom 断流（临时缓解）

### 待做（下次）
- [ ] DDS 投递验证：Python 直发 → 不行换 CycloneDDS（见部署日志 7.6）
- [ ] E3 落地标定：直行 1m/横移 1m/转 360° ×10（等固件 odom 修好 + 轮径确认）
