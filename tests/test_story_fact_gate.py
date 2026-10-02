"""P1/P2 (2026-10-03): 사실 토큰 게이트 품질 개선 회귀 테스트.

배경: Ep.31·32 가 안전 모드 사실 검사에서 대본 2회 연속 거부 → 6문장 7~13자 폴백
(약 20초)으로 published_degraded 발행됐다.
"""
import json
import unittest
from unittest.mock import MagicMock, Mock, patch

from src import drive_manager
from src.content_quality import ContentQualityError, MIN_PUBLISH_DURATION_SEC, validate_rendered_video
from src.market_facts import check_line, prompt_contract, resolve, strip_label_before_fact
from src.pipeline_control import activate, reset
from src.story import (
    BEAT_COUNT,
    SAFE_FALLBACK_TEXT,
    _build_continuity_context,
    _build_story_prompt,
    _generate_narrations,
    is_fallback_line,
)
from src.validation import ValidationError

# Ep.31 실제 운영 snapshot 일부 (DB pipeline_slots 2026-10-01)
SNAPSHOT = {
    "TNX": {"close": 5.29, "change_pct": 0.72},
    "VIX": {"close": 16.34, "change_pct": 1.87},
    "NASDAQ": {"close": 26861.06, "change_pct": 0.24},
    "SPX": {"close": 7651.54, "change_pct": -0.25},
    "DXY": {"close": 101.63, "change_pct": 0.17},
    "GOLD": {"close": 4221.4, "change_pct": 0.83},
    "OIL": {"close": 89.43, "change_pct": -1.09},
}

GOOD = [
    "뎁트타이탄이 방어선을 노린다",
    "{{FACT:TNX.close}}와 {{FACT:VIX.close}}가 동시에 압박한다",
    "금리 부담이 커지자 하락장의 그림자가 짙어진다",
    "EDT가 체인소를 들고 투자자의 구원자로 선다",
    "EDT는 추격 대신 비중을 줄여 분산을 지킨다",
    "영원한 상승은 없다, 내일 방어선은 어디인가",
]


class FactGateFalsePositiveTest(unittest.TestCase):
    def test_ordinary_words_and_label_numerals_pass(self):
        for line in ["EDT가 투자자의 구원자로 등장한다", "영원한 상승은 없다", "구원의 손길",
                     "일원화된 방어", "지배력이 흔들린다", "10년물 금리 {{FACT:TNX.close}}가 압박한다",
                     "S&P 500 {{FACT:SPX.change_pct}}에 흔들린다", "하락장의 그림자가 짙어진다"]:
            with self.subTest(line=line):
                self.assertIsNone(check_line(line))

    def test_quantities_units_and_duplicate_direction_still_rejected(self):
        cases = {
            "증시 3% 상승": "unregistered_market_numeric_claim",
            "세 배 상승": "unregistered_market_numeric_claim",
            "두배로 뛴다": "unregistered_market_numeric_claim",
            "삼십 퍼센트 빠졌다": "unregistered_market_numeric_claim",
            "이십만원이 사라졌다": "unregistered_market_numeric_claim",
            "오 퍼센트": "unregistered_market_numeric_claim",
            "달러인덱스 100.22를 깨부술": "unregistered_market_numeric_claim",
            "{{FACT:SPX.change_pct}}%": "market_unit_outside_fact",
            "{{FACT:SPX.change_pct}} 상승": "market_direction_duplicated_after_fact",
            "{{FACT:SPX.change_pct}}로 하락": "market_direction_duplicated_after_fact",
        }
        for line, reason in cases.items():
            with self.subTest(line=line):
                self.assertEqual(check_line(line), reason)
                with self.assertRaises(ValidationError):
                    resolve(line, SNAPSHOT)

    def test_label_before_token_is_not_duplicated(self):
        self.assertEqual(resolve("10년물 금리가 {{FACT:TNX.close}}로 치솟았다", SNAPSHOT),
                         "미 10년물 금리 5.29%로 치솟았다")
        self.assertEqual(resolve("공포지수 {{FACT:VIX.close}}가 경고한다", SNAPSHOT), "VIX 16.34가 경고한다")
        # 단어 내부(지금)의 '금'은 지표명으로 보지 않는다
        self.assertEqual(strip_label_before_fact("지금 {{FACT:GOLD.close}}"), "지금 {{FACT:GOLD.close}}")

    def test_contract_lists_tokens_and_directions_without_raw_numbers(self):
        contract = prompt_contract(SNAPSHOT)
        self.assertIn("{{FACT:VIX.change_pct}}", contract)
        self.assertIn("VIX=상승", contract)
        self.assertIn("S&P 500=하락", contract)
        for raw in ("5.29", "16.34", "7651", "1.87"):
            self.assertNotIn(raw, contract)


