import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.image_generator import generate_scene_images


def _make_part(data: bytes | None):
    part = MagicMock()
    if data is None:
        part.inline_data = None
    else:
        part.inline_data.data = data
    return part


class GenerateSceneImagesTest(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_no_api_key_returns_reason(self):
        paths, reason = generate_scene_images({"villain": "Debt Titan", "theme": "긴축"})

        self.assertTrue(all(p is None for p in paths))
        self.assertEqual(reason, "image:no_api_key")

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True)
    @patch("google.genai.Client")
    def test_api_failure_returns_reason(self, client_cls):
        client_cls.return_value.models.generate_content.side_effect = RuntimeError("transient network blip")

        with tempfile.TemporaryDirectory() as directory:
            paths, reason = generate_scene_images(
                {"villain": "Debt Titan", "theme": "긴축"}, output_dir=directory
            )

        self.assertTrue(all(p is None for p in paths))
        self.assertIn("RuntimeError", reason)

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True)
    @patch("google.genai.Client")
    def test_response_without_image_parts_returns_reason(self, client_cls):
        response = MagicMock()
        response.parts = [_make_part(None)]
        client_cls.return_value.models.generate_content.return_value = response

        with tempfile.TemporaryDirectory() as directory:
            paths, reason = generate_scene_images(
                {"villain": "Debt Titan", "theme": "긴축"}, output_dir=directory
            )

        self.assertTrue(all(p is None for p in paths))
        self.assertEqual(reason, "image:no_inline_image_in_response")

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True)
    @patch("google.genai.Client")
    def test_success_writes_images_and_returns_paths(self, client_cls):
        response = MagicMock()
        response.parts = [_make_part(b"\x89PNGfake-bytes")]
        client_cls.return_value.models.generate_content.return_value = response

        with tempfile.TemporaryDirectory() as directory:
            paths, reason = generate_scene_images(
                {"villain": "Debt Titan", "theme": "긴축"}, output_dir=directory, count=2
            )

            self.assertIsNone(reason)
            self.assertEqual(len(paths), 2)
            for path in paths:
                with open(path, "rb") as f:
                    self.assertEqual(f.read(), b"\x89PNGfake-bytes")

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True)
    @patch("google.genai.Client")
    def test_uses_generate_content_not_generate_images(self, client_cls):
        response = MagicMock()
        response.parts = [_make_part(b"\x89PNGfake-bytes")]
        models = client_cls.return_value.models
        models.generate_content.return_value = response

        with tempfile.TemporaryDirectory() as directory:
            generate_scene_images({"villain": "Debt Titan", "theme": "긴축"}, output_dir=directory)

        models.generate_content.assert_called()
        models.generate_images.assert_not_called()
        self.assertEqual(
            models.generate_content.call_args.kwargs["model"], "gemini-3.1-flash-image"
        )


if __name__ == "__main__":
    unittest.main()


class QuotaFailFastTest(unittest.TestCase):
    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True)
    @patch("google.genai.Client")
    def test_quota_exhaustion_aborts_remaining_calls(self, client_cls):
        models = client_cls.return_value.models
        models.generate_content.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED: project has exceeded its monthly spending cap"
        )

        with tempfile.TemporaryDirectory() as directory:
            paths, reason = generate_scene_images(
                {"villain": "Debt Titan", "theme": "긴축"},
                output_dir=directory,
                scenes=["s1", "s2", "s3", "s4", "s5", "s6"],
            )

        # 첫 호출에서 한도 소진을 감지하면 나머지 5회는 호출하지 않는다
        self.assertEqual(models.generate_content.call_count, 1)
        self.assertEqual(len(paths), 6)
        self.assertTrue(all(p is None for p in paths))
        self.assertEqual(reason, "image:quota_exhausted")

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True)
    @patch("google.genai.Client")
    def test_non_quota_error_keeps_trying_other_scenes(self, client_cls):
        models = client_cls.return_value.models
        models.generate_content.side_effect = RuntimeError("temporary hiccup")

        with tempfile.TemporaryDirectory() as directory:
            generate_scene_images(
                {"villain": "Debt Titan", "theme": "긴축"},
                output_dir=directory,
                scenes=["s1", "s2", "s3"],
            )

        self.assertEqual(models.generate_content.call_count, 3)
