import unittest
from unittest.mock import patch

from src.validation import (
    EXPECTED_BEAT_COUNT,
    REQUIRED_INDICATORS,
    MarketDataIncomplete,
    StoryboardIncomplete,
    validate_market_data,
    validate_storyboard,
)


def _metric(close=4.5, change=0.3):
    return {"close": close, "change_pct": change, "sma20": 4.4, "dev_pct": 2.0}


def _good_market(**overrides):
    data = {name: _metric() for name in REQUIRED_INDICATORS}
    data["GOLD"] = _metric(2400.0, 0.5)
    data["OIL"] = _metric(78.0, -0.4)
    data.update(overrides)
    return data


def _good_storyboard():
    beats = ["HOOK", "THREAT", "IMPACT", "HERO", "CLASH", "LESSON"]
    return [{"beat": b, "scene": f"scene {i}", "narration": f"내레이션 {i}"} for i, b in enumerate(beats)]


class MarketValidationTest(unittest.TestCase):
    def test_complete_data_passes(self):
        validate_market_data(_good_market())  # 예외 없으면 통과

    def test_missing_indicator_aborts(self):
        data = _good_market()
        del data["VIX"]
        with self.assertRaises(MarketDataIncomplete) as ctx:
            validate_market_data(data)
        self.assertIn("VIX", str(ctx.exception))

    def test_none_close_aborts(self):
        data = _good_market(TNX={"close": None, "change_pct": None, "sma20": None, "dev_pct": None})
        with self.assertRaises(MarketDataIncomplete) as ctx:
            validate_market_data(data)
        self.assertIn("TNX.close", str(ctx.exception))

    def test_all_indicators_failed_aborts(self):
        empty = {n: {"close": None, "change_pct": None} for n in REQUIRED_INDICATORS}
        with self.assertRaises(MarketDataIncomplete):
            validate_market_data(empty)

    def test_empty_dict_aborts(self):
        with self.assertRaises(MarketDataIncomplete):
            validate_market_data({})

    def test_non_numeric_value_aborts(self):
        data = _good_market(SPX={"close": "확인불가", "change_pct": 0.1})
        with self.assertRaises(MarketDataIncomplete) as ctx:
            validate_market_data(data)
        self.assertIn("SPX.close", str(ctx.exception))

    def test_missing_optional_indicator_still_passes(self):
        data = _good_market()
        data["GOLD"] = {"close": None, "change_pct": None}
        del data["OIL"]
        validate_market_data(data)  # 선택 지표는 중단 사유가 아니다

    def test_missing_sma_fields_still_pass(self):
        # 이력이 짧아 sma20/dev_pct 가 없어도 절대값 폴백이 가능하므로 통과해야 한다
        data = {n: {"close": 1.0, "change_pct": 0.1, "sma20": None, "dev_pct": None}
                for n in REQUIRED_INDICATORS}
        validate_market_data(data)

    @patch.dict("os.environ", {"STRICT_VALIDATION": "false"}, clear=True)
    def test_bypass_mode_allows_partial_data(self):
        data = _good_market(TNX={"close": None, "change_pct": None})
        validate_market_data(data)  # 완화 모드에서는 경고만

    def test_completely_empty_aborts_even_in_bypass_mode(self):
        # 완전 공백은 하류에서 어차피 깨지므로 완화 모드와 무관하게 항상 중단한다
        with patch.dict("os.environ", {"STRICT_VALIDATION": "false"}, clear=True), \
             self.assertRaises(MarketDataIncomplete):
            validate_market_data({})


class StoryboardValidationTest(unittest.TestCase):
    def test_complete_storyboard_passes(self):
        validate_storyboard(_good_storyboard())

    def test_empty_storyboard_aborts(self):
        with self.assertRaises(StoryboardIncomplete):
            validate_storyboard([])

    def test_wrong_beat_count_aborts(self):
        with self.assertRaises(StoryboardIncomplete) as ctx:
            validate_storyboard(_good_storyboard()[:4])
        self.assertIn("비트 수", str(ctx.exception))

    def test_blank_narration_aborts(self):
        sb = _good_storyboard()
        sb[2]["narration"] = "   "
        with self.assertRaises(StoryboardIncomplete) as ctx:
            validate_storyboard(sb)
        self.assertIn("beat[2].narration", str(ctx.exception))

    def test_missing_scene_aborts(self):
        sb = _good_storyboard()
        sb[0]["scene"] = ""
        with self.assertRaises(StoryboardIncomplete):
            validate_storyboard(sb)

    def test_expected_beat_count_is_six(self):
        self.assertEqual(EXPECTED_BEAT_COUNT, 6)

    @patch.dict("os.environ", {"STRICT_VALIDATION": "false"}, clear=True)
    def test_bypass_mode_allows_incomplete_storyboard(self):
        validate_storyboard(_good_storyboard()[:3])  # 완화 모드에서는 경고만

    def test_empty_storyboard_aborts_even_in_bypass_mode(self):
        with patch.dict("os.environ", {"STRICT_VALIDATION": "false"}, clear=True), \
             self.assertRaises(StoryboardIncomplete):
            validate_storyboard([])


if __name__ == "__main__":
    unittest.main()
