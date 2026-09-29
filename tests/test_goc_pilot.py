import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import main
from src.goc import GOC_VOICE, build_goc_script, latest_edt_event
from src.image_generator import _build_prompt, _load_reference_images, generate_scene_images
from src.tts import synthesize_narrations
from src.validation import ValidationError


class GocEventTest(unittest.TestCase):
    @patch("src.goc.get_client")
    def test_uses_same_day_korean_edt_snapshot(self, get_client):
        row = {"episode_no": 30, "market_as_of": "2026-09-29T17:10:00Z",
               "villain": "Bull Brute", "market_snapshot": {"VIX": {"close": 16, "change_pct": 1}}}
        get_client.return_value.table.return_value.select.return_value.in_.return_value.order.return_value.limit.return_value.execute.return_value.data = [row]
        self.assertEqual(latest_edt_event(today=date(2026, 9, 30)), row)
        with self.assertRaises(ValidationError):
            latest_edt_event(today=date(2026, 10, 1))
        self.assertEqual(latest_edt_event(today=date(2026, 10, 1), max_age_days=1), row)
        with self.assertRaises(ValidationError):
            latest_edt_event(today=date(2026, 10, 2), max_age_days=1)

    @patch("src.goc.get_client")
    def test_no_published_event_fails(self, get_client):
        get_client.return_value.table.return_value.select.return_value.in_.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        with self.assertRaises(ValidationError):
            latest_edt_event(today=date(2026, 9, 30))

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test"})
    @patch("google.genai.Client")
    def test_goc_story_has_distinct_perspective_and_shared_snapshot(self, client):
        client.return_value.models.generate_content.return_value.text = '["위험에 대비한다","둘","셋","넷","다섯","여섯"]'
        event = {"episode_no": 30, "market_as_of": "2026-09-29T17:10:00Z", "villain": "Bull Brute",
                 "market_snapshot": {"VIX": {"close": 16, "change_pct": 1}}}
        script = build_goc_script(event)
        self.assertIs(script["market_snapshot"], event["market_snapshot"])
        self.assertEqual(script["track"], "GOC")
        self.assertEqual(len(script["storyboard"]), 6)
        prompt = client.return_value.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("자본 보호", prompt)
        self.assertIn("16", prompt)

    def test_goc_image_prompt_has_no_edt_character_description(self):
        prompt = _build_prompt({"track": "GOC", "villain": "Bull Brute"}, "protect capital", True)
        self.assertIn("Guardian of Capital", prompt)
        self.assertIn("human ears", prompt)
        self.assertNotIn("tiger hero", prompt)
        self.assertNotIn("chainsaw", prompt)

    def test_approved_goc_reference_is_readable_and_isolated(self):
        references = _load_reference_images("assets/reference/goc")
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0][1], "image/png")
        self.assertTrue(references[0][0].startswith(b"\x89PNG\r\n\x1a\n"))

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test", "GOC_REFERENCE_DIR": "/missing/goc"})
    @patch("google.genai.Client")
    def test_missing_goc_reference_blocks_image_api(self, client):
        with self.assertRaises(ValueError):
            generate_scene_images({"track": "GOC"}, scenes=["GOC"])
        client.return_value.models.generate_content.assert_not_called()


class GocPipelineTest(unittest.TestCase):
    @patch.dict("os.environ", {"GEMINI_API_KEY": "test"})
    @patch("google.genai.Client")
    def test_goc_voice_reaches_gemini_speech_config(self, client):
        part = type("Part", (), {"inline_data": type("Audio", (), {"data": b"\x00\x01" * 240})()})()
        content = type("Content", (), {"parts": [part]})()
        client.return_value.models.generate_content.return_value.candidates = [
            type("Candidate", (), {"content": content})()]
        with tempfile.TemporaryDirectory() as directory:
            paths, degraded = synthesize_narrations(["자본을 지킨다"], output_dir=directory,
                                                    voice_name=GOC_VOICE)
        self.assertIsNone(degraded)
        self.assertEqual(len(paths), 1)
        config = client.return_value.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(config.speech_config.voice_config.prebuilt_voice_config.voice_name, "Kore")

    @patch("main.validate_rendered_video")
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.validate_media_package")
    @patch("main.synthesize_narrations", return_value=(["a"] * 6, None))
    @patch("main.validate_image_assets")
    @patch("main.generate_scene_images", return_value=(["i"] * 4, None))
    @patch("main.build_goc_script")
    @patch("main.latest_edt_event")
    @patch("main.validate_market_data")
    @patch("main.validate_render_environment")
    @patch("main.upload_to_youtube")
    @patch("main.update_episode")
    def test_goc_pilot_uses_female_voice_and_never_uploads_or_writes_db(
        self, update, upload, render_env, market_check, event, script, images,
        image_check, tts, package_check, render, video_check
    ):
        event.return_value = {"episode_no": 30, "market_as_of": "2026-09-29T17:10:00Z", "market_snapshot": {}}
        script.return_value = {"track": "GOC", "storyboard": [
            {"beat": name, "scene": "GOC", "narration": "보호"}
            for name in ("HOOK", "THREAT", "IMPACT", "HERO", "CLASH", "LESSON")
        ]}
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            self.assertEqual(main.goc_video_pilot(), 0)
        self.assertEqual(tts.call_args.kwargs["voice_name"], GOC_VOICE)
        self.assertEqual(event.call_args.kwargs, {"max_age_days": 1})
        self.assertEqual(images.call_args.args[0]["track"], "GOC")
        upload.assert_not_called()
        update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
