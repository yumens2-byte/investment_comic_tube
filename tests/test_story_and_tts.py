import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.story import BEAT_COUNT, _parse_narrations, build_storyboard
from src.tts import synthesize_narrations

MARKET = {"TNX": {"close": 4.8}, "VIX": {"close": 15.0}, "NASDAQ": {"change_pct": -0.3}}
SIX = [f"문장{i}" for i in range(6)]


class ParseNarrationsTest(unittest.TestCase):
    def test_plain_json_array(self):
        raw = '["a","b","c","d","e","f"]'
        self.assertEqual(_parse_narrations(raw), list("abcdef"))

    def test_markdown_fenced_json(self):
        raw = '```json\n["a","b","c","d","e","f"]\n```'
        self.assertEqual(_parse_narrations(raw), list("abcdef"))

    def test_object_with_narrations_key(self):
        raw = '{"narrations": ["a","b","c","d","e","f"]}'
        self.assertEqual(_parse_narrations(raw), list("abcdef"))

    def test_wrong_length_rejected(self):
        self.assertIsNone(_parse_narrations('["a","b"]'))

    def test_invalid_json_rejected(self):
        self.assertIsNone(_parse_narrations("not json at all"))

    def test_blank_entry_rejected(self):
        self.assertIsNone(_parse_narrations('["a","","c","d","e","f"]'))


class BuildStoryboardTest(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_no_api_key_uses_fallback_but_still_six_beats(self):
        storyboard, degraded = build_storyboard(MARKET, "Debt Titan", "긴축")

        self.assertEqual(len(storyboard), BEAT_COUNT)
        self.assertEqual(degraded, "story:no_api_key")
        for beat in storyboard:
            self.assertTrue(beat["narration"])
            self.assertTrue(beat["scene"])

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_success_uses_model_narrations(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = MagicMock(
            text='["문장0","문장1","문장2","문장3","문장4","문장5"]'
        )

        storyboard, degraded = build_storyboard(MARKET, "Debt Titan", "긴축")

        self.assertIsNone(degraded)
        self.assertEqual([b["narration"] for b in storyboard], SIX)
        self.assertEqual(storyboard[0]["beat"], "HOOK")
        self.assertEqual(storyboard[-1]["beat"], "LESSON")

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_malformed_response_falls_back(self, client_cls):
        client_cls.return_value.models.generate_content.return_value = MagicMock(text="쓰레기 응답")

        storyboard, degraded = build_storyboard(MARKET, "Debt Titan", "긴축")

        self.assertEqual(degraded, "story:malformed_response")
        self.assertEqual(len(storyboard), BEAT_COUNT)

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_api_error_falls_back(self, client_cls):
        client_cls.return_value.models.generate_content.side_effect = RuntimeError("boom")

        storyboard, degraded = build_storyboard(MARKET, "Debt Titan", "긴축")

        self.assertEqual(degraded, "story:RuntimeError")
        self.assertEqual(len(storyboard), BEAT_COUNT)


def _pcm_response(data: bytes):
    part = MagicMock()
    part.inline_data.data = data
    candidate = MagicMock()
    candidate.content.parts = [part]
    response = MagicMock()
    response.candidates = [candidate]
    return response


class SynthesizeNarrationsTest(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_no_api_key_returns_reason(self):
        paths, reason = synthesize_narrations(["a", "b"])

        self.assertEqual(paths, [None, None])
        self.assertEqual(reason, "tts:no_api_key")

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_success_writes_playable_wav(self, client_cls):
        pcm = b"\x00\x01" * 12000  # 1초 분량 24kHz 16-bit mono
        client_cls.return_value.models.generate_content.return_value = _pcm_response(pcm)

        with tempfile.TemporaryDirectory() as directory:
            paths, reason = synthesize_narrations(["안녕", "반가워"], output_dir=directory)

            self.assertIsNone(reason)
            self.assertEqual(len(paths), 2)
            for path in paths:
                self.assertTrue(Path(path).exists())
                with wave.open(path, "rb") as wf:
                    self.assertEqual(wf.getnchannels(), 1)
                    self.assertEqual(wf.getsampwidth(), 2)
                    self.assertEqual(wf.getframerate(), 24000)
                    self.assertEqual(wf.getnframes(), 12000)

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_all_calls_fail_returns_reason(self, client_cls):
        client_cls.return_value.models.generate_content.side_effect = RuntimeError("boom")

        with tempfile.TemporaryDirectory() as directory:
            paths, reason = synthesize_narrations(["a", "b"], output_dir=directory)

        self.assertEqual(paths, [None, None])
        self.assertEqual(reason, "tts:RuntimeError")

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_partial_failure_reported(self, client_cls):
        pcm = b"\x00\x01" * 12000
        client_cls.return_value.models.generate_content.side_effect = [
            _pcm_response(pcm),
            RuntimeError("boom"),
        ]

        with tempfile.TemporaryDirectory() as directory:
            paths, reason = synthesize_narrations(["a", "b"], output_dir=directory)

        self.assertIsNotNone(paths[0])
        self.assertIsNone(paths[1])
        self.assertEqual(reason, "tts:partial_1of2")


if __name__ == "__main__":
    unittest.main()


class QuotaFailFastTest(unittest.TestCase):
    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_tts_aborts_remaining_calls_on_quota(self, client_cls):
        models = client_cls.return_value.models
        models.generate_content.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED: monthly spending cap exceeded"
        )

        with tempfile.TemporaryDirectory() as directory:
            paths, reason = synthesize_narrations([f"line{i}" for i in range(6)], output_dir=directory)

        self.assertEqual(models.generate_content.call_count, 1)
        self.assertEqual(len(paths), 6)
        self.assertEqual(reason, "tts:quota_exhausted")

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
    @patch("google.genai.Client")
    def test_tts_non_quota_error_keeps_trying(self, client_cls):
        models = client_cls.return_value.models
        models.generate_content.side_effect = RuntimeError("temporary hiccup")

        with tempfile.TemporaryDirectory() as directory:
            synthesize_narrations(["a", "b", "c"], output_dir=directory)

        self.assertEqual(models.generate_content.call_count, 3)


class QuotaDetectionTest(unittest.TestCase):
    def test_detects_known_quota_markers(self):
        from src.quota import is_quota_exhausted

        for message in (
            "429 RESOURCE_EXHAUSTED",
            "project has exceeded its monthly spending cap",
            "quota exceeded for this project",
        ):
            self.assertTrue(is_quota_exhausted(RuntimeError(message)), message)

    def test_ignores_transient_errors(self):
        from src.quota import is_quota_exhausted

        for message in ("connection reset", "500 internal error", "429 rate limit, retry"):
            self.assertFalse(is_quota_exhausted(RuntimeError(message)), message)