class SafePromptTest(unittest.TestCase):
    def test_safe_prompt_has_no_raw_market_numbers(self):
        prev = {"episode": 30, "villain": "Debt Titan",
                "story_state": {"unresolved": "금 4207.0 돌파 직전, 손절선은?"},
                "market_snapshot": {"VIX": {"close": 15.1}, "TNX": {"close": 5.2}}}
        prompt = _build_story_prompt("Debt Titan", "긴축", SNAPSHOT, prev, "B", "QUESTION",
                                     ["달러 101.2까지 솟구쳤다"], safe=True)
        for raw in ("5.29", "16.34", "4207", "101.2", "15.1"):
            self.assertNotIn(raw, prompt)
        self.assertIn("사실 토큰", prompt)
        self.assertNotIn("오늘 수치 두 개를 비교", prompt)

    def test_legacy_prompt_unchanged_when_safety_off(self):
        prompt = _build_story_prompt("Debt Titan", "긴축", SNAPSHOT, None, "B", "QUESTION", [], safe=False)
        self.assertIn("미국10년물금리 5.29", prompt)
        self.assertNotIn("[수치 규칙]", prompt)

    def test_fallback_lines_are_not_carried_into_continuity(self):
        prev = {"episode": 31, "villain": "Debt Titan",
                "story_state": {"unresolved": "다음 시장 신호를 기다린다"}}
        self.assertTrue(is_fallback_line("다음 시장 신호를 기다린다"))
        self.assertNotIn("다음 시장 신호를 기다린다", _build_continuity_context(prev, SNAPSHOT, "Debt Titan"))
        prompt = _build_story_prompt("Debt Titan", "긴축", SNAPSHOT, prev, "B", "QUESTION",
                                     ["다음 시장 신호를 기다린다", "다른 마무리"], safe=True)
        self.assertNotIn("다음 시장 신호를 기다린다", prompt)
        self.assertIn("다른 마무리", prompt)


@patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
@patch("google.genai.Client")
class SafeNarrationGenerationTest(unittest.TestCase):
    def setUp(self):
        self.control = Mock()
        self.token = activate(self.control)
        self.addCleanup(reset, self.token)

    def _respond(self, client_cls, *responses):
        client_cls.return_value.models.generate_content.side_effect = [
            MagicMock(text=json.dumps(lines, ensure_ascii=False)) for lines in responses
        ]
        return client_cls.return_value.models.generate_content

    def test_valid_first_response_uses_single_call_and_resolves_tokens(self, client_cls):
        call = self._respond(client_cls, GOOD)
        lines, degraded = _generate_narrations("Debt Titan", "긴축", SNAPSHOT, hook_type="B")
        self.assertIsNone(degraded)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(lines[1], "미 10년물 금리 5.29%와 VIX 16.34가 동시에 압박한다")
        self.assertEqual(lines[3], GOOD[3])  # '구원자' 오탐 해소

    def test_only_rejected_line_is_repaired_within_two_calls(self, client_cls):
        bad = list(GOOD)
        bad[2] = "금리가 4% 넘게 오르자 공포가 번진다"
        fixed = list(GOOD)
        fixed[2] = "금리 압박이 커지자 공포가 번진다"
        call = self._respond(client_cls, bad, fixed)
        lines, degraded = _generate_narrations("Debt Titan", "긴축", SNAPSHOT, hook_type="B")
        self.assertIsNone(degraded)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(lines[2], fixed[2])
        repair_prompt = call.call_args_list[1].kwargs["contents"]
        self.assertIn("3번=unregistered_market_numeric_claim", repair_prompt)
        self.assertEqual(self.control.reserve.call_count, 2)

    def test_passing_lines_from_first_response_survive_repair_failure_elsewhere(self, client_cls):
        bad = list(GOOD)
        bad[4] = "{{FACT:SPX.change_pct}} 하락에 비중을 줄인다"
        second = list(GOOD)
        second[0] = "너무 짧다"  # 2차에서 훅이 깨져도 1차 훅 유지
        second[4] = "EDT는 손절 기준부터 다시 확인한다"
        self._respond(client_cls, bad, second)
        lines, degraded = _generate_narrations("Debt Titan", "긴축", SNAPSHOT, hook_type="B")
        self.assertIsNone(degraded)
        self.assertEqual(lines[0], GOOD[0])
        self.assertEqual(lines[4], second[4])

    def test_hook_rejected_twice_uses_rule_hook(self, client_cls):
        bad = list(GOOD)
        bad[0] = "짧다"
        self._respond(client_cls, bad, bad)
        lines, degraded = _generate_narrations("Debt Titan", "긴축", SNAPSHOT, hook_type="B")
        self.assertIsNone(degraded)
        self.assertEqual(lines[0], "뎁트타이탄이 방어선을 노린다")

    def test_body_rejected_twice_returns_blockable_fallback(self, client_cls):
        bad = list(GOOD)
        bad[2] = "나스닥이 세 배 흔들린다"
        self._respond(client_cls, bad, bad)
        lines, degraded = _generate_narrations("Debt Titan", "긴축", SNAPSHOT, hook_type="B")
        self.assertEqual(degraded, "story:fact_gate_rejected")
        self.assertEqual(len(lines), BEAT_COUNT)
        self.assertEqual(lines[0], SAFE_FALLBACK_TEXT[0])

    def test_caption_overflow_is_rejected_before_paid_media(self, client_cls):
        bad = list(GOOD)
        bad[1] = ("{{FACT:TNX.close}}와 {{FACT:VIX.close}}가 동시에 치솟으며 시장 전체를 "
                  "거대한 공포로 덮어버리고 투자자들의 마음까지 흔들어 놓는다")
        call = self._respond(client_cls, bad, GOOD)
        lines, degraded = _generate_narrations("Debt Titan", "긴축", SNAPSHOT, hook_type="B")
        self.assertIsNone(degraded)
        self.assertIn("2번=caption_over_3_lines", call.call_args_list[1].kwargs["contents"])

    def test_malformed_twice_returns_fallback_reason(self, client_cls):
        client_cls.return_value.models.generate_content.side_effect = [
            MagicMock(text="쓰레기"), MagicMock(text="또 쓰레기")]
        _lines, degraded = _generate_narrations("Debt Titan", "긴축", SNAPSHOT, hook_type="B")
        self.assertEqual(degraded, "story:fact_gate_rejected")


class DirectorFallbackBlockTest(unittest.TestCase):
    @patch("src.director.start_episode")
    @patch("src.director.build_storyboard")
    @patch("src.director._polish_narration", return_value=("훅", None))
    @patch("src.director.select_villain", return_value=("Debt Titan", "긴축", {}))
    @patch("src.director.fetch_recent_cliffhangers", return_value=[])
    @patch("src.director.fetch_latest_episode_state", return_value={"episode": 32})
    def test_safe_mode_fallback_story_is_not_persisted_or_published(
            self, _state, _recent, _villain, _polish, storyboard, start):
        from src.director import generate_connected_script
        storyboard.return_value = ([{"narration": "x"}] * 6, {"villain_streak": 1}, "story:fact_gate_rejected")
        token = activate(Mock())
        try:
            with self.assertRaises(ValidationError) as ctx:
                generate_connected_script(SNAPSHOT)
        finally:
            reset(token)
        self.assertIn("story_fallback_blocked:story:fact_gate_rejected", str(ctx.exception))
        start.assert_not_called()

    @patch("src.director.start_episode", return_value="ep-33")
    @patch("src.director.build_storyboard")
    @patch("src.director._polish_narration", return_value=("훅", None))
    @patch("src.director.select_villain", return_value=("Debt Titan", "긴축", {}))
    @patch("src.director.fetch_recent_cliffhangers", return_value=[])
    @patch("src.director.fetch_latest_episode_state", return_value={"episode": 32})
    def test_legacy_mode_keeps_degraded_publication_behavior(
            self, _state, _recent, _villain, _polish, storyboard, start):
        from src.director import generate_connected_script
        storyboard.return_value = ([{"narration": "x"}] * 6, {"villain_streak": 1}, "story:malformed_response")
        script = generate_connected_script({"VIX": {"close": 16}})
        self.assertEqual(script["episode_id"], "ep-33")
        self.assertIn("story:malformed_response", script["degraded_reason"])


