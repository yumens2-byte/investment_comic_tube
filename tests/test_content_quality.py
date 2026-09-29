import unittest
from unittest.mock import patch

from src.content_quality import ContentQualityError, validate_media_package
from src.renderer import render_video


class ContentQualityTest(unittest.TestCase):
    def test_complete_four_images_and_six_voices_pass(self):
        validate_media_package([f"scene-{n}.png" for n in range(4)],
                               [f"beat-{n}.wav" for n in range(6)])

    def test_missing_image_blocks_render_and_upload(self):
        with self.assertRaises(ContentQualityError):
            validate_media_package(["a.png", None, "c.png", "d.png"],
                                   [f"beat-{n}.wav" for n in range(6)])

    def test_repeated_image_slot_blocks_low_quality_fallback(self):
        with self.assertRaises(ContentQualityError):
            validate_media_package(["a.png"] * 4,
                                   [f"beat-{n}.wav" for n in range(6)])

    def test_missing_voice_blocks_silent_video(self):
        with self.assertRaises(ContentQualityError):
            validate_media_package([f"scene-{n}.png" for n in range(4)],
                                   ["a.wav", None, "c.wav", "d.wav", "e.wav", "f.wav"])

    def test_production_render_does_not_fall_back_to_text_card(self):
        with patch("src.renderer._render_storyboard", side_effect=RuntimeError("codec failed")), \
             patch("src.renderer._render_text_card") as text_card:
            with self.assertRaisesRegex(RuntimeError, "text card upload blocked"):
                render_video({"villain": "Debt Titan"}, scenes=[{"image": "i.png"}],
                             require_storyboard=True)
            text_card.assert_not_called()


if __name__ == "__main__":
    unittest.main()
