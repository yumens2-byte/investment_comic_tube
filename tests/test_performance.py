"""DN2026_0092 M1 성과 측정 — 외부 호출 0 (mock 만, STD-TEST-14)."""

import importlib.util
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import performance

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "performance.yml"
RUN_MODE_EXPR = (
    "${{ github.event_name == 'workflow_dispatch' && inputs.mode "
    "|| vars.PERFORMANCE_MODE || 'dry_run' }}"
)


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "collect_performance", ROOT / "scripts" / "collect_performance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _item(vid, views="10", likes="2", comments=None, privacy="public"):
    stats = {"viewCount": views, "likeCount": likes}
    if comments is not None:
        stats["commentCount"] = comments
    return {"id": vid, "statistics": stats, "status": {"privacyStatus": privacy}}


class ResolveModeTest(unittest.TestCase):
    def test_three_values(self):
        for mode in ("dry_run", "shadow", "live"):
            self.assertEqual(performance.resolve_mode(mode), mode)

    def test_case_and_whitespace(self):
        self.assertEqual(performance.resolve_mode("  LIVE "), "live")
        self.assertEqual(performance.resolve_mode("Shadow"), "shadow")

    def test_unknown_empty_none_fall_back(self):
        for raw in ("prod", "", "   ", None):
            with self.assertLogs("src.performance", level="WARNING"):
                self.assertEqual(performance.resolve_mode(raw), "dry_run")


class FetchStatsTest(unittest.TestCase):
    def test_batches_missing_stats_and_absent_ids(self):
        ids = [f"v{i:03d}" for i in range(120)]
        youtube = MagicMock()
        calls = []

        def fake_list(**kwargs):
            calls.append(kwargs)
            batch = kwargs["id"].split(",")
            items = []
            for vid in batch:
                if vid == "v005":
                    continue  # 응답에서 누락
                if vid == "v001":
                    items.append({"id": vid, "statistics": {"viewCount": "7"},
                                  "status": {"privacyStatus": "unlisted"}})
                else:
                    items.append(_item(vid, comments="1"))
            request = MagicMock()
            request.execute.return_value = {"items": items}
            return request

        youtube.videos.return_value.list.side_effect = fake_list
        stats = performance.fetch_stats(youtube, ids)

        self.assertEqual([len(c["id"].split(",")) for c in calls], [50, 50, 20])
        for c in calls:
            self.assertEqual(c["part"], "statistics,status")
            self.assertEqual(c["maxResults"], 50)
        self.assertNotIn("v005", stats)
        self.assertEqual(len(stats), 119)
        self.assertEqual(stats["v001"], {"views": 7, "likes": None, "comments": None,
                                         "privacy_status": "unlisted"})
        self.assertEqual(stats["v002"]["views"], 10)
        self.assertIsInstance(stats["v002"]["likes"], int)

    def test_empty_ids_no_call(self):
        youtube = MagicMock()
        self.assertEqual(performance.fetch_stats(youtube, []), {})
        youtube.videos.return_value.list.assert_not_called()


class BuildRowsTest(unittest.TestCase):
    def test_rows_and_run_mode(self):
        videos = [{"episode_no": 1, "youtube_video_id": "a"},
                  {"episode_no": 2, "youtube_video_id": "b"}]
        stats = {"a": {"views": 5, "likes": 1, "comments": None, "privacy_status": "public"}}
        rows = performance.build_rows(videos, stats, "2026-11-04", "shadow")
        self.assertEqual(rows, [{
            "video_id": "a", "episode_no": 1, "collected_date": "2026-11-04",
            "views": 5, "likes": 1, "comments": None, "privacy_status": "public",
            "run_mode": "shadow",
        }])

    def test_kst_date(self):
        # UTC 2026-11-03 15:41 = KST 2026-11-04 00:41
        fixed = datetime(2026, 11, 3, 15, 41, tzinfo=timezone.utc)

        class FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz)

        with patch.object(performance, "datetime", FakeDatetime):
            self.assertEqual(performance.kst_today(), "2026-11-04")


class SaveAndListTest(unittest.TestCase):
    def test_save_rows_upsert_conflict_key(self):
        client = MagicMock()
        performance.save_rows(client, [{"video_id": "a"}])
        client.table.assert_called_with("video_stats")
        client.table.return_value.upsert.assert_called_once_with(
            [{"video_id": "a"}], on_conflict="video_id,collected_date")

    def test_list_published_videos(self):
        client = MagicMock()
        chain = client.table.return_value.select.return_value.not_.is_.return_value.order.return_value
        chain.execute.return_value.data = [
            {"episode_no": 1, "youtube_video_id": "a"},
            {"episode_no": 2, "youtube_video_id": " "},
            {"episode_no": 3, "youtube_video_id": "a"},
            {"episode_no": 4, "youtube_video_id": "b"},
        ]
        videos = performance.list_published_videos(client)
        client.table.assert_called_with("episodes")
        client.table.return_value.select.return_value.not_.is_.assert_called_with(
            "youtube_video_id", "null")
        self.assertEqual(videos, [{"episode_no": 1, "youtube_video_id": "a"},
                                  {"episode_no": 4, "youtube_video_id": "b"}])