def _rows_client(rows):
    client = MagicMock()
    chain = client.table.return_value.select.return_value.in_.return_value
    chain.order.return_value.limit.return_value.execute.return_value = MagicMock(data=rows)
    return client


class FallbackContinuityPersistenceTest(unittest.TestCase):
    @patch("src.drive_manager.get_client")
    def test_recent_cliffhangers_skip_story_fallback_episodes(self, get_client):
        get_client.return_value = _rows_client([
            {"story_state": {"unresolved": "다음 시장 신호를 기다린다"},
             "degraded_reason": "story:malformed_response"},
            {"story_state": {"unresolved": "정상 마무리"}, "degraded_reason": None},
            {"story_state": {"unresolved": "이미지만 저하"}, "degraded_reason": "image:quota_exhausted"},
        ])
        self.assertEqual(drive_manager.fetch_recent_cliffhangers(), ["정상 마무리", "이미지만 저하"])

    @patch("src.drive_manager.get_client")
    def test_latest_state_drops_only_unresolved_of_fallback_episode(self, get_client):
        get_client.return_value = _rows_client([
            {"episode_no": 32, "status": "published_degraded", "villain": "Debt Titan",
             "story_state": {"unresolved": "다음 시장 신호를 기다린다", "villain_streak": 2, "hook_type": "C"},
             "market_snapshot": {}, "degraded_reason": "story:malformed_response"}])
        state = drive_manager.fetch_latest_episode_state()
        self.assertIsNone(state["story_state"]["unresolved"])
        self.assertEqual(state["story_state"]["villain_streak"], 2)
        self.assertEqual(state["story_state"]["hook_type"], "C")
        self.assertEqual(state["episode"], 32)


class MinimumDurationTest(unittest.TestCase):
    def _probe(self, duration, **kwargs):
        import tempfile
        from pathlib import Path
        metadata = {"format": {"duration": str(duration)},
                    "streams": [{"codec_type": "video", "width": 1080, "height": 1920}, {"codec_type": "audio"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v.mp4"
            path.write_bytes(b"x")
            with patch("src.content_quality.subprocess.run") as run:
                run.return_value.stdout = json.dumps(metadata)
                validate_rendered_video(str(path), **kwargs)

    def test_fallback_length_video_blocked_in_production(self):
        with self.assertRaises(ContentQualityError):
            self._probe(22.5, min_seconds=MIN_PUBLISH_DURATION_SEC)

    def test_normal_video_and_short_preview_pass(self):
        self._probe(34.0, min_seconds=MIN_PUBLISH_DURATION_SEC)
        self._probe(7.0)  # 저비용 미리보기는 최소 길이 미적용

    def test_production_path_passes_minimum(self):
        import inspect
        import main
        self.assertIn("min_seconds=MIN_PUBLISH_DURATION_SEC", inspect.getsource(main.main))


@patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
@patch("google.genai.Client")
class GocSafePromptTest(unittest.TestCase):
    def setUp(self):
        self.token = activate(Mock())
        self.addCleanup(reset, self.token)

    def test_goc_prompt_uses_tokens_and_feeds_back_reasons(self, client_cls):
        from src.goc import build_goc_script
        bad = ["자본을 지킨다", "{{FACT:VIX.close}} 상승에 노출을 줄인다", "비중을 낮춘다",
               "손실 한도를 정한다", "방어선을 세운다", "남은 위험을 점검한다"]
        good = list(bad)
        good[1] = "{{FACT:VIX.close}}에 노출을 줄인다"
        call = client_cls.return_value.models.generate_content
        call.side_effect = [MagicMock(text=json.dumps(bad, ensure_ascii=False)),
                            MagicMock(text=json.dumps(good, ensure_ascii=False))]
        script = build_goc_script({"episode_no": 33, "villain": "Debt Titan",
                                   "market_snapshot": SNAPSHOT, "market_as_of": "2026-10-03T01:00:00+00:00"})
        first_prompt = call.call_args_list[0].kwargs["contents"]
        self.assertNotIn("16.34", first_prompt)
        self.assertIn("{{FACT:VIX.close}}", first_prompt)
        self.assertIn("2번=market_direction_duplicated_after_fact", call.call_args_list[1].kwargs["contents"])
        self.assertEqual(script["storyboard"][1]["narration"], "VIX 16.34에 노출을 줄인다")


if __name__ == "__main__":
    unittest.main()
