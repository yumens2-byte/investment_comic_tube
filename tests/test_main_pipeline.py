import logging
import tempfile
import unittest
from unittest.mock import patch

import main

STORYBOARD = [
    {"beat": "HOOK", "scene": "s1", "narration": "n1"},
    {"beat": "THREAT", "scene": "s2", "narration": "n2"},
]
SCRIPT_OK = {
    "episode": 103,
    "villain": "Debt Titan",
    "narration": "n",
    "storyboard": STORYBOARD,
    "episode_id": "ep-0103-abcd1234",
    "degraded_reason": None,
}
SCRIPT_DEGRADED = dict(SCRIPT_OK, degraded_reason="story:RuntimeError")
MARKET = {"TNX": {"close": 4.8}, "VIX": {"close": 15.0}}


class BuildScenesTest(unittest.TestCase):
    def test_skips_beats_without_image(self):
        scenes = main._build_scenes(
            STORYBOARD, ["img0.png", None], ["a0.wav", "a1.wav"]
        )

        self.assertEqual(len(scenes), 1)
        self.assertEqual(scenes[0]["image"], "img0.png")
        self.assertEqual(scenes[0]["caption"], "n1")
        self.assertEqual(scenes[0]["audio"], "a0.wav")

    def test_pairs_image_caption_and_audio_by_index(self):
        scenes = main._build_scenes(
            STORYBOARD, ["img0.png", "img1.png"], [None, "a1.wav"]
        )

        self.assertEqual(len(scenes), 2)
        self.assertIsNone(scenes[0]["audio"])
        self.assertEqual(scenes[1]["audio"], "a1.wav")
        self.assertEqual(scenes[1]["caption"], "n2")


class PipelineOrchestrationTest(unittest.TestCase):
    def tearDown(self):
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.upload_to_youtube", return_value="yt-video-123")
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.synthesize_narrations", return_value=(["a0.wav", "a1.wav"], None))
    @patch("main.generate_scene_images", return_value=(["img0.png", "img1.png"], None))
    @patch("main.generate_connected_script", return_value=SCRIPT_OK)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_fully_successful_run_marks_published(
        self, _fetch, _script, images, tts, render, _upload, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        # 스토리보드 장면 지시문이 이미지 생성으로 전달됐는지
        self.assertEqual(images.call_args.kwargs["scenes"], ["s1", "s2"])
        # 내레이션이 TTS로 전달됐는지
        tts.assert_called_once_with(["n1", "n2"])
        # 렌더러가 storyboard 모드로 호출됐는지
        scenes = render.call_args.kwargs["scenes"]
        self.assertEqual(len(scenes), 2)
        self.assertEqual(scenes[0]["audio"], "a0.wav")

        final = update_episode.call_args_list[-1]
        self.assertEqual(final.kwargs["status"], "published")
        self.assertIsNone(final.kwargs["degraded_reason"])

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.upload_to_youtube", return_value="yt-video-123")
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.synthesize_narrations", return_value=([None, None], "tts:no_api_key"))
    @patch("main.generate_scene_images", return_value=([None, None], "image:no_api_key"))
    @patch("main.generate_connected_script", return_value=SCRIPT_DEGRADED)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_all_ai_steps_degraded_marks_published_degraded(
        self, _fetch, _script, _images, _tts, render, _upload, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        # 이미지가 하나도 없으므로 scenes=None -> 렌더러가 텍스트카드로 폴백
        self.assertIsNone(render.call_args.kwargs["scenes"])
        final = update_episode.call_args_list[-1]
        self.assertEqual(final.kwargs["status"], "published_degraded")
        self.assertEqual(
            final.kwargs["degraded_reason"],
            "story:RuntimeError;image:no_api_key;tts:no_api_key",
        )

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.upload_to_youtube", return_value="yt-video-123")
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.synthesize_narrations", return_value=(["a0.wav", None], "tts:partial_1of2"))
    @patch("main.generate_scene_images", return_value=(["img0.png", "img1.png"], None))
    @patch("main.generate_connected_script", return_value=SCRIPT_OK)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_partial_tts_still_renders_all_scenes(
        self, _fetch, _script, _images, _tts, render, _upload, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        scenes = render.call_args.kwargs["scenes"]
        self.assertEqual(len(scenes), 2)
        self.assertEqual(scenes[0]["audio"], "a0.wav")
        self.assertIsNone(scenes[1]["audio"])
        final = update_episode.call_args_list[-1]
        self.assertEqual(final.kwargs["status"], "published_degraded")

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.upload_to_youtube", return_value=None)
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.synthesize_narrations", return_value=(["a0.wav", "a1.wav"], None))
    @patch("main.generate_scene_images", return_value=(["img0.png", "img1.png"], None))
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
    @patch("main.synthesize_narrations", return_value=([None, None], "tts:no_api_key"))
    @patch("main.generate_scene_images", return_value=([None, None], "image:no_api_key"))
    @patch("main.generate_connected_script", return_value=SCRIPT_OK)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_render_failure_marks_episode_failed_with_reasons(
        self, _fetch, _script, _images, _tts, _render, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 1)
        update_episode.assert_called_once_with(
            "ep-0103-abcd1234",
            status="failed",
            degraded_reason="image:no_api_key;tts:no_api_key",
        )


if __name__ == "__main__":
    unittest.main()
