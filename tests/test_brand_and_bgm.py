import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.renderer import (
    BGM_EXTENSIONS,
    OUTRO_DURATION_SEC,
    SFX_EXTENSIONS,
    _find_bgm,
    _find_brand_asset,
    _find_sfx,
    _has_audio_stream,
    _render_outro_segment,
)


def _make(dirpath, name):
    p = Path(dirpath) / name
    p.write_bytes(b"x")
    return p


class Mp4SupportTest(unittest.TestCase):
    def test_mp4_is_accepted_for_bgm_and_sfx(self):
        # 마스터가 .mp4 로 업로드하므로 반드시 지원돼야 한다
        self.assertIn(".mp4", BGM_EXTENSIONS)
        self.assertIn(".mp4", SFX_EXTENSIONS)


class AudioStreamGuardTest(unittest.TestCase):
    def test_file_without_audio_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            silent_video = Path(d) / "novideo.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                 "-c:v", "libx264", str(silent_video), "-loglevel", "error"],
                check=True,
            )
            self.assertFalse(_has_audio_stream(str(silent_video)))

    def test_file_with_audio_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            audio = Path(d) / "tone.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                 "-c:a", "aac", str(audio), "-loglevel", "error"],
                check=True,
            )
            self.assertTrue(_has_audio_stream(str(audio)))

    def test_missing_file_returns_false(self):
        self.assertFalse(_has_audio_stream("/nonexistent/file.mp4"))


class VillainBgmMatchingTest(unittest.TestCase):
    def _dir_with(self, names):
        d = tempfile.mkdtemp()
        for n in names:
            _make(d, n)
        return d

    @patch("src.renderer._has_audio_stream", return_value=True)
    def test_villain_specific_track_preferred(self, _probe):
        d = self._dir_with([
            "bgm_debt_titan_01.mp4", "bgm_chaos_reaper_01.mp4", "bgm_common_01.mp4",
        ])
        with patch.dict("os.environ", {"BGM_DIR": d}, clear=True):
            self.assertTrue(_find_bgm("Debt Titan").endswith("bgm_debt_titan_01.mp4"))
            self.assertTrue(_find_bgm("Chaos Reaper").endswith("bgm_chaos_reaper_01.mp4"))

    @patch("src.renderer._has_audio_stream", return_value=True)
    def test_falls_back_to_common(self, _probe):
        d = self._dir_with(["bgm_common_01.mp4"])
        with patch.dict("os.environ", {"BGM_DIR": d}, clear=True):
            self.assertTrue(_find_bgm("Debt Titan").endswith("bgm_common_01.mp4"))

    @patch("src.renderer._has_audio_stream", return_value=True)
    def test_falls_back_to_any_track(self, _probe):
        d = self._dir_with(["random_track.mp3"])
        with patch.dict("os.environ", {"BGM_DIR": d}, clear=True):
            self.assertTrue(_find_bgm("Debt Titan").endswith("random_track.mp3"))

    @patch("src.renderer._has_audio_stream", return_value=True)
    def test_unknown_villain_uses_common(self, _probe):
        d = self._dir_with(["bgm_debt_titan_01.mp4", "bgm_common_01.mp4"])
        with patch.dict("os.environ", {"BGM_DIR": d}, clear=True):
            self.assertTrue(_find_bgm("Nonexistent").endswith("bgm_common_01.mp4"))

    @patch("src.renderer._has_audio_stream", return_value=False)
    def test_all_files_without_audio_yield_none(self, _probe):
        d = self._dir_with(["bgm_common_01.mp4"])
        with patch.dict("os.environ", {"BGM_DIR": d}, clear=True):
            self.assertIsNone(_find_bgm("Debt Titan"))

    def test_missing_directory_returns_none(self):
        with patch.dict("os.environ", {"BGM_DIR": "/nonexistent/bgm"}, clear=True):
            self.assertIsNone(_find_bgm("Debt Titan"))


class SfxMp4Test(unittest.TestCase):
    @patch("src.renderer._has_audio_stream", return_value=True)
    def test_mp4_sfx_matched_by_stem(self, _probe):
        with tempfile.TemporaryDirectory() as d:
            _make(d, "hook_a.mp4")
            _make(d, "hook_b.mp4")
            with patch.dict("os.environ", {"SFX_DIR": d}, clear=True):
                self.assertTrue(_find_sfx("hook_b").endswith("hook_b.mp4"))


