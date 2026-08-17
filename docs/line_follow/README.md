# 巡线模块说明

## 概述

`robogame_core/line_follow.py` 是纯算法模块，把八路灰度传感器原始值转换为归一化横向偏差和纠偏输出。不包含 ROS 节点、不发布话题、不直接发送 `cmd_vel`。

## 数据模型

### LineSegment

场地黑线段，包含起点/终点 Pose2D 坐标和线宽。

### LineSensorReading

八路灰度传感器读数，包含 8 路 active 状态（True=检测到黑线）和可选的归一化原始值（0..1）。

### LineSensorState

传感器状态枚举：

| 状态 | 含义 |
|------|------|
| `ON_LINE` | 线在中心附近（偏差绝对值 < edge_threshold） |
| `LEFT_EDGE` | 线偏左，机器人偏右（偏差 < -edge_threshold） |
| `RIGHT_EDGE` | 线偏右，机器人偏左（偏差 > edge_threshold） |
| `LOST` | 连续 lost_threshold 帧未检测到线 |
| `INTERSECTION` | 同时激活路数 >= intersection_threshold |
| `ALL_BLACK` | 8 路全部激活 |

## 偏差计算

### 质心法 `compute_deviation(channels, method="centroid")`

8 路传感器的归一化位置：

```
通道:  0      1      2      3      4      5      6      7
位置: -1.0  -0.71  -0.43  -0.14  +0.14  +0.43  +0.71  +1.0
```

偏差 = sum(激活通道位置) / 激活通道数

约定：
- 偏差 > 0：线在右侧
- 偏差 < 0：线在左侧
- 偏差 = 0：居中
- nan：无激活或全激活（无法确定方向）

### 加权平均法 `compute_deviation_weighted(raw_values, threshold)`

使用原始灰度值（0..1）计算加权平均偏差，权重 = max(0, raw_value - threshold)。

## 纠偏控制器 `compute_correction(deviation, kp, kd, prev_deviation, dt, vx_base)`

PD 控制器，输出机体坐标系 (vx, wz)：

- `vx = vx_base`（基础前进速度）
- `wz = -(kp * deviation + kd * derivative)`
- 偏差 > 0（线在右）→ wz < 0（右转）
- 偏差 < 0（线在左）→ wz > 0（左转）
- 偏差为 NaN → (0, 0) 停车

## 参数表

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `lost_threshold` | 5 | 连续出线多少帧后判定 LOST |
| `intersection_threshold` | 6 | 多少路同时激活判定交叉口 |
| `edge_threshold` | 0.3 | 偏差绝对值超过此值判定为边缘 |
| `kp` | 待标定 | 比例增益，建议初始值 1.0 |
| `kd` | 待标定 | 微分增益，建议初始值 0.1 |
| `dt` | 0.02 | 控制周期（秒），对应 50Hz |
| `vx_base` | 0.2 | 基础前进速度 m/s |
| `threshold`（加权法） | 0.5 | 二值化阈值 |

## 传感器状态机

```
                    检测到线
    ┌──────────────────────────────────────┐
    │                                      │
    ▼                                      │
 ON_LINE ──偏差>edge──→ RIGHT_EDGE         │
    │                      │               │
    │偏差<-edge            │恢复           │
    ▼                      ▼               │
 LEFT_EDGE ────────────── ON_LINE          │
    │                                      │
    │ 无激活                                │
    ▼                                      │
 (lost_count++) ──达到阈值──→ LOST         │
    │                                      │
    │ 恢复检测                              │
    └──────────────────────────────────────┘
```

## 测试

```bash
cd ~/robogame_git
python3 -m unittest tests.test_line_follow -v
```

覆盖场景：居中、左偏、右偏、出线、交叉口、全黑、无信号、对称性、NaN/inf 拒绝、PD 比例/微分分量。

