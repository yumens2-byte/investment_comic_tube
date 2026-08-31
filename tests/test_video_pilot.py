import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from src.episode import Episode
from src.episode_repository import LocalEpisodeRepository
from src.video_pilot import VideoValidationError, render_storyboard_preview


FIXTURE = Path(__file__).parent / "fixtures" / "episode_sample.json"


class FakeMediaRunner:
    def __init__(self, *, width=1080, height=1920):
        self.width = width
        self.height = height

    def __call__(self, command, **_kwargs):
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"pilot-video")
            return subprocess.CompletedProcess(command, 0, "", "frame=750 Lsize=100kB")
        payload = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": self.width, "height": self.height},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"duration": "30.000000"},
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")


class VideoPilotTest(unittest.TestCase):
    def setUp(self):
        self.episode = Episode.from_json(FIXTURE.read_text(encoding="utf-8"))

    def test_story_to_video_to_manifest_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalEpisodeRepository(directory)
            repository.save(self.episode)
            video = render_storyboard_preview(self.episode, directory, runner=FakeMediaRunner())
            repository.mark_rendered(self.episode, video)

            manifest = json.loads(
                (Path(directory) / self.episode.episode_id / "manifest.json").read_text("utf-8")
            )
            self.assertEqual(manifest["status"], "RENDERED")
            self.assertEqual(manifest["video_width"], 1080)
            self.assertEqual(manifest["video_height"], 1920)
            self.assertEqual(manifest["video_codec"], "h264")
            self.assertEqual(len(manifest["video_hash"]), 64)

    def test_rejects_non_vertical_output(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(VideoValidationError, "expected 1080x1920"):
                render_storyboard_preview(
                    self.episode, directory, runner=FakeMediaRunner(width=1920, height=1080)
                )


if __name__ == "__main__":
    unittest.main()
