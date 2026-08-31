import copy
import json
from pathlib import Path
import tempfile
import unittest

from src.episode import Episode, EpisodeValidationError
from src.episode_repository import LocalEpisodeRepository, content_hash


FIXTURE = Path(__file__).parent / "fixtures" / "episode_sample.json"


class EpisodeTest(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_sample_contract(self):
        episode = Episode.from_dict(self.payload)
        self.assertEqual(episode.episode_id, "EP-20260826-02")
        self.assertEqual(episode.total_duration_seconds, 30)
        self.assertEqual(episode.dgs10, 4.62)
        self.assertEqual(episode.sp500_change, 0.32)
        self.assertEqual(len(episode.sequences), 3)

    def test_rejects_episode_date_mismatch(self):
        invalid = copy.deepcopy(self.payload)
        invalid["data_summary"]["date"] = "2026-08-25"
        with self.assertRaisesRegex(EpisodeValidationError, "date must equal"):
            Episode.from_dict(invalid)

    def test_rejects_prompt_without_vertical_format(self):
        invalid = copy.deepcopy(self.payload)
        invalid["sequence_pipeline"][0]["video_prompt"] = "cinematic landscape"
        with self.assertRaisesRegex(EpisodeValidationError, "9:16"):
            Episode.from_dict(invalid)

    def test_rejects_invalid_total_duration(self):
        invalid = copy.deepcopy(self.payload)
        invalid["sequence_pipeline"] = invalid["sequence_pipeline"][:1]
        with self.assertRaisesRegex(EpisodeValidationError, "15-60"):
            Episode.from_dict(invalid)

    def test_local_save_is_deterministic_and_writes_manifest(self):
        episode = Episode.from_dict(self.payload)
        with tempfile.TemporaryDirectory() as directory:
            result = LocalEpisodeRepository(directory).save(episode)
            artifact = Path(result.artifact_path)
            manifest = json.loads((artifact.parent / "manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["status"], "SCRIPT_READY")
            self.assertEqual(manifest["content_hash"], content_hash(self.payload))
            self.assertEqual(manifest["sequence_count"], 3)


if __name__ == "__main__":
    unittest.main()
