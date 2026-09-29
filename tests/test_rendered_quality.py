import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.content_quality import ContentQualityError, validate_rendered_video


class RenderedVideoQualityTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "video.mp4"
        self.path.write_bytes(b"sample")

    def check(self, duration=40, width=1080, height=1920, audio=True):
        streams = [{"codec_type": "video", "width": width, "height": height}]
        if audio:
            streams.append({"codec_type": "audio"})
        metadata = {"format": {"duration": str(duration)}, "streams": streams}
        with patch("src.content_quality.subprocess.run") as run:
            run.return_value.stdout = json.dumps(metadata)
            validate_rendered_video(str(self.path))
            self.assertEqual(run.call_args.args[0][0], "ffprobe")

    def test_valid_40_second_video_passes(self):
        self.check(duration=40)

    def test_missing_file_aborts(self):
        with self.assertRaises(ContentQualityError):
            validate_rendered_video(str(self.path.parent / "missing.mp4"))

    def test_invalid_duration_format_or_audio_aborts(self):
        for kwargs in ({"duration": 60}, {"duration": 0}, {"duration": float("nan")},
                       {"width": 720}, {"audio": False}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ContentQualityError):
                self.check(**kwargs)

    def test_probe_error_aborts(self):
        with patch("src.content_quality.subprocess.run", side_effect=subprocess.CalledProcessError(1, "ffprobe")):
            with self.assertRaises(ContentQualityError):
                validate_rendered_video(str(self.path))
