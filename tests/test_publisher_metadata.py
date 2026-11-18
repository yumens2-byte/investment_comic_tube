import os
import tempfile
import unittest
from typing import ClassVar
from unittest.mock import MagicMock, patch

from googleapiclient.errors import HttpError

from src.publisher import (
    BASE_HASHTAGS,
    PLAYLIST_TITLE,
    _build_hashtags,
    _find_playlist,
    _format_market_block,
    _format_story_block,
    _prepare_thumbnail,
    _tag_list,
    add_to_playlist,
    build_description,
    build_social_post,
    build_title,
    set_thumbnail,
)

SNAPSHOT = {
    "SPX": {"close": 7686.14, "change_pct": -0.33},
    "NASDAQ": {"close": 26370.89, "change_pct": -0.12},
    "TNX": {"close": 4.76, "change_pct": 0.81},
    "VIX": {"close": 15.2, "change_pct": 3.4},
    "DXY": {"close": 103.5, "change_pct": 0.1},
}
STORYBOARD = [{"narration": f"문장{i}"} for i in range(6)]
META = {
    "episode": 1, "theme": "긴축", "villain": "Debt Titan",
    "market_snapshot": SNAPSHOT, "storyboard": STORYBOARD,
}


class MarketBlockTest(unittest.TestCase):
    def test_includes_price_and_change(self):
        block = _format_market_block(SNAPSHOT)
        self.assertIn("S&P 500", block)
        self.assertIn("7,686.14", block)
        self.assertIn("-0.33%", block)
        self.assertIn("+0.81%", block)

    def test_skips_indicators_without_close(self):
        block = _format_market_block({"SPX": {"close": None, "change_pct": None}})
        self.assertEqual(block, "")

    def test_none_snapshot_returns_empty(self):
        self.assertEqual(_format_market_block(None), "")

    def test_ignores_non_metric_keys(self):
        block = _format_market_block({**SNAPSHOT, "_villain_scores": {"a": 1}})
        self.assertNotIn("_villain_scores", block)


class StoryBlockTest(unittest.TestCase):
    def test_lists_all_narrations(self):
        block = _format_story_block(STORYBOARD)
        for i in range(6):
            self.assertIn(f"문장{i}", block)

    def test_empty_storyboard_returns_empty(self):
        self.assertEqual(_format_story_block([]), "")
        self.assertEqual(_format_story_block(None), "")

    def test_blank_narrations_are_dropped(self):
        self.assertEqual(_format_story_block([{"narration": "  "}]), "")


class HashtagTest(unittest.TestCase):
    def test_base_tags_always_present(self):
        tags = _build_hashtags("Debt Titan")
        for t in BASE_HASHTAGS:
            self.assertIn(t, tags)

    def test_villain_specific_tags_added(self):
        self.assertIn("#금리", _build_hashtags("Debt Titan"))
        self.assertIn("#변동성", _build_hashtags("Chaos Reaper"))
        self.assertIn("#상승장", _build_hashtags("Bull Brute"))

    def test_no_duplicates(self):
        tags = _build_hashtags("Debt Titan")
        self.assertEqual(len(tags), len(set(tags)))

    def test_unknown_villain_still_returns_base(self):
        self.assertTrue(_build_hashtags("Nobody"))

    def test_market_tags_follow_largest_moves_without_spam(self):
        tags = _build_hashtags("Debt Titan", SNAPSHOT, "긴축 경계")
        self.assertIn("#VIX", tags)
        self.assertIn("#미국채금리", tags)
        self.assertIn("#긴축경계", tags)
        self.assertLessEqual(len(tags), 10)

    def test_tag_list_has_no_hash_symbols(self):
        for tag in _tag_list("Debt Titan"):
            self.assertFalse(tag.startswith("#"))


class DescriptionTest(unittest.TestCase):
    def test_includes_episode_and_theme(self):
        d = build_description(META)
        self.assertIn("EDT Universe Ep.1 · 긴축", d)

    def test_contains_all_sections(self):
        d = build_description(META)
        self.assertIn("오늘의 미국 시장", d)
        self.assertIn("오늘의 한 줄", d)
        self.assertIn("정보 제공 및 교육 목적", d)
        self.assertIn("#Shorts", d)

    def test_within_youtube_limit(self):
        d = build_description(META)
        self.assertLessEqual(len(d), 4900)

    def test_works_without_market_or_story(self):
        d = build_description({"episode": 1, "theme": "t", "villain": "Debt Titan"})
        self.assertIn("EDT Universe Ep.1 · t", d)
        self.assertIn("#Shorts", d)


