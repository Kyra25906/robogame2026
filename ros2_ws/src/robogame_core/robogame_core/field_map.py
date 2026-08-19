"""RoboGame 2026 场地图数据模型（规则推导 + 现场回填）。

坐标系（全项目统一，和 field_layout.yaml、GENERAL_FIELD_MAP_2026.md 一致）：
    原点  = 场地外沿西南角（现场贴胶带钉死的物理角）
    +x    = 沿 4.8 m 短边
    +y    = 沿 7.2 m 长边，指向对方半场
    yaw   = 弧度，逆时针为正；0 = 朝 +x，90°(1.5708) = 朝 +y
    单位  = 米 / 弧度
    注意：x 是短边方向（全场最大 4.8），y 是长边方向（全场最大 7.2）。

我方是红方，半场在西南角（启动区左下角 = (0.4, 0.4)）。
中心对称：对方（蓝方）坐标 = (4.8 - x, 7.2 - y, yaw + pi)，最后把角度归一化。

可信度分级（每个值都要知道自己属于哪一级）：
    rule      = 规则手册文字写死的值（尺寸、高度），不要改
    estimated = 按图 3.1/3.8/3.9 推算的首版位置，够规划用，不够厘米级定位
    measured  = 你现场用卷尺量出来的值，最终以它为准
    unknown   = 尚未测量，等你回填
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .models import Pose2D


class Confidence(str, Enum):
    """数据可信度分级：unknown < estimated < measured，rule 是规则基准。"""

    UNKNOWN = "unknown"      # 未测量，等现场回填
    RULE = "rule"            # 规则文字明确给出的值（尺寸/高度），不可改
    ESTIMATED = "estimated"  # 按示意图推算的首版位置，需现场替换
    MEASURED = "measured"    # 现场实测，最终精度来源


class NavigationMode(str, Enum):
    """导航状态机可切换的 5 个模式（优先级见 choose_navigation_mode）。"""

    WAYPOINT = "WAYPOINT"        # 空旷路段：轮速里程计 + IMU 走到目标点
    LINE_FOLLOW = "LINE_FOLLOW"  # 看到黑线：灰度阵列接管横向纠偏
    TAG_DOCK = "TAG_DOCK"        # 识别到正确 ID 标签：用 PnP 矫正位置并精确靠近
    RAMP = "RAMP"                # 上坡道：限速、抑制里程计打滑
    STOP = "STOP"                # 急停/通信丢失/定位无效：立即停车


@dataclass(frozen=True)
class Rect:
    """场地里的一个矩形区域。

    坐标语义：
        x, y   = 矩形【左下角】在场地坐标系里的坐标（米），
                 x 是沿短边方向（0..4.8），y 是沿长边方向（0..7.2）
        width  = 沿 +x（短边）方向的边长（米）
        height = 沿 +y（长边）方向的边长（米）

    需要你填的数据：
        位置 (x, y) —— 现场量区域角点后回填；尺寸 (width, height) 是 rule 级不要改。
        每个区域同时标注 position_confidence（位置可信度）和 size_confidence（尺寸可信度）。
    """

    name: str
    x: float
    y: float
    width: float
    height: float
    position_confidence: Confidence = Confidence.ESTIMATED  # 位置可信度：现场量完改 MEASURED
    size_confidence: Confidence = Confidence.RULE           # 尺寸可信度：规则给出，保持 RULE

    def contains(self, pose: Pose2D, margin_m: float = 0.0) -> bool:
        """判断一个机器人位姿是否落在这个矩形内（可加容差 margin_m 米）。"""
        return (
            self.x - margin_m <= pose.x <= self.x + self.width + margin_m
            and self.y - margin_m <= pose.y <= self.y + self.height + margin_m
        )


@dataclass(frozen=True)
class TagAnchor:
    """一个视觉标签（AprilTag）的锚点。

    坐标语义（全部要你现场量/确认）：
        x    = 标签中心正下方地面点，沿短边方向离原点的距离（米，0..4.8）
        y    = 标签中心正下方地面点，沿长边方向离原点的距离（米，0..7.2）
        z    = 标签中心离地高度（米）。规则给出上沿 0.4m，标签边长 0.15m，
               所以中心高 = 0.4 - 0.15/2 = 0.325m，是 rule 级，现场核对即可
        yaw  = 标签正面（法线）指向的角度（弧度），即从标签正面垂直指向机器人通道的方向
               0 = 朝 +x（短边）；90°(1.5708) = 朝 +y（长边）；180°(3.1416) = 朝 -x

    需要你填的数据：x、y、yaw（每个标签三项）；z 不用改。
    填完把 position_confidence 从 UNKNOWN 改成 MEASURED。
    """

    tag_id: int
    x: float | None              # 待填：标签中心正下方地面点的 x（米，短边方向）
    y: float | None              # 待填：标签中心正下方地面点的 y（米，长边方向）
    z: float                     # 中心高 0.325m（rule 级，不用改）
    yaw: float | None            # 待填：标签正面朝向（弧度）
    position_confidence: Confidence = Confidence.UNKNOWN  # 位置可信度：填完改 MEASURED
    height_confidence: Confidence = Confidence.RULE       # 高度可信度：规则给出


@dataclass(frozen=True)
class FieldMap:
    """整张场地的地图：场地尺寸 + 区域集合 + 标签集合。

    需要你填的数据：zones 里各区域的位置 (x,y)、tags 里各标签的 (x,y,yaw)。
    场地尺寸（length_m/width_m/border_m/line_width_m）都是 rule 级，不要改。
    注意：length_m=7.2 是长边，沿 +y；width_m=4.8 是短边，沿 +x。
    """

    length_m: float      # 场地长边 7.2m（沿 +y，含黑边，rule）
    width_m: float       # 场地短边 4.8m（沿 +x，含黑边，rule）
    border_m: float      # 黑色边界宽/高 0.4m（rule）
    line_width_m: float  # 黑线宽 0.05m（rule）
    zones: dict[str, Rect]       # 区域名 -> 区域矩形（如 red_start、red_build）
    tags: dict[int, TagAnchor]   # 标签 id(1-6) -> 标签锚点

    def inside_field(self, pose: Pose2D, margin_m: float = 0.0) -> bool:
        """判断位姿是否在场地外沿以内（防止车中心越界），可加容差 margin_m 米。

        x 是短边方向（上限 width_m=4.8），y 是长边方向（上限 length_m=7.2）。
        """
        return (
            margin_m <= pose.x <= self.width_m - margin_m
            and margin_m <= pose.y <= self.length_m - margin_m
        )


def default_field_map() -> FieldMap:
    """返回红方半场的规划地图（来自规则 3.1.2–3.1.8 + 图 3.1/3.8/3.9）。

    现状：
        - 场地尺寸：rule 级，已确定，不用改。
        - 各区域尺寸：rule 级，已确定；各区域【位置】目前是 estimated 占位，
          需要你现场量角点后替换（把 position_confidence 改成 MEASURED）。
        - 标签：x/y/yaw 全部是 None（unknown），需要你现场逐个回填。
    注意：不要在代码里凭空编坐标；每个坐标都必须来自现场测量或规则文字。
    """
    zones = {
        # playable = 可活动区 = 全场去掉 0.4m 黑边：尺寸和位置都由规则推出（rule 级）
        # 短边方向 4.8-0.8=4.0，长边方向 7.2-0.8=6.4
        "playable": Rect(
            "playable", 0.4, 0.4, 4.0, 6.4,
            Confidence.RULE, Confidence.RULE,
        ),
        # red_start = 红方启动区（我方），规则只给了尺寸 0.6x0.6；
        # 左下角 (0.4, 0.4) 由你现场确认（正方形贴西南角），标 MEASURED
        "red_start": Rect(
            "red_start", 0.4, 0.4, 0.6, 0.6,
            Confidence.MEASURED, Confidence.RULE,
        ),
        # red_build = 红方搭建区，规则 3.1.4 尺寸 2.4x0.6：
        #   x 方向（短边）2.4m，y 方向（长边）0.6m（你确认的方向，勿反）；
        # 左下角 (2.0, 0.4) 是你确认的，标 MEASURED
        "red_build": Rect(
            "red_build", 2.0, 0.4, 2.4, 0.6,
            Confidence.MEASURED, Confidence.RULE,
        ),
        # red_platform = 红方中央高台（每半场一个，全场共两个），规则 3.1.5 尺寸 1.8x1.7：
        #   x 方向（短边）1.8m，y 方向（长边）1.7m（你确认的方向，勿反）；
        # 红方左下角 (2.6, 3.0) 是你现场实测的，标 MEASURED；
        # 蓝方高台在 (0.4, 2.5)（中心对称），仅参考不入红方地图
        "red_platform": Rect(
            "red_platform", 2.6, 3.0, 1.8, 1.7,
            Confidence.MEASURED, Confidence.RULE,
        ),
        # roof_material = 屋顶材料区（全场一个，红蓝共用），规则 3.1.7 尺寸 2.2x0.4：
        #   你确认的方向：x 方向（短边）0.4m，y 方向（长边）2.2m（细长条沿长边展开，勿反）；
        # 左下角 (2.2, 2.5) 是你现场实测的，标 MEASURED
        "roof_material": Rect(
            "roof_material", 2.2, 2.5, 0.4, 2.2,
            Confidence.MEASURED, Confidence.RULE,
        ),
        # red_ramp = 红方斜坡（每半场一个），规则 3.1.5 尺寸 1.8x0.8：
        #   你确认的方向：x 方向（短边）1.8m，y 方向（长边）0.8m；
        # 红方左下角 (2.6, 2.2) 是你现场实测的，标 MEASURED；
        # 蓝方斜坡 (0.4, 4.2)~(2.2, 5.0)（中心对称，你已确认），仅参考不入红方地图
        "red_ramp": Rect(
            "red_ramp", 2.6, 2.2, 1.8, 0.8,
            Confidence.MEASURED, Confidence.RULE,
        ),
        # red_wall_material = 红方（我方）墙体建筑材料区，规则 3.1.6 尺寸 1.8x0.3：
        #   x 方向（短边）1.8m，y 方向（长边）0.3m（你确认的方向，勿反）；
        #   全场 20 个橙色方块 = 每半场 10 个，红蓝各一个材料区；
        # 红方左下角 (2.6, 4.7) 是你现场实测的，标 MEASURED
        "red_wall_material": Rect(
            "red_wall_material", 2.6, 4.7, 1.8, 0.3,
            Confidence.MEASURED, Confidence.RULE,
        ),
        # blue_wall_material = 蓝方（对方）墙体建筑材料区，尺寸同规则 3.1.6：
        # 蓝方左下角 (0.4, 2.2) 是你现场实测的，标 MEASURED；
        # 红蓝互为中心对称（红 (2.6,4.7) <-> 蓝 (0.4,2.2)）
        "blue_wall_material": Rect(
            "blue_wall_material", 0.4, 2.2, 1.8, 0.3,
            Confidence.MEASURED, Confidence.RULE,
        ),
    }
    # 规则只确定：每半场 6 个标签（id 1-6）、贴在黑色竖直挡板朝通道一面、
    # 中心高 0.325m。
    # x/y = 标签中心正下方地面点（你现场实测，短边方向 x / 长边方向 y），标 MEASURED；
    # yaw = 标签正面朝向（弧度，逆时针为正，0=+x短边/90°=+y长边/180°=-x/270°=-y），
    #       由你确认：1:+x 2:-y 3:+x 4:-y 5:-x 6:+y。
    tags = {
        1: TagAnchor(1, 0.4, 1.6, 0.325, 0.0, Confidence.MEASURED),
        2: TagAnchor(2, 1.5, 2.2, 0.325, 3.0 * math.pi / 2.0, Confidence.MEASURED),
        3: TagAnchor(3, 2.6, 3.6, 0.325, 0.0, Confidence.MEASURED),
        4: TagAnchor(4, 3.5, 4.7, 0.325, 3.0 * math.pi / 2.0, Confidence.MEASURED),
        5: TagAnchor(5, 4.4, 1.6, 0.325, math.pi, Confidence.MEASURED),
        6: TagAnchor(6, 3.5, 0.4, 0.325, math.pi / 2.0, Confidence.MEASURED),
    }
    return FieldMap(7.2, 4.8, 0.4, 0.05, zones, tags)


def choose_navigation_mode(
    *,
    localization_valid: bool,  # 定位是否有效（里程计/IMU 状态）
    emergency_stop: bool,      # 是否按下急停
    line_visible: bool,        # 是否连续看到黑线
    near_tag: bool,            # 是否在预期标签附近且 ID 正确
    on_ramp: bool,             # 是否在坡道上（地图区或 pitch 越阈值）
) -> NavigationMode:
    """按"安全优先"选导航模式；优先级 STOP > TAG_DOCK > RAMP > LINE_FOLLOW > WAYPOINT。

    不需要你填数据；这是状态机决策逻辑，任何现场数据变化都不影响它。
    """
    if emergency_stop or not localization_valid:
        return NavigationMode.STOP
    if near_tag:
        return NavigationMode.TAG_DOCK
    if on_ramp:
        return NavigationMode.RAMP
    if line_visible:
        return NavigationMode.LINE_FOLLOW
    return NavigationMode.WAYPOINT
