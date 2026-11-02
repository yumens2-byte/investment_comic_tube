"""DN2026_0092 회귀 테스트: 휴장 스킵·로고 투명 판정·반복 이미지 구도·healthcheck 신선도.

외부 호출 없음(mock). 실측 근거는 운영 DB episodes(Ep.40~54)와 샘플 영상 4편.
"""

import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

import main
from src import renderer
from src.validation import (
    NoNewMarketSession,
    market_session_date,
    validate_new_market_session,
)


def _snap(day):
    return {"SPX": {"close": 1.0, "change_pct": -1.82, "observed_date": day},
            "NASDAQ": {"close": 1.0, "change_pct": -2.86, "observed_date": day}}


class MarketSessionSkipTest(unittest.TestCase):
    def _run(self, current, previous, enforce=True):
        with patch("src.drive_manager.fetch_latest_episode_state",
                   return_value={"market_snapshot": _snap(previous) if previous else None}):
            return validate_new_market_session(_snap(current) if current else {}, enforce=enforce)

    def test_weekend_repeat_of_friday_session_is_skipped(self):
        # Ep.53(11-01)·Ep.54(11-02) 둘 다 10-30 세션이었다
        with self.assertRaises(NoNewMarketSession):
            self._run("2026-10-30", "2026-10-30")

    def test_new_session_passes(self):
        self.assertIsNone(self._run("2026-11-02", "2026-10-30"))

    def test_pilot_records_but_does_not_block(self):
        with self.assertLogs("src.validation", level="WARNING") as logs:
            self.assertIsNone(self._run("2026-10-30", "2026-10-30", enforce=False))
        self.assertIn("market_session_repeat_pilot", "".join(logs.output))

    def test_unknown_session_does_not_block(self):
        self.assertIsNone(self._run(None, "2026-10-30"))
        self.assertIsNone(self._run("2026-10-30", None))

    def test_session_date_prefers_spx_then_nasdaq(self):
        self.assertEqual(market_session_date({"NASDAQ": {"observed_date": "2026-10-29T00:00"}}), "2026-10-29")
        self.assertIsNone(market_session_date({"SPX": {"observed_date": None}}))
        self.assertIsNone(market_session_date(None))


class PipelineSkipTest(unittest.TestCase):
    def tearDown(self):
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)

    @patch("main.record_step_start")
    @patch("main.update_episode")
    @patch("main.synthesize_narrations")
    @patch("main.generate_scene_images")
    @patch("main.generate_connected_script")
    @patch("main.validate_new_market_session", side_effect=NoNewMarketSession("no_new_market_session"))
    @patch("main.fetch_market_data")
    @patch("main.get_youtube_service")
    @patch("main.validate_not_published_today")
    @patch("main.validate_render_environment")
    def test_skip_returns_zero_before_any_paid_or_db_write(self, _env, _dup, _yt, fetch, _session,
                                                            script, images, tts, update, step):
        from tests.test_main_pipeline import MARKET
        fetch.return_value = MARKET
        self.assertEqual(main.main(track="EDT"), 0)
        script.assert_not_called()
        images.assert_not_called()
        tts.assert_not_called()
        update.assert_not_called()
        step.assert_not_called()


