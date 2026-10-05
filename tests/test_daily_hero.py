import unittest
from contextlib import ExitStack
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import main
from src.daily_hero import select_daily_hero
from src.director import generate_connected_script
from src.publisher import build_title, build_description, upload_to_youtube


class DailyHeroTest(unittest.TestCase):
    def test_same_date_retries_do_not_switch_hero(self):
        day = date(2026, 9, 30)
        self.assertEqual(select_daily_hero(day), select_daily_hero(day))
        choices = {select_daily_hero(day + timedelta(days=i)) for i in range(60)}
        self.assertEqual(choices, {"EDT"})
        self.assertEqual(select_daily_hero(), "EDT")

    @patch("src.director.start_episode", return_value="ep-30")
    @patch("src.director.build_goc_script")
    @patch("src.director.build_storyboard")
    @patch("src.director._polish_narration")
    @patch("src.director.select_villain", return_value=("Bull Brute", "theme", {}))
    @patch("src.director.fetch_recent_cliffhangers", return_value=[])
    @patch("src.director.fetch_latest_episode_state", return_value={"episode": 29})
    def test_goc_production_uses_fresh_snapshot_and_shared_episode(self, state, recent, villain, polish, edt, goc, start):
        market = {"VIX": {"close": 16, "change_pct": 1}}
        goc.return_value = {"theme": "자본 보호", "storyboard": [{"narration": "방어"}] * 6}
        script = generate_connected_script(market, track="GOC")
        self.assertIs(goc.call_args.args[0]["market_snapshot"], market)
        self.assertEqual(script["episode"], 30)
        self.assertEqual(script["story_state"]["track"], "GOC")
        self.assertEqual(script["privacy"], "private")
        self.assertEqual(script["episode_id"], "ep-30")
        edt.assert_not_called()
        polish.assert_not_called()

    def test_goc_upload_metadata_identifies_hero(self):
        metadata = {"track": "GOC", "episode": 30, "theme": "자본 보호"}
        self.assertIn("GOC 투자코믹", build_title(metadata))
        self.assertIn("GOC와 함께", build_description(metadata))

    def test_edt_full_pipeline_keeps_duplicate_gate_and_public_metadata(self):
        from tests.test_main_pipeline import MARKET, STORYBOARD
        with ExitStack() as stack:
            mocks = {}
            returns = {"fetch_market_data": MARKET,
                       "generate_connected_script": {"episode": 30, "episode_id": "ep30", "storyboard": STORYBOARD},
                       "generate_scene_images": ([f"i{i}" for i in range(4)], None),
                       "synthesize_narrations": ([f"a{i}" for i in range(6)], None),
                       "render_video": "video.mp4", "upload_to_youtube": "video-id"}
            for name in ("validate_render_environment", "validate_not_published_today", "get_youtube_service",
                         "record_step_start", "record_step_finish", "update_episode", "validate_image_assets",
                         "validate_rendered_video", *returns):
                mocks[name] = stack.enter_context(patch(f"main.{name}", return_value=returns.get(name)))
            self.assertEqual(main.main(track=select_daily_hero()), 0)
            mocks["validate_not_published_today"].assert_called_once()
            self.assertEqual(mocks["generate_connected_script"].call_args.kwargs, {})
            self.assertNotIn("voice_name", mocks["synthesize_narrations"].call_args.kwargs)
            self.assertEqual(mocks["upload_to_youtube"].call_args.args[1]["privacy"], "public")
            self.assertEqual(mocks["upload_to_youtube"].call_args.args[1]["track"], "EDT")

    @patch("main.get_youtube_service")
    @patch("main.fetch_market_data")
    def test_goc_production_is_disabled_before_generation(self, market, youtube):
        with self.assertRaisesRegex(ValueError, "GOC production publication is disabled"):
            main.main(track="GOC")
        market.assert_not_called()
        youtube.assert_not_called()

    @patch.dict("os.environ", {"YOUTUBE_DEFAULT_PRIVACY": "private"})
    @patch("src.publisher.os.path.exists", return_value=True)
    @patch("src.publisher.MediaFileUpload")
    @patch("src.publisher.set_thumbnail")
    @patch("src.publisher.add_to_playlist")
    def test_edt_public_metadata_reaches_youtube_request(self, playlist, thumbnail, media, exists):
        youtube = MagicMock()
        youtube.videos.return_value.insert.return_value.next_chunk.return_value = (None, {"id": "video-id"})
        upload_to_youtube("video.mp4", {"privacy": "public", "track": "EDT"}, youtube_service=youtube)
        status = youtube.videos.return_value.insert.call_args.kwargs["body"]["status"]
        self.assertEqual(status["privacyStatus"], "public")
        self.assertNotIn("publishAt", status)

    @patch.dict("os.environ", {"YOUTUBE_DEFAULT_PRIVACY": "public"})
    @patch("src.publisher.os.path.exists", return_value=True)
    @patch("src.publisher.MediaFileUpload")
    @patch("src.publisher.set_thumbnail")
    @patch("src.publisher.add_to_playlist")
    def test_private_daily_metadata_overrides_public_environment(self, playlist, thumbnail, media, exists):
        youtube = MagicMock()
        youtube.videos.return_value.insert.return_value.next_chunk.return_value = (None, {"id": "video-id"})
        upload_to_youtube("video.mp4", {"privacy": "private", "track": "GOC"}, youtube_service=youtube)
        self.assertEqual(youtube.videos.return_value.insert.call_args.kwargs["body"]["status"]["privacyStatus"], "private")

    @patch("main.validate_rendered_video")
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.synthesize_narrations", return_value=(["voice.wav"], None))
    @patch("main.validate_image_assets")
    @patch("main.generate_scene_images", return_value=(["image.png"], None))
    @patch("main.validate_market_data")
    @patch("main.fetch_market_data", return_value={})
    @patch("main.select_villain", return_value=("Bull Brute", "theme", {}))
    @patch("main.validate_render_environment")
    @patch("main.upload_to_youtube")
    @patch("main.update_episode")
    @patch("main.generate_connected_script")
    def test_budget_preview_limits_paid_calls_and_cannot_publish(self, script, update, upload, env, villain, market, validate,
                                                                images, image_check, tts, render, video_check):
        self.assertEqual(main.budget_video_pilot("GOC"), 0)
        self.assertEqual(len(images.call_args.kwargs["scenes"]), 1)
        self.assertEqual(images.call_args.kwargs["model_name"], "gemini-3.1-flash-lite-image")
        self.assertEqual(len(tts.call_args.args[0]), 1)
        self.assertEqual(tts.call_args.kwargs["model_name"], "gemini-3.8-flash-lite-tts")
        self.assertEqual(tts.call_args.kwargs["max_attempts"], 1)
        self.assertEqual(tts.call_args.kwargs["voice_name"], "Kore")
        script.assert_not_called()
        update.assert_not_called()
        upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
