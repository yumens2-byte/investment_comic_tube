import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from src.episode import Episode
from src.episode_repository import SupabaseEpisodeRepository


FIXTURE = Path(__file__).parent / "fixtures" / "episode_sample.json"


class SupabaseEpisodeRepositoryTest(unittest.TestCase):
    @patch("src.episode_repository.requests.post")
    def test_upserts_by_episode_id_without_logging_key(self, post):
        response = Mock()
        post.return_value = response
        episode = Episode.from_dict(json.loads(FIXTURE.read_text("utf-8")))

        result = SupabaseEpisodeRepository("https://project.supabase.co", "secret").save(episode)

        self.assertEqual(result.backend, "supabase")
        url = post.call_args.args[0]
        kwargs = post.call_args.kwargs
        self.assertEqual(url, "https://project.supabase.co/rest/v1/episodes?on_conflict=episode_id")
        self.assertEqual(kwargs["headers"]["Prefer"], "resolution=merge-duplicates,return=minimal")
        self.assertEqual(kwargs["json"]["status"], "SCRIPT_READY")
        response.raise_for_status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
