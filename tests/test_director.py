import unittest
from unittest.mock import MagicMock, patch

from src.director import generate_connected_script

# 금리 압력 우세: TNX가 20일 이평 위로 크게 튐
MARKET_DATA_HIGH_TNX = {
    "TNX": {"close": 4.8, "change_pct": 0.5, "sma20": 4.4, "dev_pct": 9.0},
    "VIX": {"close": 15.0, "change_pct": 0.0, "sma20": 15.0, "dev_pct": 0.0},
    "NASDAQ": {"close": 26000, "change_pct": 0.0, "sma20": 26000, "dev_pct": 0.0},
    "SPX": {"close": 5800, "change_pct": 0.0, "sma20": 5800, "dev_pct": 0.0},
    "DXY": {"close": 104, "change_pct": 0.1, "sma20": 103, "dev_pct": 1.0},
}
# 변동성 우세: VIX 급등 + 지수 급락
MARKET_DATA_HIGH_VIX = {
    "TNX": {"close": 4.0, "change_pct": 0.0, "sma20": 4.0, "dev_pct": 0.0},
    "VIX": {"close": 35.0, "change_pct": 20.0, "sma20": 18.0, "dev_pct": 94.0},
    "NASDAQ": {"close": 24000, "change_pct": -3.5, "sma20": 26000, "dev_pct": -7.7},
    "SPX": {"close": 5400, "change_pct": -3.0, "sma20": 5800, "dev_pct": -6.9},
    "DXY": {"close": 103, "change_pct": 0.0, "sma20": 103, "dev_pct": 0.0},
}
# 모멘텀 우세: 지수 상승 + 이평 상회, 금리/변동성 잠잠
MARKET_DATA_CALM = {
    "TNX": {"close": 4.0, "change_pct": 0.0, "sma20": 4.0, "dev_pct": 0.0},
    "VIX": {"close": 13.0, "change_pct": -2.0, "sma20": 14.0, "dev_pct": -7.0},
    "NASDAQ": {"close": 27000, "change_pct": 2.0, "sma20": 26000, "dev_pct": 3.8},
    "SPX": {"close": 6000, "change_pct": 1.8, "sma20": 5800, "dev_pct": 3.4},
    "DXY": {"close": 102, "change_pct": -0.2, "sma20": 103, "dev_pct": -1.0},
}


class VillainSelectionTest(unittest.TestCase):
    @patch("src.director.start_episode", return_value="ep-0103-abcd1234")
    @patch("src.director.fetch_latest_episode_state", return_value={"episode": 102})
    @patch.dict("os.environ", {}, clear=True)
    def test_high_tnx_selects_debt_titan(self, _fetch, _start):
        script = generate_connected_script(MARKET_DATA_HIGH_TNX)
        self.assertEqual(script["villain"], "Debt Titan")
        self.assertEqual(len(script["storyboard"]), 6)
        self.assertEqual(
            [b["beat"] for b in script["storyboard"]],
            ["HOOK", "THREAT", "IMPACT", "HERO", "CLASH", "LESSON"],
        )
        self.assertEqual(script["episode"], 103)
        self.assertEqual(script["episode_id"], "ep-0103-abcd1234")

    @patch("src.director.start_episode", return_value="ep-0103-abcd1234")
    @patch("src.director.fetch_latest_episode_state", return_value={"episode": 102})
    @patch.dict("os.environ", {}, clear=True)
    def test_high_vix_selects_chaos_reaper(self, _fetch, _start):
        script = generate_connected_script(MARKET_DATA_HIGH_VIX)
        self.assertEqual(script["villain"], "Chaos Reaper")

    @patch("src.director.start_episode", return_value="ep-0103-abcd1234")
    @patch("src.director.fetch_latest_episode_state", return_value={"episode": 102})
    @patch.dict("os.environ", {}, clear=True)
    def test_calm_market_selects_bull_brute(self, _fetch, _start):
        script = generate_connected_script(MARKET_DATA_CALM)
        self.assertEqual(script["villain"], "Bull Brute")


class NarrationPolishTest(unittest.TestCase):
    @patch("src.director.start_episode", return_value="ep-0103-abcd1234")
    @patch("src.director.fetch_latest_episode_state", return_value={"episode": 102})
    @patch.dict("os.environ", {}, clear=True)
    def test_no_api_key_keeps_rule_based_narration(self, _fetch, _start):
        script = generate_connected_script(MARKET_DATA_HIGH_TNX)
        self.assertIn("Debt Titan", script["narration"])
        self.assertIn("narration:no_api_key", script["degraded_reason"])
        self.assertIn("story:no_api_key", script["degraded_reason"])

    @patch("src.director.start_episode", return_value="ep-0103-abcd1234")
    @patch("src.director.fetch_latest_episode_state", return_value={"episode": 102})
    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True)
    @patch("google.genai.Client")
    def test_polish_success_replaces_narration(self, client_cls, _fetch, _start):
        client_cls.return_value.models.generate_content.return_value = MagicMock(
            text="Debt Titan이 시장을 뒤흔든다!"
        )

        script = generate_connected_script(MARKET_DATA_HIGH_TNX)

        self.assertEqual(script["narration"], "Debt Titan이 시장을 뒤흔든다!")
        self.assertEqual(
            client_cls.return_value.models.generate_content.call_args.kwargs["model"],
            "gemini-3.6-flash",
        )

    @patch("src.director.start_episode", return_value="ep-0103-abcd1234")
    @patch("src.director.fetch_latest_episode_state", return_value={"episode": 102})
    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True)
    @patch("google.genai.Client")
    def test_polish_failure_falls_back_to_rule_based(self, client_cls, _fetch, _start):
        client_cls.return_value.models.generate_content.side_effect = RuntimeError("api down")

        script = generate_connected_script(MARKET_DATA_HIGH_TNX)

        self.assertIn("오늘 시장 지표 분석 결과", script["narration"])
        self.assertIn("narration:RuntimeError", script["degraded_reason"])


if __name__ == "__main__":
    unittest.main()
