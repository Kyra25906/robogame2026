"""巡线黑白标定的持久化（纯逻辑，零 ROS）。

## 为什么这是「上电后无人干预」的第一阻断项

网页面板的「应用标定」原本只通过参数服务**推给运行中的节点**——断电重启就没了。
于是每次上电，巡线节点都回到代码默认基准（`white_ref=0` / `black_ref=4095`，
只有方向意义、不代表真实读数），归一化结果与真实黑白无关，巡线直接走不起来。
要「上电即自主」，标定值必须**落到文件、启动时加载**。

## 存什么 / 怎么校验

存**逐路** `white_ref[8]` / `black_ref[8]`（8 路模块各路差异常达数百，只存一个
全局基准会让某些通道永远判不出黑线），外加来源与时间戳（便于追溯是哪次标的）。

校验（任何一条不过就拒绝加载，并由调用方大声报警）：
- 必须 8 路、数值有限；
- 每一路 `black_ref > white_ref`（项目统一约定：1.0 = 黑线）；
- 每一路黑白差 ≥ `MIN_CHANNEL_SPAN`（分离度太低无法二值化）。

**校验失败不静默退回默认值**：默认值只有方向意义，静默使用会让车「看起来在巡线」
其实读数是假的。调用方必须把失败原因暴露出来。
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

#: 单路黑白最小分离度（与 line_follow_node._calibration_is_usable 同源）
MIN_CHANNEL_SPAN = 1.0
#: 期望通道数
CHANNELS = 8
#: 标定文件格式版本
FORMAT_VERSION = 1
#: 默认标定文件路径（树莓派上的部署路径；开发机可不设）
DEFAULT_CALIBRATION_FILE = "~/robogame_line_calibration.json"


@dataclass(frozen=True)
class LineCalibration:
    """一份可持久化的巡线黑白标定。"""

    white_ref: tuple[float, ...]
    black_ref: tuple[float, ...]
    source: str = ""
    saved_at: str = ""

    def __post_init__(self) -> None:
        if len(self.white_ref) != CHANNELS or len(self.black_ref) != CHANNELS:
            raise ValueError(f"标定必须是 {CHANNELS} 路，收到 {len(self.white_ref)}/{len(self.black_ref)}")
        for name, values in (("white_ref", self.white_ref), ("black_ref", self.black_ref)):
            for index, value in enumerate(values):
                if not math.isfinite(value):
                    raise ValueError(f"{name}[{index}] 不是有限数值：{value!r}")
        problems = self.problems()
        if problems:
            raise ValueError("；".join(problems))

    def problems(self) -> list[str]:
        """返回所有不合法之处（空列表 = 可用）。"""
        issues: list[str] = []
        for index, (white, black) in enumerate(zip(self.white_ref, self.black_ref)):
            if black <= white:
                issues.append(
                    f"通道 {index + 1}：black_ref({black:.0f}) 必须大于 white_ref({white:.0f})"
                    "（约定 1.0 = 黑线）"
                )
            elif (black - white) < MIN_CHANNEL_SPAN:
                issues.append(
                    f"通道 {index + 1}：黑白分离度只有 {black - white:.0f}"
                    f"（需 ≥ {MIN_CHANNEL_SPAN:.0f}），无法二值化"
                )
        return issues

    @property
    def is_usable(self) -> bool:
        return not self.problems()

    def to_json(self) -> str:
        return json.dumps(
            {
                "format_version": FORMAT_VERSION,
                "source": self.source,
                "saved_at": self.saved_at or _timestamp(),
                "white_ref": [round(v, 4) for v in self.white_ref],
                "black_ref": [round(v, 4) for v in self.black_ref],
            },
            ensure_ascii=False,
            indent=2,
        )

    def save(self, path: Path | str) -> Path:
        """写盘（先写临时文件再替换，避免写一半掉电留下坏文件）。"""
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_json() if self.saved_at else LineCalibration(
            self.white_ref, self.black_ref, self.source, _timestamp()
        ).to_json()
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)
        return target


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def calibration_from_mapping(data: Mapping[str, Any], *, source: str = "") -> LineCalibration:
    """从字典构造（文件内容或面板推送载荷）。字段缺失/类型不对 → 人话报错。"""
    if not isinstance(data, Mapping):
        raise ValueError("标定内容必须是字典")
    for key in ("white_ref", "black_ref"):
        if key not in data:
            raise ValueError(f"标定缺少 {key}")
    white = [float(v) for v in _as_sequence(data["white_ref"], "white_ref")]
    black = [float(v) for v in _as_sequence(data["black_ref"], "black_ref")]
    return LineCalibration(
        white_ref=tuple(white),
        black_ref=tuple(black),
        source=str(data.get("source", source)),
        saved_at=str(data.get("saved_at", "")),
    )


def _as_sequence(value: Any, name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} 必须是数组")
    return value


def load_calibration(path: Path | str) -> LineCalibration:
    """读标定文件；文件不存在/内容不合法都抛异常（由调用方决定怎么报警）。"""
    target = Path(path).expanduser()
    if not target.is_file():
        raise FileNotFoundError(f"标定文件不存在：{target}")
    text = target.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"标定文件不是合法 JSON：{target}") from exc
    return calibration_from_mapping(data, source=str(target))


def calibration_from_params(
    *, white_ref: float, black_ref: float,
    white_offset: Sequence[float], black_offset: Sequence[float],
) -> LineCalibration:
    """把节点参数（全局基准 + 每路偏移）还原成逐路标定。"""
    whites = [float(v) for v in white_offset]
    blacks = [float(v) for v in black_offset]
    if len(whites) != CHANNELS:
        raise ValueError(f"white_offset 必须是 {CHANNELS} 路")
    if len(blacks) != CHANNELS:
        raise ValueError(f"black_offset 必须是 {CHANNELS} 路")
    return LineCalibration(
        white_ref=tuple(float(white_ref) + offset for offset in whites),
        black_ref=tuple(float(black_ref) + offset for offset in blacks),
        source="node params",
    )


def calibration_to_params(calibration: LineCalibration) -> dict[str, Any]:
    """转成节点参数（全局基准 + 每路偏移），与网页面板的推送格式一致。"""
    white_base = min(calibration.white_ref)
    black_base = min(calibration.black_ref)
    return {
        "white_ref": round(white_base, 4),
        "black_ref": round(black_base, 4),
        "white_offset": [round(v - white_base, 4) for v in calibration.white_ref],
        "black_offset": [round(v - black_base, 4) for v in calibration.black_ref],
    }


def startup_verdict(calibration: LineCalibration | None, *, file_path: str = "") -> tuple[bool, str]:
    """上电时的结论：这份标定能不能用来巡线？（给节点日志与网页显示用）

    返回 (可用, 人话原因)。**不可用时原因必须能指导操作**，因为上电自主的前提
    就是「不需要人来判断」——真出问题时要让人一眼知道该去标定。
    """
    if calibration is None:
        return False, (
            "没有可用标定：巡线基准只有方向默认值（white=0 / black=4095），"
            "归一化结果与真实黑白无关。"
            + (f"请在网页面板标定后保存到 {file_path}。" if file_path else "请先在网页面板标定。")
        )
    issues = calibration.problems()
    if issues:
        return False, "标定不可用：" + "；".join(issues)
    return True, (
        f"标定可用（来源 {calibration.source or '未知'}，"
        f"黑白差最小 {min(b - w for w, b in zip(calibration.white_ref, calibration.black_ref)):.0f}）"
    )