class CollectMainTest(unittest.TestCase):
    def setUp(self):
        self.script = _load_script()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.script.OUTPUT_PATH = Path(self.tmp.name) / "performance_rows.json"
        self.summary = Path(self.tmp.name) / "summary.md"
        self.client = MagicMock()
        self.youtube = MagicMock()
        videos = [{"episode_no": 1, "youtube_video_id": "vid_a"},
                  {"episode_no": 2, "youtube_video_id": "vid_b"}]
        stats = {"vid_a": {"views": 100, "likes": 3, "comments": 1, "privacy_status": "public"}}
        for target, value in (
            ("src.performance.list_published_videos", videos),
            ("src.performance.fetch_stats", stats),
            ("src.performance.kst_today", "2026-11-04"),
        ):
            p = patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("src.db_client.get_client", return_value=self.client)
        p.start()
        self.addCleanup(p.stop)

    def _run(self, mode, youtube="default"):
        yt = self.youtube if youtube == "default" else youtube
        env = {"RUN_MODE": mode, "GITHUB_RUN_ID": "987", "GITHUB_SHA": "abcdef1234567",
               "GITHUB_STEP_SUMMARY": str(self.summary)}
        with patch.dict(os.environ, env), \
                patch("src.publisher.get_youtube_service", return_value=yt), \
                self.assertLogs("collect_performance", level="INFO") as logs:
            rc = self.script.main()
        return rc, logs.output

    def test_dry_run_no_upsert(self):
        rc, output = self._run("dry_run")
        self.assertEqual(rc, 0)
        self.client.table.return_value.upsert.assert_not_called()
        payload = json.loads(self.script.OUTPUT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload["mode"], "dry_run")
        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(payload["missing_episode_nos"], [2])
        summary = self.summary.read_text(encoding="utf-8")
        self.assertIn("행 수: 1", summary)
        self.assertIn("응답 누락 영상 수: 1", summary)
        self.assertIn("합계 조회수: 100", summary)
        self.assertIn("회차 1: 100", summary)
        joined = "\n".join(output) + summary
        self.assertNotIn("vid_a", joined)  # 영상 ID 미노출
        self.assertNotIn("vid_b", joined)

    def test_live_upserts_once(self):
        rc, _ = self._run("live")
        self.assertEqual(rc, 0)
        upsert = self.client.table.return_value.upsert
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.kwargs["on_conflict"], "video_id,collected_date")
        self.assertEqual(upsert.call_args.args[0][0]["run_mode"], "live")

    def test_shadow_reads_only_and_survives_read_failure(self):
        with patch("src.performance.load_previous_views", side_effect=RuntimeError("no table")):
            rc, output = self._run("shadow")
        self.assertEqual(rc, 0)
        self.client.table.return_value.upsert.assert_not_called()
        self.assertTrue(any("shadow_previous_read_failed" in line for line in output))

    def test_shadow_delta(self):
        with patch("src.performance.load_previous_views",
                   return_value=("2026-11-03", {"vid_a": 60})):
            rc, _ = self._run("shadow")
        self.assertEqual(rc, 0)
        self.assertIn("조회수 증감 합계 +40", self.summary.read_text(encoding="utf-8"))

    def test_youtube_none_returns_1(self):
        rc, _ = self._run("live", youtube=None)
        self.assertEqual(rc, 1)
        self.client.table.return_value.upsert.assert_not_called()

    def test_fetch_exception_returns_1(self):
        with patch("src.performance.fetch_stats", side_effect=RuntimeError("boom")):
            rc, _ = self._run("live")
        self.assertEqual(rc, 1)

    def test_start_log_format(self):
        _, output = self._run("LIVE ")
        self.assertTrue(any(
            line.endswith(f"[Performance] v{performance.VERSION} 시작 | mode=live "
                          "| run_id=987 | sha=abcdef1") for line in output), output)

    def test_start_log_local_defaults(self):
        env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_RUN_ID", "GITHUB_SHA")}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                self.script._start_line("dry_run"),
                f"[Performance] v{performance.VERSION} 시작 | mode=dry_run | run_id=local | sha=local")


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_run_mode_standard_expression(self):
        self.assertIn(f"RUN_MODE: {RUN_MODE_EXPR}", self.text)

    def test_cron_timeout_and_choices(self):
        self.assertIn('cron: "41 15 * * *"', self.text)
        self.assertRegex(self.text, r"timeout-minutes:\s*10\b")
        self.assertIn("DN2026_0092", self.text)
        try:
            import yaml
        except ImportError:
            self.assertIn("options: [dry_run, shadow, live]", self.text)
            return
        doc = yaml.safe_load(self.text)
        on = doc.get("on", doc.get(True))
        mode = on["workflow_dispatch"]["inputs"]["mode"]
        self.assertEqual(mode["type"], "choice")
        self.assertEqual(mode["options"], ["dry_run", "shadow", "live"])
        self.assertEqual(mode["default"], "dry_run")
        job = doc["jobs"]["collect"]
        self.assertEqual(job["timeout-minutes"], 10)
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        self.assertEqual(doc["concurrency"]["group"], "edt-performance-collector")
        self.assertFalse(doc["concurrency"]["cancel-in-progress"])
        run_steps = [s.get("run", "") for s in job["steps"]]
        self.assertIn("PYTHONPATH=$(pwd) python scripts/collect_performance.py", run_steps)


class VersionTest(unittest.TestCase):
    def test_version(self):
        self.assertEqual(performance.VERSION, "1.0.0")
        self.assertTrue(re.search(r'VERSION = "1\.0\.0"  # DN2026_0092',
                                  (ROOT / "src" / "performance.py").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