class PublishingCopyTest(unittest.TestCase):
    def test_title_leads_with_strongest_market_signal(self):
        title = build_title(META)
        self.assertTrue(title.startswith("공포지수 VIX +3.40%"))
        self.assertIn("긴축", title)
        self.assertLessEqual(len(title), 100)

    def test_social_post_is_concise_and_uses_only_three_hashtags(self):
        post = build_social_post(META)
        self.assertIn("문장0", post)
        self.assertIn("핵심 신호: 공포지수 VIX +3.40%", post)
        self.assertLessEqual(len(post), 280)
        self.assertEqual(post.count("#"), 3)

    def test_social_post_respects_custom_length(self):
        metadata = dict(META, storyboard=[{"narration": "긴 문장" * 100}])
        self.assertLessEqual(len(build_social_post(metadata, max_length=120)), 120)


class ThumbnailTest(unittest.TestCase):
    def test_missing_source_returns_none(self):
        self.assertIsNone(_prepare_thumbnail("/nonexistent/img.png"))

    def test_upscales_to_meet_minimum_width(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src.png")
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=768x1376:d=1",
                 "-frames:v", "1", src, "-loglevel", "error"], check=True,
            )
            out = _prepare_thumbnail(src, work_dir=d)
            self.assertIsNotNone(out)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=width",
                 "-of", "csv=p=0", out], capture_output=True, text=True, check=False,
            )
            # YouTube 최소 요건은 가로 1280 이상이다
            self.assertGreaterEqual(int(probe.stdout.strip().split(",")[0]), 1280)

    @patch("src.publisher._prepare_thumbnail", return_value=None)
    def test_set_thumbnail_returns_false_when_prepare_fails(self, _prep):
        self.assertFalse(set_thumbnail(MagicMock(), "vid", "x.png"))

    @patch("src.publisher._prepare_thumbnail", return_value="/tmp/t.jpg")
    @patch("src.publisher.MediaFileUpload")
    def test_http_error_does_not_raise(self, _media, _prep):
        youtube = MagicMock()
        youtube.thumbnails.return_value.set.return_value.execute.side_effect = HttpError(
            MagicMock(status=403), b"forbidden"
        )
        # 채널 미인증/스코프 부족이어도 발행은 계속돼야 한다
        self.assertFalse(set_thumbnail(youtube, "vid", "x.png"))


class PlaylistTest(unittest.TestCase):
    def _youtube_with_playlists(self, items):
        youtube = MagicMock()
        youtube.playlists.return_value.list.return_value.execute.return_value = {
            "items": items, "nextPageToken": None,
        }
        return youtube

    def test_finds_existing_playlist_by_title(self):
        youtube = self._youtube_with_playlists(
            [{"id": "PL123", "snippet": {"title": PLAYLIST_TITLE}}]
        )
        self.assertEqual(_find_playlist(youtube, PLAYLIST_TITLE), "PL123")

    def test_returns_none_when_not_found(self):
        youtube = self._youtube_with_playlists(
            [{"id": "PLX", "snippet": {"title": "다른목록"}}]
        )
        self.assertIsNone(_find_playlist(youtube, PLAYLIST_TITLE))

    def test_creates_playlist_when_missing(self):
        youtube = self._youtube_with_playlists([])
        youtube.playlists.return_value.insert.return_value.execute.return_value = {"id": "PLNEW"}

        result = add_to_playlist(youtube, "vid", PLAYLIST_TITLE)

        self.assertEqual(result, "PLNEW")
        youtube.playlists.return_value.insert.assert_called_once()
        youtube.playlistItems.return_value.insert.assert_called_once()

    def test_reuses_existing_playlist(self):
        youtube = self._youtube_with_playlists(
            [{"id": "PL123", "snippet": {"title": PLAYLIST_TITLE}}]
        )
        result = add_to_playlist(youtube, "vid", PLAYLIST_TITLE)

        self.assertEqual(result, "PL123")
        youtube.playlists.return_value.insert.assert_not_called()

    def test_scope_error_does_not_raise(self):
        youtube = MagicMock()
        youtube.playlists.return_value.list.return_value.execute.side_effect = HttpError(
            MagicMock(status=403), b"insufficient scope"
        )
        # 스코프 부족이어도 발행은 계속돼야 한다
        self.assertIsNone(add_to_playlist(youtube, "vid", PLAYLIST_TITLE))

    def test_default_title_matches_requested_name(self):
        self.assertEqual(PLAYLIST_TITLE, "EDT_UNIVERSE_INVEST_AREA99")


