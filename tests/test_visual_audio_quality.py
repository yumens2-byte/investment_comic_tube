import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from src.content_quality import ContentQualityError, validate_image_assets
from src.renderer import _render_segment, _wrap_korean


class ImageAssetQualityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = []
        for i in range(4):
            image = Image.new("RGB", (576, 1024), "#4b5563")
            draw = ImageDraw.Draw(image)
            for y in range(0, 1024, 20):
                draw.line((0, y, 575, y + 200), fill=(40 + y // 10, 80, 130), width=10)
            path = Path(self.tmp.name) / f"scene_{i}.png"
            image.save(path)
            self.paths.append(str(path))

    def test_full_illustrations_pass(self):
        validate_image_assets(self.paths)

    def test_flat_lower_band_aborts(self):
        path = Path(self.paths[0])
        with Image.open(path) as source:
            image = source.convert("RGB")
        ImageDraw.Draw(image).rectangle((0, 790, 575, 1023), fill="#999999")
        image.save(path)
        with self.assertRaisesRegex(ContentQualityError, "하단 빈"):
            validate_image_assets(self.paths)

    def test_corrupt_or_landscape_aborts(self):
        Path(self.paths[0]).write_bytes(b"not a png")
        with self.assertRaises(ContentQualityError):
            validate_image_assets(self.paths)
        Image.new("RGB", (1024, 576), "red").save(self.paths[0])
        with self.assertRaises(ContentQualityError):
            validate_image_assets(self.paths)


class SceneCaptionAndAudioTest(unittest.TestCase):
    def test_long_caption_is_rejected_instead_of_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, "caption exceeds"):
            _wrap_korean("길이가 긴 한국어 문장을 반복합니다 " * 10, 19, max_lines=3)

    @patch("src.renderer._run_ffmpeg")
    @patch("src.renderer.find_kr_font", return_value="/tmp/font.ttf")
    def test_body_scene_draws_caption_and_preserves_audio(self, _font, run):
        with tempfile.TemporaryDirectory() as directory:
            _render_segment("image.png", "나스닥이 0.22% 하락", "voice.wav", 7.0,
                            Path(directory) / "segment_1.mp4", Path(directory) / "ffmpeg.log",
                            False, narration_dur=9.44)
            cmd = run.call_args.args[0]
            self.assertIn("textfile=", cmd[cmd.index("-vf") + 1])
            self.assertIn("expansion=none", cmd[cmd.index("-vf") + 1])
            self.assertIn("나스닥이 0.22% 하락", (Path(directory) / "caption_segment_1.txt").read_text())
            self.assertIn("atempo=1.378", cmd[cmd.index("-af") + 1])

    @patch("src.renderer._run_ffmpeg")
    @patch("src.renderer.find_kr_font", return_value="/tmp/font.ttf")
    def test_body_voice_too_long_aborts_before_ffmpeg(self, _font, run):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "safe tempo"):
                _render_segment("image.png", "긴 문장", "voice.wav", 7.0,
                                Path(directory) / "segment_1.mp4", Path(directory) / "ffmpeg.log",
                                False, narration_dur=12.0)
        run.assert_not_called()
