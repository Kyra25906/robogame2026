# Two-person sprint board: 2026-07-18 to 2026-08-17

> ⚠️ 已过期（2026-09-18 文档盘点时标注）：这是 **7 月 18 日—8 月 17 日**的两人冲刺表，
> 窗口早已结束，其中的周门判据（Week 1~4）不要当作当前验收标准——真车事实由
> `docs/field/真车对接设计稿_2026-08-19.md` 与 `docs/field/INTEGRATION_CHECKLIST.md`
> 承载（后者已逐条标注哪条作废）。当前职责边界见 `docs/team/两人算法最终分工.md`，
> 当前进度见 `docs/TODO_AND_ISSUES.md`。本文件保留作历史记录。

| Dates | Person A: integration/navigation | Person B: vision/manipulator | Joint exit criterion |
|---|---|---|---|
| Jul 18-24 | Serial, safety stop, odometry, velocity control | Camera setup, image capture, mock mechanism | Week 1 gate passes |
| Jul 25-31 | Go-to-pose, waypoints, bounds | HSV detector, visual alignment, single grab | Week 2 gate passes |
| Aug 1-7 | Mission manager, timeouts, logging | Single placement and verification | Single loop ≥8/10 |
| Aug 8-14 | Multi-cube mission integration | Layered placement | Roof tower ≥6/10 or fallback |
| Aug 15-17 | Version freeze and field profiles | Lighting validation and evidence | Competition and last-stable releases |

Daily rhythm: 15-minute sync, two hours focused implementation, one hour joint
robot test, 30 minutes log analysis, 15 minutes commit and metric update.

