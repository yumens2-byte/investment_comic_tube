import unittest
from unittest.mock import MagicMock, patch

from src.market_regime import (
    VILLAIN_BULL_BRUTE,
    VILLAIN_CHAOS_REAPER,
    VILLAIN_DEBT_TITAN,
    select_villain,
)
from src.story import _build_continuity_context, build_story_state, build_storyboard

RATE_PRESSURE = {
    "TNX": {"close": 4.9, "dev_pct": 10.0},
    "VIX": {"close": 14.0, "dev_pct": -3.0},
    "NASDAQ": {"change_pct": 0.1, "dev_pct": 0.2},
    "SPX": {"change_pct": 0.1, "dev_pct": 0.2},
    "DXY": {"close": 105, "dev_pct": 2.0},
}
VOLATILE = {
    "TNX": {"close": 4.0, "dev_pct": 0.0},
    "VIX": {"close": 38.0, "dev_pct": 90.0},
    "NASDAQ": {"change_pct": -4.0, "dev_pct": -8.0},
    "SPX": {"change_pct": -3.5, "dev_pct": -7.0},
    "DXY": {"close": 103, "dev_pct": 0.0},
}
BULLISH = {
    "TNX": {"close": 4.0, "dev_pct": 0.0},
    "VIX": {"close": 12.0, "dev_pct": -10.0},
    "NASDAQ": {"change_pct": 2.5, "dev_pct": 5.0},
    "SPX": {"change_pct": 2.0, "dev_pct": 4.0},
    "DXY": {"close": 102, "dev_pct": -1.0},
}


class VillainScoringTest(unittest.TestCase):
    def test_rate_pressure_selects_debt_titan(self):
        villain, _, scores = select_villain(RATE_PRESSURE)
        self.assertEqual(villain, VILLAIN_DEBT_TITAN)
        self.assertEqual(max(scores, key=lambda k: scores[k]), VILLAIN_DEBT_TITAN)

    def test_volatility_selects_chaos_reaper(self):
        villain, _, _ = select_villain(VOLATILE)
        self.assertEqual(villain, VILLAIN_CHAOS_REAPER)

    def test_momentum_selects_bull_brute(self):
        villain, _, _ = select_villain(BULLISH)
        self.assertEqual(villain, VILLAIN_BULL_BRUTE)

    def test_all_three_villains_are_reachable(self):
        picked = {select_villain(m)[0] for m in (RATE_PRESSURE, VOLATILE, BULLISH)}
        # 고정 임계값 방식에서 Debt Titan 만 8회 연속 나온 문제의 회귀 방지
        self.assertEqual(len(picked), 3)

    def test_missing_metrics_do_not_crash(self):
        empty = {k: {"close": None, "change_pct": None, "sma20": None, "dev_pct": None}
                 for k in ("TNX", "VIX", "NASDAQ", "SPX", "DXY")}
        villain, theme, _ = select_villain(empty)
        self.assertIn(villain, (VILLAIN_DEBT_TITAN, VILLAIN_CHAOS_REAPER, VILLAIN_BULL_BRUTE))
        self.assertTrue(theme)


class StoryStateTest(unittest.TestCase):
    def test_first_episode_starts_streak_at_one(self):
        state = build_story_state("Debt Titan", None, ["a", "b", "마지막 문장"])
        self.assertEqual(state["villain_streak"], 1)
        self.assertEqual(state["villain"], "Debt Titan")
        self.assertEqual(state["unresolved"], "마지막 문장")

    def test_same_villain_increments_streak(self):
        prev = {"villain": "Debt Titan", "story_state": {"villain_streak": 3}}
        state = build_story_state("Debt Titan", prev, ["x"])
        self.assertEqual(state["villain_streak"], 4)

    def test_different_villain_resets_streak(self):
        prev = {"villain": "Debt Titan", "story_state": {"villain_streak": 5}}
        state = build_story_state("Chaos Reaper", prev, ["x"])
        self.assertEqual(state["villain_streak"], 1)


class ContinuityContextTest(unittest.TestCase):
    def test_no_prev_state_says_first_episode(self):
        text = _build_continuity_context(None, RATE_PRESSURE)
        self.assertIn("첫 회차", text)

    def test_mentions_previous_villain_and_unresolved(self):
        prev = {
            "episode": 110,
            "villain": "Debt Titan",
            "story_state": {"villain_streak": 2, "unresolved": "아직 끝나지 않았다"},
            "market_snapshot": {"TNX": {"close": 4.70}, "VIX": {"close": 14.0}},
        }
        text = _build_continuity_context(prev, RATE_PRESSURE, "Debt Titan")

        self.assertIn("110화", text)
        self.assertIn("Debt Titan", text)
        self.assertIn("아직 끝나지 않았다", text)
        # 전일 대비 변화 서사 (4.70 -> 4.9 상승)
        self.assertIn("올랐다", text)

    def test_streak_is_mentioned_when_same_villain_repeats(self):
        prev = {
            "episode": 110,
            "villain": "Debt Titan",
            "story_state": {"villain_streak": 3},
        }
        text = _build_continuity_context(prev, RATE_PRESSURE, "Debt Titan")
        self.assertIn("연속 등장", text)


class StoryboardContinuityTest(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_fallback_narration_reflects_villain_change(self):
        prev = {"episode": 110, "villain": "Chaos Reaper"}
        storyboard, _, degraded = build_storyboard(
            RATE_PRESSURE, "Debt Titan", "긴축", prev
        )

        self.assertEqual(degraded, "story:no_api_key")
        self.assertIn("Chaos Reaper", storyboard[1]["narration"])
        self.assertIn("Debt Titan", storyboard[1]["narration"])

    @patch.dict("os.environ", {}, clear=True)
    def test_fallback_narration_reflects_same_villain(self):
        prev = {"episode": 110, "villain": "Debt Titan"}
        storyboard, _, _ = build_storyboard(RATE_PRESSURE, "Debt Titan", "긴축", prev)

        self.assertIn("아직 물러나지 않았다", storyboard[1]["narration"])

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_prompt_carries_previous_episode_context(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = MagicMock(
            text='["1","2","3","4","5","6"]'
        )
        prev = {
            "episode": 110,
            "villain": "Debt Titan",
            "story_state": {"villain_streak": 2, "unresolved": "미해결 위협"},
            "market_snapshot": {"TNX": {"close": 4.70}},
        }

        build_storyboard(RATE_PRESSURE, "Debt Titan", "긴축", prev)

        prompt = client_cls.return_value.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("이전 회차 맥락", prompt)
        self.assertIn("미해결 위협", prompt)
        self.assertIn("클리프행어", prompt)


if __name__ == "__main__":
    unittest.main()
