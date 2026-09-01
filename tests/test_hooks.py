import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.hooks import (
    HOOK_A,
    HOOK_B,
    HOOK_C,
    HOOK_D,
    HOOK_MAX_CHARS,
    HOOK_MIN_CHARS,
    HOOK_SPECS,
    HOOK_TYPES,
    fallback_hook_line,
    is_valid_hook_line,
    select_hook_type,
    subject_particle,
)
from src.renderer import (
    HOOK_MAX_SEC,
    HOOK_WRAP_CHARS,
    _find_sfx,
    _hook_audio_filter,
    _hook_video_filter,
    _wrap_korean,
    find_kr_font,
)

MARKET = {"TNX": {"close": 4.8}, "VIX": {"close": 15.2}}


class HookTypeSelectionTest(unittest.TestCase):
    def test_villain_maps_to_preferred_type(self):
        self.assertEqual(select_hook_type("Chaos Reaper", None, 1), HOOK_A)
        self.assertEqual(select_hook_type("Debt Titan", None, 1), HOOK_B)
        self.assertEqual(select_hook_type("Bull Brute", None, 1), HOOK_C)

    def test_long_streak_switches_to_urgent(self):
        self.assertEqual(select_hook_type("Debt Titan", None, 3), HOOK_D)

    def test_same_type_as_previous_is_rotated(self):
        prev = {"story_state": {"hook_type": HOOK_B}}
        chosen = select_hook_type("Debt Titan", prev, 1)
        self.assertNotEqual(chosen, HOOK_B)

    def test_rotation_stays_within_valid_types(self):
        for t in HOOK_TYPES:
            prev = {"story_state": {"hook_type": t}}
            self.assertIn(select_hook_type("Debt Titan", prev, 1), HOOK_TYPES)

    def test_all_types_have_complete_spec(self):
        for t in HOOK_TYPES:
            spec = HOOK_SPECS[t]
            for key in ("name", "guide", "example", "tts_tone", "sfx"):
                self.assertTrue(spec.get(key), f"{t}.{key} 누락")


class HookLineTest(unittest.TestCase):
    def test_length_bounds_enforced(self):
        self.assertFalse(is_valid_hook_line("짧다", HOOK_A))
        self.assertFalse(is_valid_hook_line("가" * 40, HOOK_A))
        self.assertTrue(is_valid_hook_line("가" * 15, HOOK_A))

    def test_urgent_type_requires_prefix(self):
        self.assertFalse(is_valid_hook_line("방어선이 붕괴되었다구요", HOOK_D))
        self.assertTrue(is_valid_hook_line("[긴급] 방어선 붕괴 직전", HOOK_D))

    def test_empty_line_rejected(self):
        self.assertFalse(is_valid_hook_line("", HOOK_A))

    def test_all_fallbacks_satisfy_their_own_constraints(self):
        for villain in ("Debt Titan", "Chaos Reaper", "Bull Brute", "Unknown Villain"):
            for t in HOOK_TYPES:
                line = fallback_hook_line(t, villain, MARKET)
                self.assertTrue(
                    is_valid_hook_line(line, t),
                    f"{villain}/{t} -> '{line}' ({len(line)}자)",
                )

    def test_fallback_never_cuts_mid_word(self):
        line = fallback_hook_line(HOOK_B, "Debt Titan", MARKET)
        self.assertLessEqual(len(line), HOOK_MAX_CHARS)
        self.assertGreaterEqual(len(line), HOOK_MIN_CHARS)

    def test_korean_subject_particle(self):
        self.assertEqual(subject_particle("뎁트타이탄"), "이")  # 받침 있음
        self.assertEqual(subject_particle("카오스리퍼"), "가")  # 받침 없음
        self.assertEqual(subject_particle("불브루트"), "가")

    def test_fallback_uses_correct_particle(self):
        self.assertIn("카오스리퍼가", fallback_hook_line(HOOK_B, "Chaos Reaper", MARKET))


