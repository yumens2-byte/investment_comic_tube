import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.image_generator import generate_scene_images


class GenerateSceneImagesTest(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_no_api_key_returns_empty_list(self):
        result = generate_scene_images({"villain": "Debt Titan", "theme": "긴축"})

        self.assertEqual(result, [])

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True)
    @patch("google.genai.Client")
    def test_api_failure_returns_empty_list(self, client_cls):
        client_cls.return_value.models.generate_images.side_effect = RuntimeError("quota exceeded")

        with tempfile.TemporaryDirectory() as directory:
            result = generate_scene_images(
                {"villain": "Debt Titan", "theme": "긴축"}, output_dir=directory
            )

        self.assertEqual(result, [])

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True)
    @patch("google.genai.Client")
    def test_success_writes_images_and_returns_paths(self, client_cls):
        fake_image = MagicMock()
        fake_image.image.image_bytes = b"\xff\xd8fake-jpeg-bytes"
        response = MagicMock()
        response.generated_images = [fake_image, fake_image]
        client_cls.return_value.models.generate_images.return_value = response

        with tempfile.TemporaryDirectory() as directory:
            result = generate_scene_images(
                {"villain": "Debt Titan", "theme": "긴축"}, output_dir=directory, count=2
            )

            self.assertEqual(len(result), 2)
            for path in result:
                with open(path, "rb") as f:
                    self.assertEqual(f.read(), b"\xff\xd8fake-jpeg-bytes")


if __name__ == "__main__":
    unittest.main()
