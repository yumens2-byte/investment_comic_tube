import logging
import tempfile
import unittest
from unittest.mock import patch

import main

STORYBOARD = [
    {"beat": "HOOK", "scene": "s1", "narration": "n1"},
    {"beat": "THREAT", "scene": "s2", "narration": "n2"},
    {"beat": "IMPACT", "scene": "s3", "narration": "n3"},
    {"beat": "HERO", "scene": "s4", "narration": "n4"},
    {"beat": "CLASH", "scene": "s5", "narration": "n5"},
    {"beat": "LESSON", "scene": "s6", "narration": "n6"},
]
IMAGES3 = ["img0.png", "img1.png", "img2.png"]
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
MARKET = {"TNX": {"close": 4.8}, "VIX": {"close": 15.0}}


class BuildScenesTest(unittest.TestCase):
    def test_three_images_are_reused_across_six_beats(self):
        scenes = main._build_scenes(STORYBOARD, IMAGES3, AUDIO6)

        # 6비트 전부 렌더링되며, 이미지는 슬롯 매핑 [0,0,0,1,2,2] 대로 재사용된다
        self.assertEqual(len(scenes), 6)
        self.assertEqual(
            [s["image"] for s in scenes],
            ["img0.png", "img0.png", "img0.png", "img1.png", "img2.png", "img2.png"],
        )
        self.assertEqual([s["caption"] for s in scenes], [f"n{i}" for i in range(1, 7)])
        self.assertEqual([s["audio"] for s in scenes], AUDIO6)

    def test_missing_slot_image_falls_back_to_available_one(self):
        scenes = main._build_scenes(STORYBOARD, ["img0.png", None, None], AUDIO6)

        # 슬롯 1,2 이미지가 없어도 비트가 사라지지 않고 생성된 이미지로 대체된다
        self.assertEqual(len(scenes), 6)
        self.assertTrue(all(s["image"] == "img0.png" for s in scenes))

    def test_no_images_yields_no_scenes(self):
        scenes = main._build_scenes(STORYBOARD, [None, None, None], AUDIO6)

        self.assertEqual(scenes, [])


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
    @patch("main.synthesize_narrations", return_value=(AUDIO6, None))
    @patch("main.generate_scene_images", return_value=(IMAGES3, None))
    @patch("main.generate_connected_script", return_value=SCRIPT_OK)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_fully_successful_run_marks_published(
        self, _fetch, _script, images, tts, render, _upload, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        # 비용 통제: 비트(6)가 아니라 슬롯(3)만큼만 이미지를 생성한다
        from src.story import SLOT_SCENES
        self.assertEqual(images.call_args.kwargs["scenes"], SLOT_SCENES)
        self.assertEqual(len(SLOT_SCENES), 3)
        # 내레이션 6줄이 전부 TTS로 전달됐는지
        tts.assert_called_once_with([f"n{i}" for i in range(1, 7)])
        # 이미지 3장으로 6장면이 렌더링되는지
        scenes = render.call_args.kwargs["scenes"]
        self.assertEqual(len(scenes), 6)
        self.assertEqual(scenes[0]["audio"], "a0.wav")

        final = update_episode.call_args_list[-1]
        self.assertEqual(final.kwargs["status"], "published")
        self.assertIsNone(final.kwargs["degraded_reason"])

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.upload_to_youtube", return_value="yt-video-123")
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.synthesize_narrations", return_value=([None] * 6, "tts:no_api_key"))
    @patch("main.generate_scene_images", return_value=([None] * 3, "image:no_api_key"))
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
    @patch("main.synthesize_narrations", return_value=(["a0.wav"] + [None] * 5, "tts:partial_1of6"))
    @patch("main.generate_scene_images", return_value=(IMAGES3, None))
    @patch("main.generate_connected_script", return_value=SCRIPT_OK)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_partial_tts_still_renders_all_scenes(
        self, _fetch, _script, _images, _tts, render, _upload, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        scenes = render.call_args.kwargs["scenes"]
        self.assertEqual(len(scenes), 6)
        self.assertEqual(scenes[0]["audio"], "a0.wav")
        self.assertIsNone(scenes[1]["audio"])
        final = update_episode.call_args_list[-1]
        self.assertEqual(final.kwargs["status"], "published_degraded")

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.upload_to_youtube", return_value=None)
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.synthesize_narrations", return_value=(AUDIO6, None))
    @patch("main.generate_scene_images", return_value=(IMAGES3, None))
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
    @patch("main.synthesize_narrations", return_value=([None] * 6, "tts:no_api_key"))
    @patch("main.generate_scene_images", return_value=([None] * 3, "image:no_api_key"))
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
