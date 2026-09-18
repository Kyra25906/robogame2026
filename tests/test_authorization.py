"""底盘授权规则的单元测试（纯逻辑，零 ROS）。

这条规则原本在三个节点里各写一份（其中 manipulator_client 干脆没写，被集成审计
抓成 blocker）。现在单点定义在这里，测试要钉住：

1. `require=False`（独立联调）→ 永远放行；
2. `require=True` → 必须**新鲜**且**属于自己**的授权；
3. 授权缺失 / 过期 / 空串 / `none` → 一律不放行（fail-safe：授权断流就停车）；
4. 授权换给别人后立刻失效（不能靠旧授权继续跑）。
"""

from __future__ import annotations

import unittest

from robogame_core.authorization import SOURCE_NONE, AuthorizationState


class AuthorizationStateTests(unittest.TestCase):
    def test_not_required_always_allows(self):
        auth = AuthorizationState(require=False)
        self.assertTrue(auth.allows("line_follow", now=0.0))
        self.assertTrue(auth.allows("anything", now=1_000.0))

    def test_required_denies_until_granted(self):
        auth = AuthorizationState(require=True, stale_s=0.5)
        self.assertFalse(auth.allows("line_follow", now=0.0))
        auth.grant("line_follow", 1.0)
        self.assertTrue(auth.allows("line_follow", now=1.1))

    def test_only_our_own_source_is_allowed(self):
        auth = AuthorizationState(require=True, stale_s=0.5)
        auth.grant("motion_control", 1.0)
        self.assertFalse(auth.allows("line_follow", now=1.1))
        self.assertTrue(auth.allows("motion_control", now=1.1))

    def test_expired_grant_denies(self):
        auth = AuthorizationState(require=True, stale_s=0.5)
        auth.grant("line_follow", 1.0)
        self.assertFalse(auth.allows("line_follow", now=1.6), "过期授权不得继续放行")

    def test_none_and_empty_mean_nobody_may_drive(self):
        auth = AuthorizationState(require=True, stale_s=0.5)
        for value in (SOURCE_NONE, "", "   "):
            auth.grant(value, 1.0)
            self.assertFalse(auth.allows("line_follow", now=1.1), repr(value))
            # `granted_source` 如实返回广播值（`none` 就是「谁都不许」），
            # 空串/空白则归一化成一个空串。
            self.assertIn(auth.granted_source(now=1.1), ("", SOURCE_NONE))

    def test_regranting_another_source_takes_effect_immediately(self):
        auth = AuthorizationState(require=True, stale_s=0.5)
        auth.grant("line_follow", 1.0)
        self.assertTrue(auth.allows("line_follow", now=1.1))
        auth.grant("motion_control", 1.2)
        self.assertFalse(auth.allows("line_follow", now=1.25), "旧授权必须立刻失效")
        self.assertTrue(auth.allows("motion_control", now=1.25))

    def test_granted_source_trims_whitespace(self):
        auth = AuthorizationState(require=True, stale_s=0.5)
        auth.grant("  line_follow  ", 1.0)
        self.assertEqual(auth.granted_source(now=1.1), "line_follow")

    def test_release_denies_everyone(self):
        auth = AuthorizationState(require=True, stale_s=0.5)
        auth.grant("line_follow", 1.0)
        auth.release()
        self.assertFalse(auth.allows("line_follow", now=1.05))
        self.assertFalse(auth.was_authorized)

    def test_was_authorized_reflects_a_real_source(self):
        auth = AuthorizationState(require=True, stale_s=0.5)
        self.assertFalse(auth.was_authorized)
        auth.grant("line_follow", 1.0)
        self.assertTrue(auth.was_authorized)
        auth.grant(SOURCE_NONE, 1.1)
        self.assertFalse(auth.was_authorized)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            AuthorizationState(require=True, stale_s=0.0)
        with self.assertRaises(ValueError):
            AuthorizationState(require=True, stale_s=float("nan"))
        auth = AuthorizationState(require=True, stale_s=0.5)
        with self.assertRaises(ValueError):
            auth.grant("line_follow", float("inf"))
        with self.assertRaises(ValueError):
            auth.granted_source(float("nan"))

    def test_boundary_exactly_at_stale_limit_still_allows(self):
        """边界写成「超过才算过期」，与各节点原来的一致（避免差一帧误停）。"""
        auth = AuthorizationState(require=True, stale_s=0.5)
        auth.grant("line_follow", 1.0)
        self.assertTrue(auth.allows("line_follow", now=1.5))
        self.assertFalse(auth.allows("line_follow", now=1.500001))


if __name__ == "__main__":
    unittest.main()
