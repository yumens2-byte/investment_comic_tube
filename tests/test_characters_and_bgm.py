import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.characters import (
    HERO_APPEARANCE,
    VILLAIN_APPEARANCE,
    get_villain_appearance,
)
from src.image_generator import _build_prompt
from src.renderer import _find_bgm


class CharacterSheetTest(unittest.TestCase):
    def test_hero_is_described_as_tiger(self):
        lowered = HERO_APPEARANCE.lower()
        self.assertIn("tiger", lowered)
        self.assertIn("not a human", lowered)

    def test_known_villains_have_appearance(self):
        for villain in ("Debt Titan", "Chaos Reaper", "Bull Brute"):
            self.assertIn(villain, VILLAIN_APPEARANCE)
            self.assertTrue(get_villain_appearance(villain))

    def test_unknown_villain_falls_back(self):
        result = get_villain_appearance("Nonexistent Villain")
        self.assertTrue(result)
        self.assertNotIn("Nonexistent Villain", VILLAIN_APPEARANCE)


class PromptTest(unittest.TestCase):
    def test_prompt_includes_tiger_and_villain_appearance(self):
        prompt = _build_prompt({"villain": "Debt Titan", "theme": "긴축"})
        self.assertIn("tiger", prompt.lower())
        self.assertIn("Debt Titan", prompt)
        self.assertIn("rusted chains", prompt)

    def test_prompt_forbids_text_explicitly(self):
        prompt = _build_prompt({"villain": "Bull Brute", "theme": "돌파"})
        lowered = prompt.lower()
        self.assertIn("no text", lowered)
        self.assertIn("no letters", lowered)
        self.assertIn("no speech bubbles", lowered)


class FindBgmTest(unittest.TestCase):
    def test_missing_directory_returns_none(self):
        with patch.dict("os.environ", {"BGM_DIR": "/nonexistent/path/bgm"}, clear=True):
            self.assertIsNone(_find_bgm())

    def test_empty_directory_returns_none(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"BGM_DIR": directory}, clear=True
        ):
            self.assertIsNone(_find_bgm())

    def test_readme_only_directory_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "README.md").write_text("doc", encoding="utf-8")
            with patch.dict("os.environ", {"BGM_DIR": directory}, clear=True):
                self.assertIsNone(_find_bgm())

    def test_picks_an_audio_file(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "track_a.mp3").write_bytes(b"fake")
            (Path(directory) / "track_b.wav").write_bytes(b"fake")
            (Path(directory) / "README.md").write_text("doc", encoding="utf-8")
            with patch.dict("os.environ", {"BGM_DIR": directory}, clear=True):
                chosen = _find_bgm()

        self.assertIsNotNone(chosen)
        self.assertTrue(chosen.endswith((".mp3", ".wav")))


class RenderBgmIntegrationTest(unittest.TestCase):
    @patch("src.renderer.subprocess.run")
    @patch("src.renderer.os.path.getsize", return_value=51200)
    @patch("src.renderer.os.replace")
    def test_bgm_applied_when_file_present(self, os_replace, _getsize, run):
        run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        from src.renderer import render_video

        with tempfile.TemporaryDirectory() as directory:
            bgm_dir = Path(directory) / "bgm"
            bgm_dir.mkdir()
            (bgm_dir / "track.mp3").write_bytes(b"fake")
            env = {"LOG_DIR": directory, "BGM_DIR": str(bgm_dir)}
            with patch.dict("os.environ", env, clear=True):
                render_video({"episode": 103, "villain": "Debt Titan"})

        os_replace.assert_called_once()
        commands = [" ".join(c.args[0]) for c in run.call_args_list]
        self.assertTrue(any("stream_loop" in c for c in commands))

    @patch("src.renderer.subprocess.run")
    @patch("src.renderer.os.path.getsize", return_value=39440)
    @patch("src.renderer.os.replace")
    def test_no_bgm_leaves_video_untouched(self, os_replace, _getsize, run):
        run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        from src.renderer import render_video

        with tempfile.TemporaryDirectory() as directory:
            bgm_dir = Path(directory) / "bgm"
            bgm_dir.mkdir()
            env = {"LOG_DIR": directory, "BGM_DIR": str(bgm_dir)}
            with patch.dict("os.environ", env, clear=True):
                render_video({"episode": 103, "villain": "Debt Titan"})

        os_replace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
