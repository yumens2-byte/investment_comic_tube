import logging
import tempfile
import unittest
from unittest.mock import patch

import main


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
    @patch("main.generate_scene_images", return_value=["scene_0.jpg", "scene_1.jpg"])
    @patch(
        "main.generate_connected_script",
        return_value={"episode": 103, "villain": "Debt Titan", "narration": "n", "episode_id": "ep-0103-abcd1234"},
    )
    @patch("main.fetch_market_data", return_value={"TNX": {"close": 4.8}, "VIX": {"close": 15.0}})
    def test_success_path_updates_episode_to_published(
        self, _fetch, _script, _images, _render, upload, update_episode, _step_start, _step_finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 0)
        upload.assert_called_once_with("output_short.mp4", {
            "episode": 103, "villain": "Debt Titan", "narration": "n", "episode_id": "ep-0103-abcd1234",
        })
        final_call = update_episode.call_args_list[-1]
        self.assertEqual(final_call.args[0], "ep-0103-abcd1234")
        self.assertEqual(final_call.kwargs["status"], "published")
        self.assertEqual(final_call.kwargs["youtube_video_id"], "yt-video-123")

    @patch("main.record_step_finish")
    @patch("main.record_step_start", return_value="step-run-1")
    @patch("main.update_episode")
    @patch("main.render_video", side_effect=RuntimeError("ffmpeg exploded"))
    @patch("main.generate_scene_images", return_value=[])
    @patch(
        "main.generate_connected_script",
        return_value={"episode": 103, "villain": "Debt Titan", "narration": "n", "episode_id": "ep-0103-abcd1234"},
    )
    @patch("main.fetch_market_data", return_value={"TNX": {"close": 4.8}, "VIX": {"close": 15.0}})
    def test_render_failure_marks_episode_failed(
        self, _fetch, _script, _images, _render, update_episode, _step_start, _step_finish
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            exit_code = main.main()

        self.assertEqual(exit_code, 1)
        update_episode.assert_called_once_with("ep-0103-abcd1234", status="failed")


if __name__ == "__main__":
    unittest.main()
