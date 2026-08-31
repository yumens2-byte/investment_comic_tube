import unittest
from unittest.mock import MagicMock, patch

from src.director import generate_connected_script

MARKET_DATA_HIGH_TNX = {"TNX": {"close": 4.8}, "VIX": {"close": 15.0}}
MARKET_DATA_HIGH_VIX = {"TNX": {"close": 4.0}, "VIX": {"close": 30.0}}
MARKET_DATA_CALM = {"TNX": {"close": 4.0}, "VIX": {"close": 15.0}}


class VillainSelectionTest(unittest.TestCase):
    @patch("src.director.start_episode", return_value="ep-0103-abcd1234")
    @patch("src.director.fetch_latest_episode_state", return_value={"episode": 102})
    @patch.dict("os.environ", {}, clear=True)
    def test_high_tnx_selects_debt_titan(self, _fetch, _start):
        script = generate_connected_script(MARKET_DATA_HIGH_TNX)
        self.assertEqual(script["villain"], "Debt Titan")
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
        self.assertEqual(script["degraded_reason"], "narration:no_api_key")

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
        self.assertIsNone(script["degraded_reason"])
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
        self.assertEqual(script["degraded_reason"], "narration:RuntimeError")


if __name__ == "__main__":
    unittest.main()
