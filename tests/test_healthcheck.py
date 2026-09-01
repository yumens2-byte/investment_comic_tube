import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.healthcheck import (
    EXPECTED_CRON,
    EXPECTED_MODELS,
    EXPECTED_PRIVACY,
    Report,
    check_code_constants,
    check_runtime_health,
    check_workflow_config,
)

GOOD_WORKFLOW = """
on:
  schedule:
    - cron: '0 0 * * *'
env_block:
  GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
  YOUTUBE_CLIENT_ID: ${{ secrets.YOUTUBE_CLIENT_ID }}
  YOUTUBE_CLIENT_SECRET: ${{ secrets.YOUTUBE_CLIENT_SECRET }}
  YOUTUBE_REFRESH_TOKEN: ${{ secrets.YOUTUBE_REFRESH_TOKEN }}
  SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
  SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
  YOUTUBE_DEFAULT_PRIVACY: private
"""


def _run_workflow_check(text: str) -> Report:
    r = Report()
    with patch.object(Path, "exists", return_value=True), \
         patch.object(Path, "read_text", return_value=text):
        check_workflow_config(r)
    return r


class WorkflowConfigCheckTest(unittest.TestCase):
    def test_good_config_passes(self):
        self.assertEqual(_run_workflow_check(GOOD_WORKFLOW).failures, [])

    def test_wrong_cron_is_detected(self):
        r = _run_workflow_check(GOOD_WORKFLOW.replace(EXPECTED_CRON, "0 23 * * 1-5"))
        self.assertTrue(any("cron" in f for f in r.failures))

    def test_public_privacy_is_detected(self):
        r = _run_workflow_check(GOOD_WORKFLOW.replace(EXPECTED_PRIVACY, "public"))
        self.assertTrue(any("privacy" in f for f in r.failures))

    def test_duplicate_cron_is_detected(self):
        text = GOOD_WORKFLOW.replace(
            "    - cron: '0 0 * * *'",
            "    - cron: '0 0 * * *'\n    - cron: '0 12 * * *'",
        )
        r = _run_workflow_check(text)
        self.assertTrue(any("중복 실행" in f for f in r.failures))

    def test_missing_secret_wiring_is_detected(self):
        r = _run_workflow_check(GOOD_WORKFLOW.replace("secrets.SUPABASE_URL", "secrets.WRONG"))
        self.assertTrue(any("workflow_secret_wiring" in f for f in r.failures))

    def test_missing_file_is_detected(self):
        r = Report()
        with patch.object(Path, "exists", return_value=False):
            check_workflow_config(r)
        self.assertTrue(any("workflow_file" in f for f in r.failures))


class CodeConstantCheckTest(unittest.TestCase):
    def test_current_code_matches_expected_models(self):
        r = Report()
        check_code_constants(r)
        self.assertEqual(r.failures, [], f"모델명/계약 불일치: {r.failures}")

    def test_expected_models_cover_all_ai_modules(self):
        self.assertEqual(
            set(EXPECTED_MODELS),
            {"src.story", "src.director", "src.image_generator", "src.tts"},
        )

    def test_stale_model_name_is_detected(self):
        r = Report()
        with patch("src.director.NARRATION_MODEL", "gemini-2.5-flash"):
            check_code_constants(r)
        self.assertTrue(any("NARRATION_MODEL" in f for f in r.failures))

    def test_wrong_episode_base_is_detected(self):
        r = Report()
        with patch.dict("src.drive_manager.DEFAULT_STATE", {"episode": 102}):
            check_code_constants(r)
        self.assertTrue(any("episode_base" in f for f in r.failures))


def _runtime_report(rows):
    r = Report()
    client = MagicMock()
    client.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = (
        MagicMock(data=rows)
    )
    with patch("src.db_client.get_client", return_value=client):
        check_runtime_health(r)
    return r


class RuntimeHealthCheckTest(unittest.TestCase):
    def _row(self, **kw):
        base = {
            "episode_no": 1,
            "status": "published",
            "youtube_video_id": "abc123",
            "degraded_reason": None,
            "updated_at": "2099-01-01T00:00:00+00:00",
            "villain": "Debt Titan",
        }
        base.update(kw)
        return base

    def test_healthy_latest_episode_passes(self):
        self.assertEqual(_runtime_report([self._row()]).failures, [])

    def test_failed_episode_is_detected(self):
        r = _runtime_report([self._row(status="failed", youtube_video_id=None)])
        self.assertTrue(any("runtime_latest" in f for f in r.failures))

    def test_missing_video_id_is_detected(self):
        r = _runtime_report([self._row(status="rendered", youtube_video_id=None)])
        self.assertTrue(any("runtime_latest" in f for f in r.failures))

    def test_degraded_episode_raises_warning_not_failure(self):
        r = _runtime_report([self._row(status="published_degraded", degraded_reason="tts:quota_exhausted")])
        self.assertEqual(r.failures, [])
        self.assertTrue(any("runtime_quality" in w for w in r.warnings))

    def test_stale_episode_is_detected(self):
        r = _runtime_report([self._row(updated_at="2020-01-01T00:00:00+00:00")])
        self.assertTrue(any("runtime_freshness" in f for f in r.failures))

    def test_villain_lock_in_raises_warning(self):
        rows = [self._row(episode_no=n, villain="Debt Titan") for n in range(5, 0, -1)]
        r = _runtime_report(rows)
        self.assertTrue(any("villain_variety" in w for w in r.warnings))

    def test_varied_villains_pass(self):
        villains = ["Debt Titan", "Chaos Reaper", "Bull Brute", "Debt Titan", "Chaos Reaper"]
        rows = [self._row(episode_no=5 - i, villain=v) for i, v in enumerate(villains)]
        r = _runtime_report(rows)
        self.assertFalse(any("villain_variety" in w for w in r.warnings))

    def test_empty_table_warns_but_does_not_fail(self):
        r = _runtime_report([])
        self.assertEqual(r.failures, [])
        self.assertTrue(any("runtime_latest" in w for w in r.warnings))


if __name__ == "__main__":
    unittest.main()
