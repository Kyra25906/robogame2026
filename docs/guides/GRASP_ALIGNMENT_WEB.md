# 抓取对准：网页怎么看（不开额外终端）

## 一句话

方块检测 → **原地转向 → 沿车头轴前进 → 到位**，全过程在联调网页的「抓取对准」卡片里看，
不需要 `ros2 topic echo`，也不需要另开终端。

## 为什么改成"转向 + 前进"（不是横移）

真车 **VY 恒 0**（无横移，电控硬要求）。原来的对准律下发 `linear_y`，在真车上等于没命令。
现在：

| 相位 | 车做什么 | 为什么 |
|---|---|---|
| `TURN` | 原地转向，`vx` 强制为 0 | 横向偏差只能靠转向消掉 |
| `APPROACH` | 沿车头轴前进/后退 | 目标已在前方轴上，改距离 |
| `READY` | 停住，可以抓 | 两个方向都进容差 |

**安全不变式**：横向偏差超容差时 `vx` 必为 0（有专门测试守住）。绝不带偏压过去。

**为什么没有"接近轴偏角"参数**：设爪朝侧面（γ=90°）时，前进既改不了横向偏差，
旋转在目标位于正侧方时对横向偏差的增益又趋近于 0 —— 数学上会卡死（等于平行泊车的奇异位形）。
所以视觉对准**只把方块送到车头正前方**，侧向够取交给云盘。这条约束做成了"参数不存在"，
不是文档里的一句提醒。

## 网页上的两块内容

1. **实时面板**（上半部分）：读 `/cubes` 最新一帧，显示
   检测（前向/画面横向/置信度/颜色）、换算结果（沿轴距离、横向偏差、方位角）、
   命令（`vx`/`wz`/`vy`）与当前相位。
   - 没有 `/cubes` 数据 → 明确写"尚未收到检测"，不拿旧值冒充现场；
   - 有数据但这一帧没方块 → 写"本帧未检测到方块"，与"没数据"分开；
   - 数据超过 0.5 s → 标记过期。
2. **对准仿真**（按钮「跑对准仿真」）：用**与真车同一份纯函数**
   （`ros2_ws/src/robogame_core/robogame_core/grasp_alignment.py`）跑闭环，
   画布上黄=转向、蓝=前进、绿=到位，红圈=横向容差，下面给出步数、阶段统计、
   最终误差与**安全违规次数**。

## 怎么打开

### 本地（Windows，无 ROS，只看算法与仿真）

```powershell
D:\python.exe -B tools\field_dashboard.py --host 127.0.0.1 --port 8766 --output tmp\grasp_web_check
```

浏览器打开 `http://127.0.0.1:8766`。`rclpy` 缺失时页面顶部会写 `ROS unavailable`，
这是预期的：实时面板需要真机话题，**仿真按钮不需要 ROS**。

### 树莓派（真车联调）

按 `FIELD_DASHBOARD.md` 的方式启动一个 SSH 隧道 + 浏览器页面即可。本轮新增/改动的文件：

```text
tools/grasp_alignment_sim.py                    (新增)
tools/field_dashboard_web/grasp_panel.js        (新增)
tools/field_dashboard.py                        (改：订阅 /cubes、快照加 grasp、/api/grasp/simulate)
tools/field_dashboard_web/index.html            (改：抓取对准卡片)
tools/field_dashboard_web/app.js                (改：每次刷新渲染面板)
ros2_ws/src/robogame_core/robogame_core/grasp_alignment.py   (新增)
ros2_ws/src/manipulator_client/manipulator_client/node.py    (改：横移→转向对准)
ros2_ws/src/robogame_bringup/config/robot.yaml               (改：对准参数)
```

部署时这 8 个文件都要进 bundle 清单，只改 Windows 不会更新树莓派上正在跑的服务。

## 证据边界

**能证明**：网页确实提供该面板与仿真接口（HTTP 实测）；仿真在同一套参数下收敛、
且全程未出现"偏离时前进"（自动断言）；纯函数与节点默认值一致（交叉断言）。

**不能证明**：真车抓得住。仿真没有打滑、惯性、延迟、视觉噪声、云盘角度误差；
真车 `target_distance_m` / `cross_tolerance_m` / `kp_bearing` / `max_turn_rate` 需要上车实测；
相机偏角需要安装后标定。命中率必须由真车抓取容差窗口测试给出。
