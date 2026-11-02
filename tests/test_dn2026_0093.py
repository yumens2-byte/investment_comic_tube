"""DN2026_0093 회귀 테스트: 내레이션 자연 속도 우선(압축 ≤1.15배, 비트 최대 10초, 회차 연장 예산 6초).

실측 근거: 9-30 이후 예약 실행 로그 segment_audio_compressed(2번 비트 8.3~10.8초 → 1.21~1.58배).
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import renderer
from src.renderer import (
    MAX_SEGMENT_SEC,
    NATURAL_TEMPO_MAX,
    STRETCH_SEGMENT_MAX_SEC,
    STRETCH_TOTAL_BUDGET_SEC,
    TEMPO_GUARD_SEC,
    _stretch_for_natural_tempo,
)


def _tempo(narration, duration):
    return narration / (duration - TEMPO_GUARD_SEC)


class StretchRuleTest(unittest.TestCase):
    def test_pilot_case_10_08s_is_spoken_at_natural_tempo(self):
        # 11-03 파일럿 실측: 10.08초 → 기존 7초·1.472배
        duration, left = _stretch_for_natural_tempo(1, 10.08, MAX_SEGMENT_SEC, STRETCH_TOTAL_BUDGET_SEC)
        self.assertAlmostEqual(_tempo(10.08, duration), NATURAL_TEMPO_MAX, places=3)
        self.assertLess(duration, STRETCH_SEGMENT_MAX_SEC)
        self.assertAlmostEqual(left, STRETCH_TOTAL_BUDGET_SEC - (duration - MAX_SEGMENT_SEC), places=6)

    def test_short_narration_is_untouched(self):
        self.assertEqual(_stretch_for_natural_tempo(2, 5.0, 5.6, 6.0), (5.6, 6.0))
        # 7초 안에 1.15배 이하로 들어가는 경우도 그대로(기존 압축 경로)
        self.assertEqual(_stretch_for_natural_tempo(2, 7.03, 7.0, 6.0), (7.0, 6.0))

    def test_very_long_narration_capped_at_10s(self):
        duration, _ = _stretch_for_natural_tempo(1, 12.0, MAX_SEGMENT_SEC, STRETCH_TOTAL_BUDGET_SEC)
        self.assertEqual(duration, STRETCH_SEGMENT_MAX_SEC)
        self.assertLessEqual(_tempo(12.0, duration), renderer.SEGMENT_ATEMPO_MAX)

    def test_budget_exhaustion_falls_back_to_compression(self):
        duration, left = _stretch_for_natural_tempo(3, 10.84, MAX_SEGMENT_SEC, 0.5)
        self.assertAlmostEqual(duration, MAX_SEGMENT_SEC + 0.5)
        self.assertEqual(left, 0.0)
        self.assertEqual(_stretch_for_natural_tempo(4, 10.84, MAX_SEGMENT_SEC, 0.0), (MAX_SEGMENT_SEC, 0.0))

    def test_worst_case_total_stays_under_publish_limit(self):
        # 훅 최대 8초 + 본편 5비트 7초 + 연장 예산 + 아웃트로 2초 < 60초(content_quality)
        worst = renderer.HOOK_MAX_SEC + 5 * MAX_SEGMENT_SEC + STRETCH_TOTAL_BUDGET_SEC + renderer.OUTRO_DURATION_SEC
        self.assertLess(worst, 60)


class StoryboardIntegrationTest(unittest.TestCase):
    @patch("src.renderer._concat_segments")
    @patch("src.renderer._render_outro_segment", return_value=False)
    @patch("src.renderer._render_segment")
    @patch("src.renderer._render_hook_segment", return_value=3.0)
    def test_only_long_body_beats_are_stretched(self, hook, segment, _outro, _concat):
        lengths = {"h.wav": 9.0, "a1.wav": 10.08, "a2.wav": 7.6, "a3.wav": 4.0}
        scenes = [{"image": "i0", "caption": "훅", "audio": "h.wav", "is_hook": True}] + [
            {"image": f"i{n}", "caption": "자막", "audio": f"a{n}.wav", "is_hook": False} for n in (1, 2, 3)]
        with patch("src.renderer._probe_duration", side_effect=lambda p: lengths[p]), \
             tempfile.TemporaryDirectory() as d:
            renderer._render_storyboard(scenes, str(Path(d) / "out.mp4"), Path(d) / "ff.log")
        durations = [c.kwargs["duration"] for c in segment.call_args_list]
        self.assertAlmostEqual(_tempo(10.08, durations[0]), NATURAL_TEMPO_MAX, places=3)
        self.assertAlmostEqual(durations[1], MAX_SEGMENT_SEC)  # 7.6초: 7초에 1.109배 → 자연 범위, 연장 없음
        self.assertAlmostEqual(durations[2], 4.6)
        hook.assert_called_once()  # 훅은 전용 연출(길이 규칙 별도)


if __name__ == "__main__":
    unittest.main()
