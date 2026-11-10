"""DN2026_0113: 미장 휴일 진입 스킵 · 예비 예약 게이트 · 자막 측정 결함."""
import os
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.validation import UsMarketHoliday, NoNewMarketSession, us_session_day_for, validate_us_session_day

SEOUL = ZoneInfo("Asia/Seoul")


def _kst(y, m, d, hh=10, mm=17):
    return datetime(y, m, d, hh, mm, tzinfo=SEOUL)


class UsSessionDayGateTest(unittest.TestCase):
    def test_weekday_after_us_session_passes(self):
        validate_us_session_day(_kst(2026, 11, 10))  # 미국 11-09(월)

    def test_kst_saturday_publishes_us_friday(self):
        validate_us_session_day(_kst(2026, 11, 7))  # 미국 11-06(금)

    def test_kst_sunday_and_monday_are_skipped(self):
        for day in (8, 9):  # 미국 토·일
            with self.subTest(day=day), self.assertRaises(UsMarketHoliday):
                validate_us_session_day(_kst(2026, 11, day))

    def test_us_holidays_are_skipped(self):
        for when in (_kst(2026, 11, 27), _kst(2026, 12, 26), _kst(2027, 1, 2)):
            with self.subTest(when=when), self.assertRaises(UsMarketHoliday):
                validate_us_session_day(when)

    def test_early_close_is_a_session(self):
        validate_us_session_day(_kst(2026, 11, 28))  # 미국 11-27 조기 폐장

    def test_delayed_schedule_uses_kst_date(self):
        # 예약 6시간 지연(07:30 UTC = KST 16:30)도 같은 KST 날짜로 판정한다
        delayed = datetime(2026, 11, 10, 7, 30, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(str(us_session_day_for(delayed)[1]), "2026-11-09")
        validate_us_session_day(delayed)

    def test_holiday_is_a_no_new_session_skip(self):
        self.assertTrue(issubclass(UsMarketHoliday, NoNewMarketSession))

    def test_calendar_failure_fails_open(self):
        with patch("exchange_calendars.get_calendar", side_effect=RuntimeError("down")):
            validate_us_session_day(_kst(2026, 11, 9))


class RunTodayGateTest(unittest.TestCase):
    def setUp(self):
        import main
        self.main = main

    def test_holiday_skips_before_pipeline_and_control(self):
        with patch.dict(os.environ, {"PIPELINE_SCHEDULE_ROLE": "primary"}), \
             patch.object(self.main, "validate_us_session_day", side_effect=UsMarketHoliday("us_market_holiday")), \
             patch.object(self.main, "main") as pipeline, \
             patch.object(self.main, "configure_logging"):
            self.assertEqual(self.main.run_today(), 0)
            pipeline.assert_not_called()

    def test_primary_runs_without_backup_check(self):
        with patch.dict(os.environ, {"PIPELINE_SCHEDULE_ROLE": "primary"}), \
             patch.object(self.main, "validate_us_session_day"), \
             patch.object(self.main, "_primary_already_started") as check, \
             patch.object(self.main, "main", return_value=0) as pipeline, \
             patch.object(self.main, "configure_logging"), \
             patch.object(self.main, "select_daily_hero", return_value="EDT"):
            self.assertEqual(self.main.run_today(), 0)
            check.assert_not_called()
            pipeline.assert_called_once_with(track="EDT")

    def _backup(self, started=None, error=None):
        check = {"side_effect": error} if error else {"return_value": started}
        with patch.dict(os.environ, {"PIPELINE_SCHEDULE_ROLE": "backup"}), \
             patch.object(self.main, "validate_us_session_day"), \
             patch.object(self.main, "_primary_already_started", **check), \
             patch.object(self.main, "main", return_value=0) as pipeline, \
             patch.object(self.main, "configure_logging"), \
             patch.object(self.main, "select_daily_hero", return_value="EDT"):
            return self.main.run_today(), pipeline

    def test_backup_skips_when_primary_left_a_trace(self):
        code, pipeline = self._backup(started=True)
        self.assertEqual(code, 0)
        pipeline.assert_not_called()

    def test_backup_runs_when_primary_never_arrived(self):
        code, pipeline = self._backup(started=False)
        self.assertEqual(code, 0)
        pipeline.assert_called_once_with(track="EDT")

    def test_backup_stops_when_trace_cannot_be_checked(self):
        code, pipeline = self._backup(error=RuntimeError("db down"))
        self.assertEqual(code, 1)
        pipeline.assert_not_called()

    def test_primary_trace_uses_publish_then_slot(self):
        with patch("src.drive_manager.has_published_today", return_value=True):
            self.assertTrue(self.main._primary_already_started("EDT"))
        with patch("src.drive_manager.has_published_today", return_value=False), \
             patch("src.pipeline_control.enabled", return_value=True), \
             patch("src.pipeline_control.Control.status", return_value={"version": 2, "exists": True}):
            self.assertTrue(self.main._primary_already_started("EDT"))
        with patch("src.drive_manager.has_published_today", return_value=False), \
             patch("src.pipeline_control.enabled", return_value=True), \
             patch("src.pipeline_control.Control.status", return_value={"version": 2, "exists": False}):
            self.assertFalse(self.main._primary_already_started("EDT"))


class OutcomeReportTest(unittest.TestCase):
    def test_new_events_are_reported(self):
        from scripts import report_pipeline_outcome as report
        lines = [
            "2026-11-09T01:20:00Z INFO __main__ pipeline_skipped_us_market_holiday reason=us_market_holiday",
            "2026-11-10T09:50:00Z INFO __main__ pipeline_skipped_backup_not_needed reason=primary_already_started",
        ]
        events = [e["event"] for e in report.parse(lines)]
        self.assertEqual(events, ["pipeline_skipped_us_market_holiday", "pipeline_skipped_backup_not_needed"])
        self.assertEqual(report.EVENTS["pipeline_skipped_us_market_holiday"][0], "notice")


class ScriptCacheKeyTest(unittest.TestCase):
    def test_layout_revision_is_part_of_script_cache_key(self):
        source = open("main.py", encoding="utf-8").read()
        self.assertIn('"caption_layout_revision": LAYOUT_REVISION', source)


if __name__ == "__main__":
    unittest.main()
