"""底盘授权的单点定义（纯逻辑，零 ROS）。

## 为什么单独一个模块

「谁有权驱动底盘」这条规则原本在 `line_follow_node` 与 `motion_control` 里**各写了一份**
（两份代码、两套边界），而 `manipulator_client` 干脆没写——集成审计
（`tools/integration_audit.py`）正是因此报出 blocker：路线把作业段的底盘授权给了
机构节点，机构节点却不看授权。

三份实现迟早分叉，而分叉的后果是「某个节点在没被授权时也能驱动底盘」。
所以规则放这里单点定义，三个节点都调它：

    require=False        → 独立联调：不要求授权（永远放行）
    require=True         → 必须有**新鲜**且**属于自己**的授权，否则不放行
    授权缺失 / 过期 / 给了别人 → 不放行（fail-safe：授权断流就停车）

**安全例外**：急停、通信丢失、机构故障仍由各节点自己的安全门控优先处理——
授权只能「禁止运动」，永远不能「放行危险运动」。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: 谁都不许驱动底盘
SOURCE_NONE = "none"


@dataclass
class AuthorizationState:
    """一个运动节点持有的授权视图。

    用法::

        auth = AuthorizationState(require=True, stale_s=0.5)
        auth.grant("line_follow", now)      # 收到 /mission/active_source
        if auth.allows("line_follow", now): ...
    """

    require: bool = False
    stale_s: float = 0.5
    granted: str | None = None
    granted_at: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.stale_s) or self.stale_s <= 0.0:
            raise ValueError("stale_s must be positive and finite")

    def grant(self, source: str, now: float) -> None:
        """记下任务层广播的授权（空串按「谁都不许」处理）。"""
        if not math.isfinite(now):
            raise ValueError("now must be finite")
        self.granted = (source or "").strip()
        self.granted_at = now

    def granted_source(self, now: float) -> str:
        """当前有效授权来源；无人授权或已过期时返回空串。"""
        if not math.isfinite(now):
            raise ValueError("now must be finite")
        if self.granted is None or self.granted == "":
            return ""
        if (now - self.granted_at) > self.stale_s:
            return ""
        return self.granted

    def allows(self, source: str, now: float) -> bool:
        """本节点此刻是否有权驱动底盘。"""
        if not self.require:
            return True
        return self.granted_source(now) == source

    def release(self) -> None:
        """显式释放（终态/急停时用）。"""
        self.granted = None
        self.granted_at = 0.0

    @property
    def was_authorized(self) -> bool:
        return self.granted not in (None, "", SOURCE_NONE)
