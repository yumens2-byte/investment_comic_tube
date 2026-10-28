"""2026-10-28 사고 회귀 테스트: 429 후 장중 재시도가 input_context_changed 로 막힘."""
from copy import deepcopy
from datetime import date, datetime
import io
import os
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from scripts import report_pipeline_outcome as report
from src.collector import _build_metric, _last_completed_session
from src.market_facts import validate_provenance
from src.pipeline_control import activate, reset, ControlError
from src.script_checkpoint import pin_market, stable_market
from tests.test_caption_recovery import MemoryControl

NY = ZoneInfo('America/New_York')


def metric(close, prev, observed, prev_observed, collected):
    return {'close': close, 'change_pct': round((close / prev - 1) * 100, 2), 'sma20': None, 'dev_pct': None,
            'source': 'yfinance', 'source_symbol': 'fixture', 'close_raw': str(close),
            'prev_close_raw': str(prev), 'observed_date': observed, 'prev_observed_date': prev_observed,
            'observation_kind': 'latest', 'collected_at_utc': collected}


def snapshot(close, observed, collected):
    return {k: metric(close, 100.0, observed, '2026-10-26', collected)
            for k in ('TNX', 'VIX', 'NASDAQ', 'SPX', 'DXY', 'GOLD', 'OIL')}


class PinMarketTest(unittest.TestCase):
    def setUp(self):
        self.control = MemoryControl()
        self.token = activate(self.control)
        self.addCleanup(reset, self.token)
        blobs = {}
        def request(method, key, data=None):
            if method == 'POST':
                blobs[key] = data
                return b''
            return blobs[key]
        patcher = patch('src.asset_store._request', side_effect=request)
        patcher.start(); self.addCleanup(patcher.stop)
        # 06:26 UTC 첫 시도: 10/27 종가
        self.first = snapshot(101.0, '2026-10-27', '2026-10-28T06:26:40+00:00')
        self.assertIs(pin_market('EDT', self.first), self.first)

    def test_same_snapshot_passes_through(self):
        again = deepcopy(self.first)
        again['TNX']['collected_at_utc'] = '2026-10-28T07:00:00+00:00'
        self.assertIs(pin_market('EDT', again), again)

    def test_intraday_retry_reuses_frozen_close(self):
        # 13:43 UTC 재시도: 진행 중인 10/28 봉
        retry = snapshot(102.5, '2026-10-28', '2026-10-28T13:43:47+00:00')
        with patch('src.market_facts.datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 28, 9, 43, tzinfo=NY)
            clock.fromisoformat = datetime.fromisoformat
            with self.assertLogs('src.script_checkpoint', 'WARNING') as logs:
                pinned = pin_market('EDT', retry)
        self.assertEqual(stable_market(pinned), stable_market(self.first))
        self.assertEqual(pinned['TNX']['collected_at_utc'], '2026-10-28T13:43:47+00:00')
        self.assertIn('market_context_pinned', logs.output[0])

    def test_track_change_still_holds(self):
        with self.assertRaisesRegex(ControlError, 'input_context_changed'):
            pin_market('GOC', snapshot(102.5, '2026-10-28', 'x'))

    def test_stale_frozen_snapshot_still_holds(self):
        with patch('src.market_facts.datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 30, 18, 0, tzinfo=NY)
            clock.fromisoformat = datetime.fromisoformat
            with self.assertRaisesRegex(ControlError, 'input_context_changed'):
                pin_market('EDT', snapshot(103.0, '2026-10-30', 'x'))


class PartialSessionBarTest(unittest.TestCase):
    def hist(self):
        index = pd.DatetimeIndex(pd.bdate_range('2026-09-28', '2026-10-28'), tz=NY)
        return pd.DataFrame({'Close': [100.0 + i for i in range(len(index))]}, index=index)

    def test_completed_session_before_and_after_close(self):
        self.assertEqual(_last_completed_session(datetime(2026, 10, 28, 9, 43, tzinfo=NY)), date(2026, 10, 27))
        self.assertEqual(_last_completed_session(datetime(2026, 10, 28, 16, 30, tzinfo=NY)), date(2026, 10, 27))
        self.assertEqual(_last_completed_session(datetime(2026, 10, 28, 17, 30, tzinfo=NY)), date(2026, 10, 28))

    def test_in_progress_bar_is_dropped(self):
        hist = self.hist()
        result = _build_metric(hist, date(2026, 10, 27))
        self.assertEqual(result['observed_date'], '2026-10-27')
        self.assertEqual(result['close'], round(float(hist['Close'].iloc[-2]), 2))
        self.assertEqual(result['observation_kind'], 'daily_close')

    def test_without_calendar_keeps_legacy_behaviour(self):
        result = _build_metric(self.hist())
        self.assertEqual(result['observed_date'], '2026-10-28')
        self.assertEqual(result['observation_kind'], 'latest')

    def test_filtered_snapshot_passes_provenance_intraday(self):
        hist = self.hist()
        data = {}
        for key in ('TNX', 'VIX', 'NASDAQ', 'SPX', 'DXY', 'GOLD', 'OIL'):
            data[key] = {**_build_metric(hist, date(2026, 10, 27)), 'source_symbol': key}
        validate_provenance(data, now=datetime(2026, 10, 28, 9, 43, tzinfo=NY))


class OutcomeReportTest(unittest.TestCase):
    LOG = (
        "2026-10-28T06:26:48Z WARNING src.story story_generation_aborted reason=quota_exhausted\n"
        "2026-10-28T06:26:48Z ERROR __main__ pipeline_aborted_validation reason=story_fallback_blocked:story:quota_exhausted\n"
        "2026-10-28T13:43:48Z ERROR __main__ pipeline_control_hold reason=input_context_changed details={}\n"
        "2026-10-28T13:43:47Z INFO src.collector market_collection_finished ok=7 data={'TNX': 1}\n"
    )

    def test_annotations_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, 'pipeline.log')
            summary = os.path.join(tmp, 'summary.md')
            with open(log, 'w', encoding='utf-8') as handle:
                handle.write(self.LOG)
            out = io.StringIO()
            with patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': summary}), patch('sys.stdout', out):
                self.assertEqual(report.main(log), 0)
            printed = out.getvalue()
            self.assertIn('::error title=pipeline_control_hold::', printed)
            self.assertIn('reason=input_context_changed', printed)
            self.assertNotIn('market_collection_finished', printed)
            with open(summary, encoding='utf-8') as handle:
                text = handle.read()
            self.assertIn('Gemini 한도', text)

    def test_missing_log_is_not_fatal(self):
        out = io.StringIO()
        with patch('sys.stdout', out):
            self.assertEqual(report.main('does/not/exist.log'), 0)
        self.assertIn('::warning', out.getvalue())


if __name__ == '__main__':
    unittest.main()
