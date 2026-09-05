"""5일 운영 리뷰 후 고도화 6건 검증."""

import logging
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src import drive_manager
from src.market_regime import MAX_VILLAIN_STREAK, select_villain
from src.story import ENDING_TYPES, build_storyboard, select_ending_type

MARKET = {"TNX": {"close": 4.8, "dev_pct": 1.5}, "VIX": {"close": 14.5, "dev_pct": -5},
          "NASDAQ": {"change_pct": -0.3, "dev_pct": 1}, "SPX": {"change_pct": -0.4, "dev_pct": 1},
          "DXY": {"close": 103, "dev_pct": 0}}


def _client(rows):
    c = MagicMock()
    chain = c.table.return_value.select.return_value.in_.return_value
    chain.order.return_value.limit.return_value.execute.return_value = MagicMock(data=rows)
    chain.gte.return_value.limit.return_value.execute.return_value = MagicMock(data=rows)
    return c


# ---------- 1) 클리프행어 다양화 ----------
class EndingRotationTest(unittest.TestCase):
    def test_first_episode_starts_with_first_type(self):
        self.assertEqual(select_ending_type(None), ENDING_TYPES[0])

    def test_rotates_to_next_type(self):
        for i, t in enumerate(ENDING_TYPES):
            prev = {"story_state": {"ending_type": t}}
            self.assertEqual(select_ending_type(prev), ENDING_TYPES[(i + 1) % len(ENDING_TYPES)])

    @patch.dict("os.environ", {}, clear=True)
    def test_ending_type_persisted_in_story_state(self):
        _, state, _ = build_storyboard(MARKET, "Debt Titan", "긴축", None)
        self.assertIn(state["ending_type"], ENDING_TYPES)

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_recent_cliffhangers_injected_as_avoid_list(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = MagicMock(
            text='["공포지수 경고등이 켜졌다","2","3","4","5","6"]'
        )
        prev = {"recent_cliffhangers": ["과연 EDT는 방어선을 지켜낼까요?", "또 다른 마무리"]}

        build_storyboard(MARKET, "Debt Titan", "긴축", prev)

        prompt = client_cls.return_value.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("과연 EDT는 방어선을 지켜낼까요?", prompt)
        self.assertIn("겹치면 안 된다", prompt)
        self.assertIn("상투구를 반복하지 마라", prompt)


# ---------- 2) 빌런 편중 완화 ----------
class VillainRotationTest(unittest.TestCase):
    def test_scores_are_floored_at_zero(self):
        # 5일 운영에서 Chaos Reaper 가 -7~-13 으로 구조적으로 못 이기던 문제
        _, _, scores = select_villain(MARKET)
        for v in scores.values():
            self.assertGreaterEqual(v, 0.0)

    def test_same_villain_rotates_after_streak_limit(self):
        prev = {"villain": "Debt Titan", "story_state": {"villain_streak": MAX_VILLAIN_STREAK}}
        villain, _, _ = select_villain(MARKET, prev)
        self.assertNotEqual(villain, "Debt Titan")

    def test_below_streak_limit_keeps_winner(self):
        prev = {"villain": "Debt Titan", "story_state": {"villain_streak": MAX_VILLAIN_STREAK - 1}}
        villain, _, _ = select_villain(MARKET, prev)
        self.assertEqual(villain, "Debt Titan")

    def test_different_prev_villain_does_not_rotate(self):
        prev = {"villain": "Bull Brute", "story_state": {"villain_streak": 5}}
        villain, _, _ = select_villain(MARKET, prev)
        self.assertEqual(villain, "Debt Titan")


# ---------- 3) TTS 재시도 ----------
class TtsRetryTest(unittest.TestCase):
    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_transient_failure_recovers_on_retry(self, client_cls):
        from src.tts import synthesize_narrations

        part = MagicMock(); part.inline_data.data = b"\x00\x01" * 1000
        cand = MagicMock(); cand.content.parts = [part]
        ok = MagicMock(); ok.candidates = [cand]
        client_cls.return_value.models.generate_content.side_effect = [RuntimeError("blip"), ok]

        with tempfile.TemporaryDirectory() as d:
            paths, reason = synthesize_narrations(["한 줄"], output_dir=d)

        # Ep.4 사례: 한 비트만 무음 -> 재시도로 살려야 한다
        self.assertIsNotNone(paths[0])
        self.assertIsNone(reason)

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_quota_exhaustion_does_not_retry(self, client_cls):
        from src.tts import synthesize_narrations

        client_cls.return_value.models.generate_content.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED spending cap"
        )
        with tempfile.TemporaryDirectory() as d:
            synthesize_narrations(["a", "b"], output_dir=d)

        # 한도 소진은 재시도해도 소용없으므로 1회만 호출하고 전체 중단
        self.assertEqual(client_cls.return_value.models.generate_content.call_count, 1)


