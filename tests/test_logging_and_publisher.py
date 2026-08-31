import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from google.auth.exceptions import RefreshError

from src.logging_config import configure_logging
from src.publisher import (
    YouTubeAuthenticationError,
    get_youtube_service,
    upload_to_youtube,
)
from src.renderer import render_video


class LoggingTest(unittest.TestCase):
    def tearDown(self):
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)

    def test_pipeline_log_is_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = configure_logging(directory)
            logging.getLogger("pilot").info("test_event episode_id=EP-20260826-02")
            for handler in logging.getLogger().handlers:
                handler.flush()
            self.assertIn("test_event", path.read_text(encoding="utf-8"))


class PublisherTest(unittest.TestCase):
    def test_missing_video_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            upload_to_youtube("does-not-exist.mp4", {"episode": 102, "villain": "Debt Titan"})

    @patch.dict("os.environ", {
        "YOUTUBE_CLIENT_ID": "client",
        "YOUTUBE_CLIENT_SECRET": "secret",
        "YOUTUBE_REFRESH_TOKEN": "revoked",
    }, clear=True)
    @patch("src.publisher.Credentials.refresh")
    def test_expired_token_has_actionable_error(self, refresh):
        refresh.side_effect = RefreshError("invalid_grant: Token has been expired or revoked")
        with self.assertRaisesRegex(YouTubeAuthenticationError, "Re-authorize"):
            get_youtube_service()


class RendererLoggingTest(unittest.TestCase):
    @patch("src.renderer.subprocess.run")
    @patch("src.renderer.os.path.getsize", return_value=40960)
    def test_ffmpeg_output_is_written_to_separate_log(self, _getsize, run):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = "frame=200 Lsize=39kB"
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"LOG_DIR": directory}):
                self.assertEqual(render_video({"episode": 102, "villain": "Debt Titan"}), "output_short.mp4")
            log = (Path(directory) / "ffmpeg.log").read_text(encoding="utf-8")
            self.assertIn("frame=200", log)


if __name__ == "__main__":
    unittest.main()