class BrandAssetTest(unittest.TestCase):
    def test_logo_is_reserved_and_excluded_from_covers(self):
        with tempfile.TemporaryDirectory() as d:
            _make(d, "logo.png")
            _make(d, "EDT_UNIVERS_Invest_01_Area.png")
            with patch.dict("os.environ", {"BRAND_DIR": d}, clear=True):
                self.assertTrue(_find_brand_asset(stem_is_logo=True).endswith("logo.png"))
                cover = _find_brand_asset(stem_is_logo=False)
                self.assertTrue(cover.endswith("EDT_UNIVERS_Invest_01_Area.png"))

    def test_no_cover_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            _make(d, "logo.png")
            with patch.dict("os.environ", {"BRAND_DIR": d}, clear=True):
                self.assertIsNone(_find_brand_asset(stem_is_logo=False))

    def test_readme_is_not_treated_as_cover(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "README.md").write_text("doc", encoding="utf-8")
            with patch.dict("os.environ", {"BRAND_DIR": d}, clear=True):
                self.assertIsNone(_find_brand_asset(stem_is_logo=False))

    def test_missing_directory_returns_none(self):
        with patch.dict("os.environ", {"BRAND_DIR": "/nonexistent/brand"}, clear=True):
            self.assertIsNone(_find_brand_asset(stem_is_logo=False))


class OutroRenderTest(unittest.TestCase):
    def test_skipped_when_no_cover(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.dict("os.environ", {"BRAND_DIR": d}, clear=True):
            rendered = _render_outro_segment(Path(d) / "o.mp4", Path(d) / "log.txt", False)
        self.assertFalse(rendered)

    @patch("src.renderer._run_ffmpeg")
    def test_logo_overlay_included_when_logo_present(self, run):
        with tempfile.TemporaryDirectory() as d:
            _make(d, "cover.png")
            _make(d, "logo.png")
            with patch.dict("os.environ", {"BRAND_DIR": d}, clear=True):
                self.assertTrue(
                    _render_outro_segment(Path(d) / "o.mp4", Path(d) / "log.txt", False)
                )
        cmd = " ".join(run.call_args.args[0])
        self.assertIn("overlay=", cmd)

    @patch("src.renderer._run_ffmpeg")
    def test_no_overlay_when_logo_absent(self, run):
        with tempfile.TemporaryDirectory() as d:
            _make(d, "cover.png")
            with patch.dict("os.environ", {"BRAND_DIR": d}, clear=True):
                _render_outro_segment(Path(d) / "o.mp4", Path(d) / "log.txt", False)
        self.assertNotIn("overlay=", " ".join(run.call_args.args[0]))

    def test_outro_duration_is_two_seconds(self):
        self.assertEqual(OUTRO_DURATION_SEC, 2.0)


class OutroPlacementTest(unittest.TestCase):
    @patch("src.renderer._concat_segments")
    @patch("src.renderer._render_outro_segment", return_value=True)
    @patch("src.renderer._render_segment")
    def test_outro_is_appended_last_not_first(self, _seg, _outro, concat):
        from src.renderer import _render_storyboard

        with tempfile.TemporaryDirectory() as d:
            scenes = [{"image": "a.png", "caption": "c", "audio": None}]
            _render_storyboard(scenes, str(Path(d) / "out.mp4"), Path(d) / "log.txt")

        segments = concat.call_args.args[0]
        # 쇼츠 이탈 방지를 위해 브랜드 화면은 반드시 마지막에만 온다
        self.assertTrue(segments[-1].name.startswith("segment_outro"))
        self.assertFalse(segments[0].name.startswith("segment_outro"))

    @patch("src.renderer._concat_segments")
    @patch("src.renderer._render_outro_segment", side_effect=RuntimeError("boom"))
    @patch("src.renderer._render_segment")
    def test_outro_failure_does_not_break_main_video(self, _seg, _outro, concat):
        from src.renderer import _render_storyboard

        with tempfile.TemporaryDirectory() as d:
            scenes = [{"image": "a.png", "caption": "c", "audio": None}]
            _render_storyboard(scenes, str(Path(d) / "out.mp4"), Path(d) / "log.txt")

        concat.assert_called_once()
        self.assertEqual(len(concat.call_args.args[0]), 1)


if __name__ == "__main__":
    unittest.main()