# ---------- 4) 당일 중복 발행 방지 ----------
class DuplicatePublishTest(unittest.TestCase):
    @patch("src.drive_manager.get_client")
    def test_detects_publication_today(self, get_client):
        get_client.return_value = _client([{"episode_no": 5, "market_as_of": "2026-09-05T02:48:00+00:00"}])
        self.assertTrue(drive_manager.has_published_today())

    @patch("src.drive_manager.get_client")
    def test_no_publication_today(self, get_client):
        get_client.return_value = _client([])
        self.assertFalse(drive_manager.has_published_today())

    @patch("src.drive_manager.get_client", side_effect=RuntimeError("db down"))
    def test_db_failure_does_not_block(self, _gc):
        # 중복 방지는 보조 장치이므로 조회 실패가 발행을 막으면 안 된다
        self.assertFalse(drive_manager.has_published_today())

    @patch("src.drive_manager.has_published_today", return_value=True)
    def test_validation_aborts_when_already_published(self, _hp):
        from src.validation import DuplicatePublish, validate_not_published_today

        with self.assertRaises(DuplicatePublish):
            validate_not_published_today()

    @patch("src.drive_manager.has_published_today", return_value=True)
    @patch.dict("os.environ", {"STRICT_VALIDATION": "false"}, clear=True)
    def test_bypass_mode_allows_republish(self, _hp):
        from src.validation import validate_not_published_today

        validate_not_published_today()  # 경고만


# ---------- 5) 실패 회차 번호 재사용 ----------
class FailedEpisodeNumberingTest(unittest.TestCase):
    @patch("src.drive_manager.get_client")
    def test_only_published_statuses_are_queried(self, get_client):
        client = _client([{"episode_no": 4, "status": "published"}])
        get_client.return_value = client

        state = drive_manager.fetch_latest_episode_state()

        # Ep.1 실패 -> YouTube 가 Ep.2 부터 시작하던 gap 방지
        client.table.return_value.select.return_value.in_.assert_called_once_with(
            "status", drive_manager.PUBLISHED_STATUSES
        )
        self.assertEqual(state["episode"], 4)

    def test_published_statuses_exclude_failed(self):
        self.assertNotIn("failed", drive_manager.PUBLISHED_STATUSES)
        self.assertNotIn("aborted_validation", drive_manager.PUBLISHED_STATUSES)
        self.assertIn("published", drive_manager.PUBLISHED_STATUSES)
        self.assertIn("published_degraded", drive_manager.PUBLISHED_STATUSES)

    @patch("src.drive_manager.get_client")
    def test_recent_cliffhangers_returns_last_lines(self, get_client):
        get_client.return_value = _client([
            {"story_state": {"unresolved": "첫째"}},
            {"story_state": {"unresolved": "둘째"}},
            {"story_state": {}},
        ])
        self.assertEqual(drive_manager.fetch_recent_cliffhangers(), ["첫째", "둘째"])


# ---------- 6) 실패 시 step_runs 상태 갱신 ----------
class StepFailureRecordingTest(unittest.TestCase):
    def tearDown(self):
        for h in logging.getLogger().handlers[:]:
            h.close(); logging.getLogger().removeHandler(h)

    @patch("main.validate_not_published_today")
    @patch("main.validate_render_environment")
    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-render")
    @patch("main.update_episode")
    @patch("main.render_video", side_effect=RuntimeError("ffmpeg exploded"))
    @patch("main.synthesize_narrations", return_value=([None] * 6, "tts:no_api_key"))
    @patch("main.generate_scene_images", return_value=([None] * 4, "image:no_api_key"))
    @patch("main.generate_connected_script")
    @patch("main.fetch_market_data")
    def test_running_step_marked_failed_on_crash(
        self, fetch, script, _img, _tts, _render, _upd, _start, finish, _env, _dup
    ):
        import main
        fetch.return_value = {n: {"close": 1.0, "change_pct": 0.1} for n in ("TNX", "VIX", "NASDAQ", "SPX", "DXY")}
        script.return_value = {
            "episode": 1, "villain": "Debt Titan", "narration": "n", "episode_id": "ep-1",
            "storyboard": [{"beat": b, "scene": "s", "narration": "n"} for b in
                           ("HOOK", "THREAT", "IMPACT", "HERO", "CLASH", "LESSON")],
            "degraded_reason": None,
        }
        with tempfile.TemporaryDirectory() as d, patch.dict("os.environ", {"LOG_DIR": d}):
            self.assertEqual(main.main(), 1)

        # Ep.1 의 upload 가 'running' 으로 영구 방치되던 문제
        failed_calls = [c for c in finish.call_args_list if c.args[1] == "failed"]
        self.assertEqual(len(failed_calls), 1)
        self.assertEqual(failed_calls[0].args[0], "step-render")
        self.assertIn("RuntimeError", failed_calls[0].kwargs["error_code"])


if __name__ == "__main__":
    unittest.main()
