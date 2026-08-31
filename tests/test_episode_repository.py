import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from src.episode import Episode
from src.episode_repository import SupabaseEpisodeRepository
from src.video_pilot import VideoArtifact


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

    @patch("src.episode_repository.requests.patch")
    def test_render_update_is_guarded_by_episode_and_content_hash(self, patch_request):
        response = Mock()
        response.json.return_value = [{"episode_id": "EP-20260826-02"}]
        patch_request.return_value = response
        episode = Episode.from_dict(json.loads(FIXTURE.read_text("utf-8")))
        video = VideoArtifact(Path("preview.mp4"), "a" * 64, 30, 1080, 1920, "h264", "aac")

        SupabaseEpisodeRepository("https://project.supabase.co", "secret").mark_rendered(episode, video)

        params = patch_request.call_args.kwargs["params"]
        self.assertEqual(params["episode_id"], "eq.EP-20260826-02")
        self.assertTrue(params["content_hash"].startswith("eq."))
        self.assertEqual(patch_request.call_args.kwargs["json"]["status"], "RENDERED")


if __name__ == "__main__":
    unittest.main()