class CaptionRenderTest(unittest.TestCase):
    def test_long_korean_wraps_to_two_lines(self):
        wrapped = _wrap_korean("금리가 4.76%까지 솟구치며 긴축 공포가 짙어집니다", HOOK_WRAP_CHARS)
        lines = wrapped.split("\n")
        self.assertLessEqual(len(lines), 2)
        for line in lines:
            self.assertLessEqual(len(line), HOOK_WRAP_CHARS)

    def test_short_text_stays_single_line(self):
        self.assertNotIn("\n", _wrap_korean("짧은 훅", HOOK_WRAP_CHARS))

    def test_video_filter_uses_textfile_and_disables_expansion(self):
        f = _hook_video_filter(3.0, Path("/tmp/cap.txt"))
        # '%' 가 strftime 으로 확장돼 자막이 깨지던 사고의 재발 방지
        self.assertIn("expansion=none", f)
        self.assertIn("textfile=", f)
        self.assertNotIn("text='", f)

    def test_video_filter_specifies_font_explicitly(self):
        f = _hook_video_filter(3.0, Path("/tmp/cap.txt"))
        # fontfile 미지정 시 한글이 두부(□)로 렌더링된 사고의 재발 방지
        if find_kr_font():
            self.assertIn("fontfile=", f)

    def test_video_filter_has_shake_and_punch_in(self):
        f = _hook_video_filter(3.0, Path("/tmp/cap.txt"))
        self.assertIn("sin(2*PI*t*9)", f)   # 흔들림
        self.assertIn("zoompan", f)          # 펀치인


class HookAudioFilterTest(unittest.TestCase):
    def test_long_narration_is_compressed_with_atempo(self):
        f = _hook_audio_filter(4.2, 3.0, audio_index=1, sfx_index=None)
        self.assertIn("atempo=1.400", f)
        self.assertIn("[1:a]", f)

    def test_short_narration_is_not_compressed(self):
        f = _hook_audio_filter(2.5, 3.0, audio_index=1, sfx_index=None)
        self.assertNotIn("atempo", f)

    def test_atempo_is_capped(self):
        f = _hook_audio_filter(30.0, 3.0, audio_index=1, sfx_index=None)
        self.assertIn("atempo=1.500", f)

    def test_sfx_uses_correct_input_index(self):
        # 입력 0은 이미지이므로 SFX 는 2번. 인덱스가 어긋나면 ffmpeg 가 실패한다
        f = _hook_audio_filter(2.0, 3.0, audio_index=1, sfx_index=2)
        self.assertIn("[2:a]", f)
        self.assertIn("amix=inputs=2", f)

    def test_without_sfx_no_amix(self):
        f = _hook_audio_filter(2.0, 3.0, audio_index=1, sfx_index=None)
        self.assertNotIn("amix", f)

    def test_hook_duration_constraint_is_three_seconds(self):
        self.assertEqual(HOOK_MAX_SEC, 3.0)


class SfxSlotTest(unittest.TestCase):
    def test_missing_directory_returns_none(self):
        with patch.dict("os.environ", {"SFX_DIR": "/nonexistent/sfx"}, clear=True):
            self.assertIsNone(_find_sfx("hook_a"))

    @patch("src.renderer._has_audio_stream", return_value=True)
    def test_exact_type_match_preferred(self, _probe):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "hook_a.wav").write_bytes(b"x")
            (Path(d) / "hook_b.wav").write_bytes(b"x")
            with patch.dict("os.environ", {"SFX_DIR": d}, clear=True):
                self.assertTrue(_find_sfx("hook_b").endswith("hook_b.wav"))

    @patch("src.renderer._has_audio_stream", return_value=True)
    def test_falls_back_to_any_file_when_type_missing(self, _probe):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "generic.wav").write_bytes(b"x")
            with patch.dict("os.environ", {"SFX_DIR": d}, clear=True):
                self.assertTrue(_find_sfx("hook_z").endswith("generic.wav"))

    def test_readme_only_directory_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "README.md").write_text("doc", encoding="utf-8")
            with patch.dict("os.environ", {"SFX_DIR": d}, clear=True):
                self.assertIsNone(_find_sfx("hook_a"))


