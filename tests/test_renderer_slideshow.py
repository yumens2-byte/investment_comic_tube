import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.renderer import render_video


class SlideshowRenderTest(unittest.TestCase):
    @patch("src.renderer.subprocess.run")
    @patch("src.renderer.os.path.getsize", return_value=51200)
    def test_slideshow_mode_runs_ffmpeg_per_segment_and_concat(self, _getsize, run):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""

        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            result = render_video(
                {"episode": 103, "villain": "Debt Titan", "narration": "긴장감이 감돈다"},
                image_paths=["scene_0.jpg", "scene_1.jpg"],
            )

        self.assertEqual(result, "output_short.mp4")
        # 세그먼트 2개 + concat 1회 = 최소 3회 ffmpeg 호출
        self.assertGreaterEqual(run.call_count, 3)

    @patch("src.renderer.subprocess.run")
    @patch("src.renderer.os.path.getsize", return_value=39440)
    def test_slideshow_failure_falls_back_to_text_card(self, _getsize, run):
        # 첫 호출(슬라이드쇼 세그먼트)만 실패시키고 이후 호출은 성공시킨다.
        # _probe_duration 도 subprocess.run 을 쓰므로 고정 리스트 대신 함수형으로 둔다.
        calls = {"n": 0}

        def _side_effect(*_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return MagicMock(returncode=1, stdout="", stderr="slideshow ffmpeg error")
            return MagicMock(returncode=0, stdout="", stderr="")

        run.side_effect = _side_effect

        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            result = render_video(
                {"episode": 103, "villain": "Debt Titan"},
                image_paths=["scene_0.jpg"],
            )

        self.assertEqual(result, "output_short.mp4")

    @patch("src.renderer.subprocess.run")
    @patch("src.renderer.os.path.getsize", return_value=39440)
    def test_no_image_paths_uses_text_card_directly(self, _getsize, run):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""

        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOG_DIR": directory}):
            result = render_video({"episode": 103, "villain": "Debt Titan"})

        self.assertEqual(result, "output_short.mp4")
        self.assertGreaterEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