class LogoTransparencyTest(unittest.TestCase):
    def test_rgba_but_fully_opaque_is_not_transparent(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "logo.png"
            Image.new("RGBA", (8, 8), (255, 1, 245, 255)).save(path)
            self.assertFalse(renderer._has_alpha_channel(str(path)))

    def test_rgba_with_transparent_pixels_is_transparent(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "logo.png"
            img = Image.new("RGBA", (8, 8), (0, 0, 0, 255))
            img.putpixel((0, 0), (0, 0, 0, 0))
            img.save(path)
            self.assertTrue(renderer._has_alpha_channel(str(path)))

    def test_production_logo_asset_gets_chroma_key(self):
        # 운영 logo.png: RGBA·마젠타 배경 불투명 → 키잉 대상이어야 한다
        self.assertFalse(renderer._has_alpha_channel("assets/brand/logo.png"))


class ReframeTest(unittest.TestCase):
    def test_focus_cycle(self):
        self.assertEqual([renderer._reframe_focus(i) for i in range(4)], ["center", "upper", "lower", "upper"])

    def test_reframe_filter_differs_from_center(self):
        center = renderer._ken_burns_filter(5.0, True)
        upper = renderer._ken_burns_filter(5.0, True, "upper")
        self.assertNotEqual(center, upper)
        self.assertIn("ih*0.3", upper)
        self.assertIn(str(renderer.REFRAME_START_ZOOM), upper)

    @patch("src.renderer._concat_segments")
    @patch("src.renderer._render_outro_segment", return_value=False)
    @patch("src.renderer._render_segment")
    @patch("src.renderer._render_hook_segment", return_value=3.0)
    @patch("src.renderer._probe_duration", return_value=4.0)
    def test_repeated_image_beats_get_new_framing(self, _probe, _hook, segment, _outro, _concat):
        # BEAT_IMAGE_SLOT [0,1,1,2,3,3] 과 같은 배치
        images = ["i0", "i1", "i1", "i2", "i3", "i3"]
        scenes = [{"image": img, "caption": "자막", "audio": "a.wav", "is_hook": i == 0}
                  for i, img in enumerate(images)]
        with tempfile.TemporaryDirectory() as d:
            renderer._render_storyboard(scenes, str(Path(d) / "out.mp4"), Path(d) / "ff.log")
        focuses = [c.kwargs["focus"] for c in segment.call_args_list]
        self.assertEqual(focuses, ["center", "upper", "center", "center", "upper"])


class HealthcheckSessionFreshnessTest(unittest.TestCase):
    def _report(self, row):
        from scripts.healthcheck import Report, check_runtime_health
        r = Report()
        client = MagicMock()
        client.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = (
            MagicMock(data=[row]))
        with patch("src.db_client.get_client", return_value=client):
            check_runtime_health(r)
        return r

    def _row(self, observed, updated="2026-10-31T07:00:00+00:00"):
        return {"episode_no": 53, "status": "published", "youtube_video_id": "v", "degraded_reason": None,
                "updated_at": updated, "villain": "Chaos Reaper", "market_snapshot": _snap(observed)}

    def test_no_new_session_is_ok_even_if_old(self):
        with patch("scripts.healthcheck._new_session_available_at", return_value=None):
            r = self._report(self._row("2026-10-30"))
        self.assertFalse(any("runtime_freshness" in f for f in r.failures))

    def test_new_session_waiting_too_long_fails(self):
        ready = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with patch("scripts.healthcheck._new_session_available_at", return_value=ready):
            r = self._report(self._row("2019-12-30"))
        self.assertTrue(any("runtime_freshness" in f for f in r.failures))

    def test_calendar_after_friday_session(self):
        from scripts.healthcheck import _new_session_available_at
        # 일요일(UTC) 기준 금요일 10-30 이후 새 세션 없음, 화요일 기준 월요일 11-02 세션 사용 가능
        self.assertIsNone(_new_session_available_at("2026-10-30", datetime(2026, 11, 1, 12, tzinfo=timezone.utc)))
        ready = _new_session_available_at("2026-10-30", datetime(2026, 11, 3, 12, tzinfo=timezone.utc))
        self.assertEqual(ready.date().isoformat(), "2026-11-02")



class OutcomeReportTest(unittest.TestCase):
    def test_skip_event_is_reported_as_notice(self):
        from scripts.report_pipeline_outcome import EVENTS, parse
        line = "2026-11-01T07:35:00Z INFO main pipeline_skipped_no_new_session reason=no_new_market_session current=2026-10-30"
        events = parse([line])
        self.assertEqual(events[0]["fields"]["reason"], "no_new_market_session")
        self.assertEqual(EVENTS["pipeline_skipped_no_new_session"][0], "notice")
if __name__ == "__main__":
    unittest.main()