class StoryboardHookIntegrationTest(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_first_beat_carries_hook_metadata(self):
        from src.story import build_storyboard

        sb, state, _ = build_storyboard(MARKET, "Debt Titan", "긴축", None)

        self.assertTrue(sb[0]["is_hook"])
        self.assertIn(sb[0]["hook_type"], HOOK_TYPES)
        self.assertTrue(sb[0]["tts_tone"])
        self.assertTrue(sb[0]["sfx"])
        self.assertEqual(state["hook_type"], sb[0]["hook_type"])

    @patch.dict("os.environ", {}, clear=True)
    def test_hook_line_respects_length_in_fallback_path(self):
        from src.story import build_storyboard

        sb, _, _ = build_storyboard(MARKET, "Debt Titan", "긴축", None)
        self.assertTrue(is_valid_hook_line(sb[0]["narration"], sb[0]["hook_type"]))

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_oversized_model_hook_triggers_retry(self, client_cls):
        from src.story import build_storyboard

        bad = MagicMock(text='["' + "가" * 40 + '","2","3","4","5","6"]')
        good = MagicMock(text='["공포지수 경고등이 켜졌다","2","3","4","5","6"]')
        client_cls.return_value.models.generate_content.side_effect = [bad, good]

        sb, _, _ = build_storyboard(MARKET, "Debt Titan", "긴축", None)

        self.assertEqual(client_cls.return_value.models.generate_content.call_count, 2)
        self.assertEqual(sb[0]["narration"], "공포지수 경고등이 켜졌다")

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_retry_failure_falls_back_to_rule_hook_only(self, client_cls):
        from src.story import build_storyboard

        bad = MagicMock(text='["' + "가" * 40 + '","본문2","본문3","본문4","본문5","본문6"]')
        client_cls.return_value.models.generate_content.side_effect = [bad, bad]

        sb, _, _ = build_storyboard(MARKET, "Debt Titan", "긴축", None)

        # 훅만 규칙 문장으로 교체되고 나머지 5줄은 모델 결과를 살린다
        self.assertTrue(is_valid_hook_line(sb[0]["narration"], sb[0]["hook_type"]))
        self.assertEqual(sb[1]["narration"], "본문2")


if __name__ == "__main__":
    unittest.main()


class FontResolutionTest(unittest.TestCase):
    def test_env_override_takes_priority(self):
        with tempfile.TemporaryDirectory() as d:
            font = Path(d) / "custom.ttf"
            font.write_bytes(b"x")
            with patch.dict("os.environ", {"KR_FONT_PATH": str(font)}, clear=True):
                self.assertEqual(find_kr_font(), str(font))

    def test_ignores_override_when_path_missing(self):
        with patch.dict("os.environ", {"KR_FONT_PATH": "/nope/font.ttf"}, clear=True):
            # 존재하지 않는 경로는 무시하고 다음 단계로 넘어가야 한다
            self.assertNotEqual(find_kr_font(), "/nope/font.ttf")

    def test_falls_back_to_recursive_search(self):
        from src import renderer

        with tempfile.TemporaryDirectory() as d:
            noto = Path(d) / "opentype" / "noto"
            noto.mkdir(parents=True)
            target = noto / "NotoSansCJK-Bold.ttc"
            target.write_bytes(b"x")
            with patch.dict("os.environ", {}, clear=True), \
                 patch.object(renderer, "KR_FONT_CANDIDATES", []), \
                 patch.object(renderer, "FONT_SEARCH_ROOTS", [d]):
                self.assertEqual(find_kr_font(), str(target))

    def test_uses_fc_match_as_last_resort(self):
        from src import renderer

        with patch.dict("os.environ", {}, clear=True), \
             patch.object(renderer, "KR_FONT_CANDIDATES", []), \
             patch.object(renderer, "FONT_SEARCH_ROOTS", []), \
             patch.object(renderer, "_font_from_fc_match", return_value="/fake/kr.ttf"):
            self.assertEqual(find_kr_font(), "/fake/kr.ttf")

    def test_returns_none_when_nothing_found(self):
        from src import renderer

        with patch.dict("os.environ", {}, clear=True), \
             patch.object(renderer, "KR_FONT_CANDIDATES", []), \
             patch.object(renderer, "FONT_SEARCH_ROOTS", []), \
             patch.object(renderer, "_font_from_fc_match", return_value=None):
            self.assertIsNone(find_kr_font())


class RenderEnvironmentValidationTest(unittest.TestCase):
    def test_missing_font_aborts_pipeline(self):
        from src.validation import RenderEnvironmentInvalid, validate_render_environment

        with patch("src.renderer.find_kr_font", return_value=None), \
             self.assertRaises(RenderEnvironmentInvalid):
            validate_render_environment()

    def test_present_font_passes(self):
        from src.validation import validate_render_environment

        with patch("src.renderer.find_kr_font", return_value="/usr/share/fonts/x.ttc"):
            validate_render_environment()

    def test_bypass_mode_only_warns(self):
        from src.validation import validate_render_environment

        with patch("src.renderer.find_kr_font", return_value=None), \
             patch.dict("os.environ", {"STRICT_VALIDATION": "false"}, clear=True):
            validate_render_environment()
