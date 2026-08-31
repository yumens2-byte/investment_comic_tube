import logging
import tempfile
import unittest
from unittest.mock import patch

import main

SCRIPT_OK = {
    "episode": 103,
    "villain": "Debt Titan",
    "narration": "n",
    "episode_id": "ep-0103-abcd1234",
    "degraded_reason": None,
}
SCRIPT_DEGRADED = dict(SCRIPT_OK, degraded_reason="narration:RuntimeError")
MARKET = {"TNX": {"close": 4.8}, "VIX": {"close": 15.0}}


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
    @patch("main.generate_scene_images", return_value=(["scene_0.png", "scene_1.png"], None))
    @patch("main.generate_connected_script", return_value=SCRIPT_OK)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_fully_successful_run_marks_published(
        self, _fetch, _script, _images, _render, upload, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        upload.assert_called_once_with("output_short.mp4", SCRIPT_OK)
        final = update_episode.call_args_list[-1]
        self.assertEqual(final.kwargs["status"], "published")
        self.assertIsNone(final.kwargs["degraded_reason"])

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.upload_to_youtube", return_value="yt-video-123")
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.generate_scene_images", return_value=([], "image:no_api_key"))
    @patch("main.generate_connected_script", return_value=SCRIPT_DEGRADED)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_both_ai_steps_degraded_marks_published_degraded(
        self, _fetch, _script, _images, render, _upload, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        # 이미지가 없으므로 렌더러에 image_paths=None 이 전달돼 텍스트카드로 폴백
        self.assertIsNone(render.call_args.kwargs["image_paths"])
        final = update_episode.call_args_list[-1]
        self.assertEqual(final.kwargs["status"], "published_degraded")
        self.assertEqual(
            final.kwargs["degraded_reason"], "narration:RuntimeError;image:no_api_key"
        )

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.upload_to_youtube", return_value=None)
    @patch("main.render_video", return_value="output_short.mp4")
    @patch("main.generate_scene_images", return_value=(["scene_0.png"], None))
    @patch("main.generate_connected_script", return_value=SCRIPT_OK)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_no_upload_marks_rendered_no_upload(
        self, _fetch, _script, _images, _render, _upload, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        final = update_episode.call_args_list[-1]
        self.assertEqual(final.kwargs["status"], "rendered_no_upload")

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.render_video", side_effect=RuntimeError("ffmpeg exploded"))
    @patch("main.generate_scene_images", return_value=([], "image:no_api_key"))
    @patch("main.generate_connected_script", return_value=SCRIPT_OK)
    @patch("main.fetch_market_data", return_value=MARKET)
    def test_render_failure_marks_episode_failed_with_reason(
        self, _fetch, _script, _images, _render, update_episode, _start, _finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 1)
        update_episode.assert_called_once_with(
            "ep-0103-abcd1234", status="failed", degraded_reason="image:no_api_key"
        )


if __name__ == "__main__":
    unittest.main()
