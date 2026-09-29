import logging
import tempfile
import unittest
from unittest.mock import patch

import main
from src.publisher import YouTubeAuthenticationError

STORYBOARD = [
    {"beat": "HOOK", "scene": "s1", "narration": "n1"},
    {"beat": "THREAT", "scene": "s2", "narration": "n2"},
    {"beat": "IMPACT", "scene": "s3", "narration": "n3"},
    {"beat": "HERO", "scene": "s4", "narration": "n4"},
    {"beat": "CLASH", "scene": "s5", "narration": "n5"},
    {"beat": "LESSON", "scene": "s6", "narration": "n6"},
]  # 6비트 -- validate_storyboard 통과 조건
IMAGES4 = ["img0.png", "img1.png", "img2.png", "img3.png"]
AUDIO6 = [f"a{i}.wav" for i in range(6)]
SCRIPT_OK = {
    "episode": 103,
    "villain": "Debt Titan",
    "narration": "n",
    "storyboard": STORYBOARD,
    "episode_id": "ep-0103-abcd1234",
    "degraded_reason": None,
}
SCRIPT_DEGRADED = dict(SCRIPT_OK, degraded_reason="story:RuntimeError")
# validate_market_data 를 통과하려면 필수 지표 5종이 모두 완비돼야 한다
MARKET = {
    "TNX": {"close": 4.8, "change_pct": 0.5, "sma20": 4.4, "dev_pct": 9.0},
    "VIX": {"close": 15.0, "change_pct": 1.2, "sma20": 15.5, "dev_pct": -3.2},
    "NASDAQ": {"close": 26000.0, "change_pct": -0.3, "sma20": 25800.0, "dev_pct": 0.8},
    "SPX": {"close": 5800.0, "change_pct": -0.2, "sma20": 5750.0, "dev_pct": 0.9},
    "DXY": {"close": 103.5, "change_pct": 0.1, "sma20": 103.0, "dev_pct": 0.5},
    "GOLD": {"close": 2400.0, "change_pct": 0.4, "sma20": 2380.0, "dev_pct": 0.8},
    "OIL": {"close": 78.0, "change_pct": -0.6, "sma20": 79.0, "dev_pct": -1.3},
}


class BuildScenesTest(unittest.TestCase):
    def test_four_images_are_reused_across_six_beats(self):
        scenes = main._build_scenes(STORYBOARD, IMAGES4, AUDIO6)

        # 슬롯 매핑 [0,1,1,2,3,3] -- 슬롯0은 훅 전용 클로즈업이라 단독으로 쓴다
        self.assertEqual(len(scenes), 6)
        self.assertEqual(
            [s["image"] for s in scenes],
            ["img0.png", "img1.png", "img1.png", "img2.png", "img3.png", "img3.png"],
        )
        self.assertEqual([s["caption"] for s in scenes], [f"n{i}" for i in range(1, 7)])
        self.assertEqual([s["audio"] for s in scenes], AUDIO6)

    def test_missing_slot_image_falls_back_to_available_one(self):
        scenes = main._build_scenes(STORYBOARD, ["img0.png", None, None, None], AUDIO6)

        # 슬롯 1,2 이미지가 없어도 비트가 사라지지 않고 생성된 이미지로 대체된다
        self.assertEqual(len(scenes), 6)
        self.assertTrue(all(s["image"] == "img0.png" for s in scenes))

    def test_no_images_yields_no_scenes(self):
        scenes = main._build_scenes(STORYBOARD, [None, None, None, None], AUDIO6)

        self.assertEqual(scenes, [])


class PipelineOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.duplicate_check = patch("main.validate_not_published_today")
        self.duplicate_check.start()
        self.addCleanup(self.duplicate_check.stop)
        self.video_check = patch("main.validate_rendered_video")
        self.video_check.start()
        self.addCleanup(self.video_check.stop)

    def tearDown(self):
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)

    @patch("main.update_episode")
    @patch("main.upload_to_youtube")
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.synthesize_narrations", return_value=(AUDIO6, None))
    @patch("main.generate_scene_images", return_value=(IMAGES4, None))
    @patch("main.generate_connected_script", return_value=SCRIPT_OK)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_invalid_rendered_video_blocks_upload(self, _fetch, _script, _images, _tts, _render, upload, update):
        from src.content_quality import ContentQualityError
        with patch("main.validate_rendered_video", side_effect=ContentQualityError("missing audio")):
            with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
                self.assertEqual(main.main(), 1)
        upload.assert_not_called()
        self.assertEqual(update.call_args.kwargs["status"], "aborted_validation")

    @patch("main.record_step_finish")
    @patch("main.get_youtube_service", return_value="youtube-service")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.upload_to_youtube", return_value="yt-video-123")
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.synthesize_narrations", return_value=(AUDIO6, None))
    @patch("main.generate_scene_images", return_value=(IMAGES4, None))
    @patch("main.generate_connected_script", return_value=SCRIPT_OK)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_fully_successful_run_marks_published(
        self, _fetch, _script, images, tts, render, upload, update_episode, _start,
        youtube_auth, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        # 비용 통제: 비트(6)가 아니라 슬롯(3)만큼만 이미지를 생성한다
        from src.story import SLOT_SCENES
        self.assertEqual(images.call_args.kwargs["scenes"], SLOT_SCENES)
        self.assertEqual(len(SLOT_SCENES), 4)
        # 내레이션 6줄이 전부 TTS로 전달됐는지
        self.assertEqual(tts.call_args.args[0], [f"n{i}" for i in range(1, 7)])
        youtube_auth.assert_called_once_with()
        self.assertEqual(upload.call_args.kwargs["youtube_service"], "youtube-service")
        # 이미지 3장으로 6장면이 렌더링되는지
        scenes = render.call_args.kwargs["scenes"]
        self.assertEqual(len(scenes), 6)
        self.assertEqual(scenes[0]["audio"], "a0.wav")

        final = update_episode.call_args_list[-1]
        self.assertEqual(final.kwargs["status"], "published")
        self.assertIsNone(final.kwargs["degraded_reason"])

    @patch("main.upload_to_youtube")
    @patch("main.render_video")
    @patch("main.synthesize_narrations")
    @patch("main.generate_scene_images")
    @patch("main.generate_connected_script")
    @patch("main.fetch_market_data")
    @patch(
        "main.get_youtube_service",
        side_effect=YouTubeAuthenticationError("refresh token revoked"),
    )
    def test_revoked_youtube_token_aborts_before_market_and_paid_generation(
        self, youtube_auth, fetch, script, images, tts, render, upload
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"LOG_DIR": directory}
        ):
            exit_code = main.main()

        self.assertEqual(exit_code, 1)
        youtube_auth.assert_called_once_with()
        fetch.assert_not_called()
        script.assert_not_called()
        images.assert_not_called()
        tts.assert_not_called()
        render.assert_not_called()
        upload.assert_not_called()

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.upload_to_youtube", return_value="yt-video-123")
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.synthesize_narrations", return_value=([None] * 6, "tts:no_api_key"))
    @patch("main.generate_scene_images", return_value=([None] * 4, "image:no_api_key"))
    @patch("main.generate_connected_script", return_value=SCRIPT_DEGRADED)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_missing_media_aborts_before_render_and_upload(
        self, _fetch, _script, _images, _tts, render, _upload, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 1)
        render.assert_not_called()
        _upload.assert_not_called()
        final = update_episode.call_args_list[-1]
        self.assertEqual(final.kwargs["status"], "aborted_validation")

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.upload_to_youtube", return_value="yt-video-123")
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.synthesize_narrations", return_value=(["a0.wav"] + [None] * 5, "tts:partial_1of6"))
    @patch("main.generate_scene_images", return_value=(IMAGES4, None))
    @patch("main.generate_connected_script", return_value=SCRIPT_OK)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_partial_tts_aborts_before_render(
        self, _fetch, _script, _images, _tts, render, _upload, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 1)
        render.assert_not_called()
        _upload.assert_not_called()
        final = update_episode.call_args_list[-1]
        self.assertEqual(final.kwargs["status"], "aborted_validation")

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.upload_to_youtube", return_value=None)
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.synthesize_narrations", return_value=(AUDIO6, None))
    @patch("main.generate_scene_images", return_value=(IMAGES4, None))
    @patch("main.generate_connected_script", return_value=SCRIPT_OK)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_no_upload_marks_rendered_no_upload(
        self, _fetch, _script, _images, _tts, _render, _upload, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(update_episode.call_args_list[-1].kwargs["status"], "rendered_no_upload")

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.render_video", side_effect=RuntimeError("ffmpeg exploded"))
    @patch("main.synthesize_narrations", return_value=(AUDIO6, None))
    @patch("main.generate_scene_images", return_value=(IMAGES4, None))
    @patch("main.generate_connected_script", return_value=SCRIPT_OK)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_render_failure_marks_episode_failed(
        self, _fetch, _script, _images, _tts, _render, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 1)
        update_episode.assert_called_once_with(
            "ep-0103-abcd1234",
            status="failed",
            degraded_reason=None,
        )


if __name__ == "__main__":
    unittest.main()


class ValidationAbortTest(unittest.TestCase):
    def setUp(self):
        self.duplicate_check = patch("main.validate_not_published_today")
        self.duplicate_check.start()
        self.addCleanup(self.duplicate_check.stop)

    def tearDown(self):
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)

    @patch("main.fetch_market_data")
    @patch("main.get_youtube_service")
    @patch("main.validate_not_published_today", side_effect=RuntimeError("db down"))
    def test_duplicate_check_outage_aborts_before_paid_work(self, _check, youtube, fetch):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            self.assertEqual(main.main(), 1)
        youtube.assert_not_called()
        fetch.assert_not_called()

    @patch("main.generate_connected_script")
    @patch("main.fetch_market_data", return_value={"TNX": {"close": None, "change_pct": None}})
    def test_missing_market_data_aborts_before_creating_episode(self, _fetch, script):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 1)
        # 핵심: 검증 실패 시 에피소드 생성 자체가 일어나면 안 된다 (고아 row 방지)
        script.assert_not_called()

    @patch("main.upload_to_youtube")
    @patch("main.render_video")
    @patch("main.synthesize_narrations")
    @patch("main.generate_scene_images")
    @patch("main.record_step_start", return_value="s1")
    @patch("main.record_step_finish")
    @patch("main.update_episode")
    @patch("main.generate_connected_script")
    @patch("main.fetch_market_data")
    def test_incomplete_storyboard_aborts_before_paid_api_calls(
        self, fetch, script, update_episode, _finish, _start, images, tts, render, upload
    ):
        fetch.return_value = {
            n: {"close": 1.0, "change_pct": 0.1}
            for n in ("TNX", "VIX", "NASDAQ", "SPX", "DXY")
        }
        # 6비트여야 하는데 2개만 생성된 상황
        script.return_value = {
            "episode": 1,
            "villain": "Debt Titan",
            "narration": "n",
            "storyboard": [{"beat": "HOOK", "scene": "s", "narration": "n"}],
            "episode_id": "ep-0001-abcd",
            "degraded_reason": None,
        }

        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 1)
        # 유료 API(이미지/TTS)와 업로드가 전혀 호출되지 않아야 한다
        images.assert_not_called()
        tts.assert_not_called()
        render.assert_not_called()
        upload.assert_not_called()
        # 이미 생성된 회차는 aborted_validation 으로 기록된다
        update_episode.assert_called_once()
        self.assertEqual(update_episode.call_args.kwargs["status"], "aborted_validation")
