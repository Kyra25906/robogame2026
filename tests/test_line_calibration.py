"""巡线标定持久化的单元测试（纯逻辑，零 ROS）。

这条链路是「上电后无人干预」的第一阻断项，测试要钉住：

1. 存下去的标定**能原样读回来**（往返一致）；
2. 不合法的标定**一律拒绝**（通道数、非有限值、black≤white、分离度不足）——
   而且**不静默退回默认值**（默认值只有方向意义，静默使用会让车「看起来在巡线」）；
3. 上电结论**能指导操作**（不可用时必须说清「去标定」并指出文件路径）；
4. 与节点参数/网页面板的格式互转一致（全局基准 + 每路偏移）。
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from robogame_core.line_calibration import (
    CHANNELS,
    FORMAT_VERSION,
    LineCalibration,
    calibration_from_mapping,
    calibration_from_params,
    calibration_to_params,
    load_calibration,
    startup_verdict,
)


def _calibration(**overrides) -> LineCalibration:
    white = tuple(600.0 + 10 * index for index in range(CHANNELS))
    black = tuple(2400.0 + 20 * index for index in range(CHANNELS))
    return LineCalibration(
        white_ref=overrides.get("white_ref", white),
        black_ref=overrides.get("black_ref", black),
        source=overrides.get("source", "test"),
    )


class SerializationTests(unittest.TestCase):
    def test_round_trip_through_a_file(self):
        original = _calibration()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cal.json"
            original.save(path)
            loaded = load_calibration(path)
        self.assertEqual(loaded.white_ref, original.white_ref)
        self.assertEqual(loaded.black_ref, original.black_ref)
        self.assertEqual(loaded.source, "test")
        self.assertTrue(loaded.is_usable)

    def test_file_has_version_and_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cal.json"
            _calibration().save(path)
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["format_version"], FORMAT_VERSION)
        self.assertTrue(data["saved_at"], "必须留时间戳（追溯是哪次标的）")
        self.assertEqual(len(data["white_ref"]), CHANNELS)

    def test_save_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "dir" / "cal.json"
            _calibration().save(path)
            self.assertTrue(path.is_file())

    def test_save_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cal.json"
            _calibration().save(path)
            leftovers = [p.name for p in Path(tmp).iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [], "写盘必须用临时文件+替换，且不留残渣")

    def test_missing_file_raises_with_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError) as caught:
                load_calibration(Path(tmp) / "nope.json")
            self.assertIn("nope.json", str(caught.exception))

    def test_broken_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cal.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_calibration(path)

    def test_mapping_requires_both_reference_arrays(self):
        with self.assertRaises(ValueError) as caught:
            calibration_from_mapping({"white_ref": list(range(CHANNELS))})
        self.assertIn("black_ref", str(caught.exception))
        with self.assertRaises(ValueError):
            calibration_from_mapping({"white_ref": "not a list", "black_ref": [1] * CHANNELS})


def _unchecked(white, black, source="手工编辑") -> LineCalibration:
    """绕过构造期校验，模拟「文件被手工改坏 / 旧版本残留」的场景。

    正常路径下构造期就会拒绝坏标定（见 ValidationTests），但加载文件时必须能
    对**已经存在的坏数据**给出逐条原因，所以需要一个能持有坏值的实例。
    """
    broken = object.__new__(LineCalibration)
    object.__setattr__(broken, "white_ref", tuple(white))
    object.__setattr__(broken, "black_ref", tuple(black))
    object.__setattr__(broken, "source", source)
    object.__setattr__(broken, "saved_at", "")
    return broken


class ValidationTests(unittest.TestCase):
    def test_wrong_channel_count_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            LineCalibration(white_ref=(1.0,) * 7, black_ref=(2.0,) * 7)
        self.assertIn("8", str(caught.exception))

    def test_non_finite_values_are_rejected(self):
        white = (float("nan"),) + (600.0,) * 7
        with self.assertRaises(ValueError):
            LineCalibration(white_ref=white, black_ref=(2400.0,) * CHANNELS)

    def test_reversed_polarity_is_rejected(self):
        """black 必须大于 white（约定 1.0 = 黑线）；接反会让整条算法反向。"""
        with self.assertRaises(ValueError) as caught:
            _calibration(black_ref=(100.0,) * CHANNELS)
        self.assertIn("必须大于", str(caught.exception))

    def test_insufficient_contrast_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            _calibration(black_ref=(600.5,) * CHANNELS)
        self.assertIn("分离度", str(caught.exception))

    def test_problems_lists_every_bad_channel(self):
        white = (600.0,) * CHANNELS
        black = list((2400.0,) * CHANNELS)
        black[2] = 500.0
        # 正常构造会被拒（下面的断言证明这一点）
        with self.assertRaises(ValueError):
            LineCalibration(white_ref=white, black_ref=tuple(black))
        # 但加载已有坏文件时必须给出逐条原因，而不是一句「不合法」
        broken = _unchecked(white, black)
        problems = broken.problems()
        self.assertEqual(len(problems), 1)
        self.assertIn("通道 3", problems[0])
        self.assertFalse(broken.is_usable)


class ParameterConversionTests(unittest.TestCase):
    def test_params_round_trip(self):
        original = _calibration()
        params = calibration_to_params(original)
        self.assertEqual(len(params["white_offset"]), CHANNELS)
        restored = calibration_from_params(
            white_ref=params["white_ref"], black_ref=params["black_ref"],
            white_offset=params["white_offset"], black_offset=params["black_offset"],
        )
        for a, b in zip(restored.white_ref, original.white_ref):
            self.assertAlmostEqual(a, b, places=3)
        for a, b in zip(restored.black_ref, original.black_ref):
            self.assertAlmostEqual(a, b, places=3)

    def test_bad_offsets_are_rejected(self):
        with self.assertRaises(ValueError):
            calibration_from_params(
                white_ref=0.0, black_ref=4095.0,
                white_offset=[0.0] * 7, black_offset=[0.0] * 8,
            )


class StartupVerdictTests(unittest.TestCase):
    def test_missing_calibration_says_exactly_what_to_do(self):
        usable, reason = startup_verdict(None, file_path="/home/pi/robogame_line_calibration.json")
        self.assertFalse(usable)
        self.assertIn("方向默认值", reason)
        self.assertIn("标定", reason)
        self.assertIn("robogame_line_calibration.json", reason, "要指出保存到哪个文件")

    def test_good_calibration_is_reported_with_its_source(self):
        usable, reason = startup_verdict(_calibration(source="面板 2026-09-17"))
        self.assertTrue(usable)
        self.assertIn("面板 2026-09-17", reason)
        self.assertIn("黑白差最小", reason)

    def test_unusable_calibration_never_reports_success(self):
        white = (600.0,) * CHANNELS
        black = list((2400.0,) * CHANNELS)
        black[5] = 100.0
        usable, reason = startup_verdict(_unchecked(white, black))
        self.assertFalse(usable)
        self.assertIn("通道 6", reason)


if __name__ == "__main__":
    unittest.main()