class ScopeTest(unittest.TestCase):
    def test_force_ssl_scope_present_for_playlist_support(self):
        from src.publisher import SCOPES

        # playlistItems.insert 는 youtube.upload 로는 403 이 난다
        self.assertTrue(any("force-ssl" in s for s in SCOPES))

    def test_token_script_scopes_match_publisher(self):
        import re
        from pathlib import Path

        from src.publisher import SCOPES

        text = Path("scripts/issue_youtube_token.py").read_text(encoding="utf-8")
        for scope in SCOPES:
            self.assertIn(scope, text, f"발급 스크립트에 {scope} 누락")
        self.assertTrue(re.search(r"SCOPES\s*=", text))


if __name__ == "__main__":
    unittest.main()


class RefreshScopeRegressionTest(unittest.TestCase):
    """2026-09-02 사고 회귀 방지.

    확장된 SCOPES 를 갱신 요청에 실어 보내면, 아직 upload 스코프로만 발급된
    토큰에서 invalid_scope 가 나 파이프라인 전체가 죽는다.
    """

    ENV: ClassVar[dict] = {
        "YOUTUBE_CLIENT_ID": "cid",
        "YOUTUBE_CLIENT_SECRET": "csec",
        "YOUTUBE_REFRESH_TOKEN": "rtok",
    }

    @patch.dict("os.environ", ENV, clear=True)
    @patch("src.publisher.build")
    @patch("src.publisher.Credentials")
    def test_scopes_not_passed_to_credentials(self, creds_cls, _build):
        from src.publisher import get_youtube_service

        get_youtube_service()

        kwargs = creds_cls.call_args.kwargs
        # scopes 를 넘기면 토큰이 가진 스코프와 불일치할 때 갱신이 거부된다
        self.assertNotIn("scopes", kwargs)
        self.assertEqual(kwargs["refresh_token"], "rtok")

    @patch.dict("os.environ", ENV, clear=True)
    @patch("src.publisher.build")
    @patch("src.publisher.Credentials")
    def test_refresh_error_message_includes_actual_reason(self, creds_cls, _build):
        from google.auth.exceptions import RefreshError

        from src.publisher import YouTubeAuthenticationError, get_youtube_service

        creds_cls.return_value.refresh.side_effect = RefreshError("invalid_scope: Bad Request")

        with self.assertLogs("src.publisher", level="ERROR") as captured, \
             self.assertRaises(YouTubeAuthenticationError):
            get_youtube_service()

        # 사유를 invalid_grant 로 고정하면 원인을 오진한다
        joined = "\n".join(captured.output)
        self.assertIn("invalid_scope", joined)
        self.assertIn("action=inspect_oauth_configuration", joined)
        self.assertNotIn("interactive_reauthorization_required", joined)

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_credentials_returns_none_without_raising(self):
        from src.publisher import get_youtube_service

        self.assertIsNone(get_youtube_service())


