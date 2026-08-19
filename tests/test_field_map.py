"""field_map 模块的单元测试。

这个文件不需要你填数据。它的作用是：每次你改 field_map.py 里的坐标后，
跑一遍这些测试，确认没有改坏规则级的东西（尺寸、高度、状态机优先级）。

坐标系约定（和 field_map.py 一致）：
    原点 = 场地外沿西南角；x 沿 4.8m 短边（0..4.8）；y 沿 7.2m 长边（0..7.2）。
    矩形 width = 沿短边方向的边长，height = 沿长边方向的边长。

对你（现场回填者）最有用的两条：
    - test_zone_sizes_match_rule_text  —— 防止区域尺寸和规则手册不一致（之前 Codex 把
      搭建区写成 0.6x2.4 就是这类错，已修并加了回归）。
    - test_six_unknown_tag_anchors_have_rule_height —— 你填标签坐标时，不能把
      中心高 0.325 改掉（那是规则算出来的）；x/y/yaw 填完测试仍应通过。
"""
import unittest

from robogame_core.field_map import (
    Confidence,
    NavigationMode,
    choose_navigation_mode,
    default_field_map,
)
from robogame_core.models import Pose2D


class FieldMapTests(unittest.TestCase):
    # 规则手册给出的区域尺寸对照表。
    # 元组 = (width, height)，即 (x方向尺寸, y方向尺寸) = (短边方向, 长边方向)，单位米。
    # 注意 key 必须和 field_map.py 里 zones 字典的 key 一致；
    # 以后新增区域（如蓝方搭建区 blue_build）时，把规则尺寸加进来即可。
    RULE_SIZES = {
        # rule 3.1.3: 启动区 0.6m x 0.6m（正方形，两方向相同）
        "red_start": (0.6, 0.6),
        # rule 3.1.4: 搭建区 x方向2.4m（短边）x y方向0.6m（长边）
        "red_build": (2.4, 0.6),
        # rule 3.1.5: 中央高台（每半场一个）x方向1.8m（短边）x y方向1.7m（长边）
        "red_platform": (1.8, 1.7),
        # rule 3.1.7: 屋顶材料区（全场一个）x方向0.4m（短边）x y方向2.2m（长边，细长条沿长边展开）
        "roof_material": (0.4, 2.2),
        # rule 3.1.5: 斜坡（每半场一个）x方向1.8m（短边）x y方向0.8m（长边）
        "red_ramp": (1.8, 0.8),
        # rule 3.1.6: 墙体建筑材料区（每半场一个，红蓝各一）x方向1.8m（短边）x y方向0.3m（长边）
        "red_wall_material": (1.8, 0.3),
        "blue_wall_material": (1.8, 0.3),
    }

    def test_rule_dimensions_and_start_zone(self):
        """验证：场地尺寸、黑线宽、启动区尺寸都是规则值，且启动区能包住 (0.7,0.7)。"""
        field = default_field_map()
        self.assertEqual((field.length_m, field.width_m), (7.2, 4.8))
        self.assertEqual(field.line_width_m, 0.05)
        start = field.zones["red_start"]
        self.assertEqual((start.width, start.height), (0.6, 0.6))
        self.assertEqual(start.size_confidence, Confidence.RULE)
        self.assertEqual(start.position_confidence, Confidence.MEASURED)
        self.assertTrue(start.contains(Pose2D(0.7, 0.7, 0.0)))

    def test_zone_sizes_match_rule_text(self):
        """回归测试：每个区域尺寸必须和规则手册一致（宽/高方向也不能错）。

        曾修过的 bug：blue_build 被写成 0.6x2.4，而规则 3.1.4 是 2.4x0.6。
        改区域尺寸时必须保证这条仍绿。
        """
        field = default_field_map()
        for name, (w, h) in self.RULE_SIZES.items():
            zone = field.zones.get(name)
            if zone is None:
                continue
            self.assertEqual(
                (zone.width, zone.height), (w, h),
                f"zone '{name}' size deviates from rule text",
            )

    def test_six_measured_tag_anchors_have_rule_height(self):
        """验证：6 个标签都在、x/y/yaw 都是现场确认值（MEASURED）、中心高是 rule 0.325。

        数据来源：x/y = 你现场实测；yaw = 你确认的正面朝向
        （弧度，逆时针为正，0=+x短边/90°=+y长边/180°=-x/270°=-y）。
        """
        import math

        field = default_field_map()
        self.assertEqual(set(field.tags), set(range(1, 7)))
        self.assertTrue(all(tag.z == 0.325 for tag in field.tags.values()))
        # 现场实测：1:(0.4,1.6) 2:(1.5,2.2) 3:(2.6,3.6) 4:(3.5,4.7) 5:(4.4,1.6) 6:(3.5,0.4)
        # 朝向确认：1:+x  2:-y  3:+x  4:-y  5:-x  6:+y
        expected = {
            1: (0.4, 1.6, 0.0),
            2: (1.5, 2.2, 3.0 * math.pi / 2.0),
            3: (2.6, 3.6, 0.0),
            4: (3.5, 4.7, 3.0 * math.pi / 2.0),
            5: (4.4, 1.6, math.pi),
            6: (3.5, 0.4, math.pi / 2.0),
        }
        for tid, (x, y, yaw) in expected.items():
            tag = field.tags[tid]
            self.assertEqual((tag.x, tag.y), (x, y), f"tag {tid} x/y differs")
            self.assertAlmostEqual(tag.yaw, yaw, places=6, msg=f"tag {tid} yaw differs")
        self.assertTrue(all(tag.position_confidence is Confidence.MEASURED for tag in field.tags.values()))
        self.assertTrue(all(tag.height_confidence is Confidence.RULE for tag in field.tags.values()))

    def test_playable_zone_matches_field_geometry(self):
        """验证：可活动区 = 全场去掉 0.4m 黑边，且方向与新坐标系一致。

        短边方向 4.8-0.4-0.4=4.0（width），长边方向 7.2-0.4-0.4=6.4（height）。
        """
        field = default_field_map()
        playable = field.zones["playable"]
        self.assertEqual((playable.width, playable.height), (4.0, 6.4))
        self.assertEqual((playable.x, playable.y), (0.4, 0.4))
        self.assertEqual(playable.position_confidence, Confidence.RULE)
        self.assertEqual(playable.size_confidence, Confidence.RULE)
        # 场地内侧的点应在场内；超出外沿的点判为越界
        self.assertTrue(field.inside_field(Pose2D(0.5, 0.5, 0.0)))
        self.assertTrue(field.inside_field(Pose2D(4.3, 6.7, 0.0)))
        self.assertFalse(field.inside_field(Pose2D(5.0, 3.6, 0.0)))   # 超出短边 4.8
        self.assertFalse(field.inside_field(Pose2D(2.4, 7.5, 0.0)))   # 超出长边 7.2
        self.assertFalse(field.inside_field(Pose2D(-0.1, 2.0, 0.0)))  # 负坐标
        # 带容差时，黑边内的点（距外沿 < margin）判为越界
        self.assertFalse(field.inside_field(Pose2D(0.2, 0.2, 0.0), margin_m=0.4))

    def test_navigation_mode_priority(self):
        """验证：状态机优先级 STOP > TAG_DOCK > RAMP > LINE_FOLLOW > WAYPOINT。"""
        common = dict(localization_valid=True, emergency_stop=False)
        self.assertEqual(choose_navigation_mode(**common, line_visible=True, near_tag=True, on_ramp=True), NavigationMode.TAG_DOCK)
        self.assertEqual(choose_navigation_mode(**common, line_visible=True, near_tag=False, on_ramp=True), NavigationMode.RAMP)
        self.assertEqual(choose_navigation_mode(**common, line_visible=True, near_tag=False, on_ramp=False), NavigationMode.LINE_FOLLOW)
        self.assertEqual(choose_navigation_mode(**common, line_visible=False, near_tag=False, on_ramp=False), NavigationMode.WAYPOINT)

    def test_invalid_localization_forces_stop(self):
        """验证：定位无效或急停时，无论其他条件如何都必须 STOP（安全兜底）。"""
        mode = choose_navigation_mode(localization_valid=False, emergency_stop=False, line_visible=True, near_tag=True, on_ramp=True)
        self.assertEqual(mode, NavigationMode.STOP)


if __name__ == "__main__":
    unittest.main()
