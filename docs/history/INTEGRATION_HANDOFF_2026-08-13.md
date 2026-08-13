# RoboGame2026 集成交接（2026-08-13）

## 1. 用途

记录 08-13 完成的软件安全修复、视觉进度同步与视觉节点审计，供重启后继续。分支 `integration/robogame-t26`，远程 `origin/integration/robogame-t26`。

## 2. 本次完成（已提交，按时间倒序）

| commit | 内容 |
|---|---|
| `044664f` | docs: 修视觉 TODO 交叉引用 |
| `3b677cc` | docs: 计划加视觉轨道 |
| `b003ea4` | docs: 记录视觉→集成接口需求 P0 |
| `3cfe728` | docs: 视觉进度补进 TODO/README |
| `46bc02f` | docs: 测试计数同步 258 |
| `aaadd40` | fix ISSUE-017 cancel 调 STOP |
| `9717cb9` | fix ISSUE-016 超时发零速度帧 |
| `bc4b920` | fix ISSUE-015 串口异常保护 |
| `e5eaf37` | docs: 记录软件缺口 + 13-18 计划 |

测试：Windows 全量 **258 项通过，0 失败，16 skip**（16 项需 rclpy 的行为测试在 Windows skip，Ubuntu 全量运行）。

## 3. 视觉节点审计结论（本次新增）

`cube_perception/node.py` 是纯「检测→发布」管道：

- 只订阅 `/camera/image_raw` + `/camera/camera_info`，发布 `/cubes`，**无任何 stage/mode/目标选择输入**。
- `max_working_distance_m` / ROI / `confirm_frames` 等已是参数，但均为静态。
- 目标颜色过滤目前在 `manipulator_client` 侧（按 `PICK_ORANGE`/`PICK_PURPLE`），不在视觉侧。
- `CubeDetection.msg` 与 `DetectionEstimate` 均无 `distance_valid` 字段。

三个接口需求的最小改动：

1. **SEARCH/ACQUIRE/VERIFY 阶段**：给视觉节点加 stage 输入（话题/服务），按阶段切 `max_working_distance_m`/ROI。旋钮已存在，改动最小、收益最大。
2. **多目标选择约束**：加 `desired_color` + 槽位/中心约束输入，需先对齐「约束由视觉侧还是 manipulator 侧消费」。
3. **`distance_valid` 字段**：扩展 `DetectionEstimate` + `CubeDetection.msg` + 检测器按 clipped/side_rotated/occluded/out_of_range 设标志。

## 4. 下一步（唯一建议）

先做 **SEARCH/ACQUIRE/VERIFY 阶段**：给视觉节点加 stage 话题，按阶段切 `max_working_distance_m`。这是三个接口里改动最小、解锁面最广的一个。

## 5. 阻塞项（外部依赖，未交付）

- 电控：`0x10`/`0x11`/`0x12` 逐字节字段表 + 真实十六进制样例（ISSUE-007）。
- 机械：`GRAB/LIFT/RELEASE/STOP` 动作完成判据 + STOP/CANCEL 行为 + 相机安装位。

## 6. 接手 agent 先做

```bash
git branch --show-current   # 应 integration/robogame-t26
git status --short          # 应干净
git log --oneline -3        # HEAD 应 044664f 或更新
```

计划见 `docs/PLAN_2026-08-13_TO_18.md`（含软件/视觉/硬件三条轨道）；任务清单见 `docs/TODO_AND_ISSUES.md`。