class TestDn20260117PostUploadAndMasking(unittest.TestCase):
    """DN2026_0117 — skip 옵션(기본 False=일간 라인 불변)·로그 식별자 마스킹."""

    @patch("src.publisher.add_to_playlist")
    @patch("src.publisher.set_thumbnail")
    def test_default_calls_thumbnail_and_playlist(self, thumb, playlist):
        from src.publisher import _post_upload
        _post_upload(MagicMock(), "VID12345", {"thumbnail_source": "t.png"})
        thumb.assert_called_once()
        playlist.assert_called_once()

    @patch("src.publisher.add_to_playlist")
    @patch("src.publisher.set_thumbnail")
    def test_skip_flags_suppress_calls(self, thumb, playlist):
        from src.publisher import _post_upload
        with self.assertLogs("src.publisher", level="INFO") as logs:
            _post_upload(MagicMock(), "VID12345", {"skip_thumbnail": True, "skip_playlist": True})
        thumb.assert_not_called()
        playlist.assert_not_called()
        joined = "\n".join(logs.output)
        self.assertIn("thumbnail_skipped video_id=VID***", joined)
        self.assertNotIn("VID12345", joined)

    def test_mask_and_redact(self):
        from src.publisher import _mask_id, _redact
        self.assertEqual(_mask_id("ABCDEFGHIJK"), "ABC***")
        self.assertEqual(_mask_id(None), "-")
        self.assertEqual(_redact("GET /thumbnails/set?videoId=ABCDEFGHIJK&alt=json", "ABCDEFGHIJK"),
                         "GET /thumbnails/set?videoId=ABC***&alt=json")

    @patch("src.publisher._prepare_thumbnail", return_value="/tmp/t.jpg")
    @patch("src.publisher.MediaFileUpload")
    def test_thumbnail_failure_log_masks_id_in_error(self, _media, _prep):
        resp = MagicMock(status=403, reason="forbidden")
        youtube = MagicMock()
        youtube.thumbnails.return_value.set.return_value.execute.side_effect = HttpError(
            resp, b'{"error": "x"}', uri="https://youtube.googleapis.com/upload/youtube/v3/thumbnails/set?videoId=ABCDEFGHIJK")
        with self.assertLogs("src.publisher", level="WARNING") as logs:
            self.assertFalse(set_thumbnail(youtube, "ABCDEFGHIJK", "x.png"))
        joined = "\n".join(logs.output)
        self.assertNotIn("ABCDEFGHIJK", joined)
        self.assertIn("ABC***", joined)


    def test_playlist_failure_log_masks_playlist_id(self):
        resp = MagicMock(status=403, reason="forbidden")
        youtube = MagicMock()
        youtube.playlists.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "PLabcdef123456", "snippet": {"title": PLAYLIST_TITLE}}]}
        youtube.playlistItems.return_value.insert.return_value.execute.side_effect = HttpError(
            resp, b'{}', uri="https://x/playlistItems?playlistId=PLabcdef123456&videoId=ABCDEFGHIJK")
        with self.assertLogs("src.publisher", level="WARNING") as logs:
            self.assertIsNone(add_to_playlist(youtube, "ABCDEFGHIJK"))
        joined = "\n".join(logs.output)
        self.assertNotIn("PLabcdef123456", joined)
        self.assertNotIn("ABCDEFGHIJK", joined)

    def test_redact_ignores_short_and_prefers_longest(self):
        from src.publisher import _redact
        self.assertEqual(_redact("x a-b abc", "a"), "x a-b abc")
        self.assertEqual(_redact("id=ABCDEFGHIJKLMN", "ABCDEFG", "ABCDEFGHIJKLMN"), "id=ABC***")

    @patch("src.publisher.add_to_playlist")
    @patch("src.publisher.set_thumbnail")
    def test_string_false_does_not_skip(self, thumb, playlist):
        from src.publisher import _post_upload
        _post_upload(MagicMock(), "VID12345", {"skip_thumbnail": "false", "skip_playlist": "false"})
        thumb.assert_called_once()
        playlist.assert_called_once()


class TestDn20260118LogMask(unittest.TestCase):
    """DN2026_0118 — 공용 마스킹 유틸과 적용 지점."""

    def test_shared_util_matches_publisher_alias(self):
        from src import log_mask, publisher
        self.assertIs(publisher._mask_id, log_mask.mask_id)
        self.assertEqual(log_mask.mask_id("ABCDEFGHIJK"), "ABC***")

    def test_report_outcome_masks_video_id_field(self):
        import importlib.util, os
        spec = importlib.util.spec_from_file_location(
            "rpo", os.path.join(os.path.dirname(__file__), "..", "scripts", "report_pipeline_outcome.py"))
        rpo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rpo)
        self.assertEqual(rpo.mask_id("ABCDEFGHIJK"), "ABC***")
        line = "2026-11-21T10:00:00 INFO __main__ pipeline_finished video_id={} status=published"
        for raw, want in (("ABCDEFGHIJK", "ABC***"), ("not_uploaded", "not_uploaded"), ("ABC***", "ABC***"),
                          ("-", "-")):
            events = rpo.parse([line.format(raw)])
            self.assertEqual(events[0]["fields"]["video_id"], want)

    def test_drive_manager_logs_mask_episode_id(self):
        from unittest.mock import patch
        from src import drive_manager
        client = MagicMock()
        client.table.return_value.update.return_value.eq.return_value.select.return_value.execute.return_value.data = [
            {"id": "ep-0058-3be38db3"}]
        with patch.object(drive_manager, "get_client", return_value=client), \
                self.assertLogs("src.drive_manager", level="INFO") as logs:
            drive_manager.update_episode("ep-0058-3be38db3", status="published")
        joined = "\n".join(logs.output)
        self.assertNotIn("ep-0058-3be38db3", joined)
        self.assertIn("ep-***", joined)
