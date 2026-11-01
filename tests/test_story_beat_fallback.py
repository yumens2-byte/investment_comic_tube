"""DN2026_0089: 2026-10-30/31 예약 실행 발행 차단 재현 및 회귀 테스트.

배경: run 41018143256(10-30)·41178817392(10-31) 모두 2번 문장만 1·2차 모두
caption_over_3_lines(legacy 19자×3줄)로 탈락 → story_fact_gate_failed → 발행 차단.
REJECTED_1030/1031(2번 문장)과 SNAP 값은 두 실행의 pipeline.log 실측이다(STD-CI-11).
1·3~6번(BODY)은 로그에 남지 않아(탈락 문장만 기록) 합성 문장이다.
"""
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from src.market_facts import check_line, resolve
from src.pipeline_control import activate, reset
from src.story import BEAT_COUNT, _generate_narrations, rule_comparison_line
from src.renderer import _wrap_korean

# 실측 시세(두 실행의 market_collection_finished 로그 값)
SNAP_1030 = {
    "TNX": {"close": 4.99, "change_pct": -1.11}, "VIX": {"close": 17.2, "change_pct": 1.71},
    "NASDAQ": {"close": 26184.72, "change_pct": -1.38}, "SPX": {"close": 7720.39, "change_pct": -0.3},
    "DXY": {"close": 102.04, "change_pct": -0.15}, "GOLD": {"close": 4031.4, "change_pct": -0.5},
    "OIL": {"close": 102.0, "change_pct": -0.76},
}
SNAP_1031 = {
    "TNX": {"close": 4.93, "change_pct": -1.2}, "VIX": {"close": 19.42, "change_pct": 12.91},
    "NASDAQ": {"close": 25437.0, "change_pct": -2.86}, "SPX": {"close": 7580.07, "change_pct": -1.82},
    "DXY": {"close": 101.88, "change_pct": -0.16}, "GOLD": {"close": 3898.9, "change_pct": -3.29},
    "OIL": {"close": 104.12, "change_pct": 2.08},
}
REJECTED_1030 = ("Chaos Reaper는 {{FACT:TNX.change_pct}}에도 {{FACT:VIX.change_pct}} 충격을 던졌습니다.",
                 "Chaos Reaper가 {{FACT:TNX.change_pct}}에도 {{FACT:VIX.change_pct}} 폭발시켰습니다.")
REJECTED_1031 = ("{{FACT:TNX.change_pct}}에도 {{FACT:VIX.change_pct}}로 Chaos Reaper가 폭주합니다.",
                 "{{FACT:TNX.change_pct}}에도 {{FACT:VIX.change_pct}}하며 Chaos Reaper가 덮칩니다.")
BODY = [
    "카오스리퍼가 시장을 뒤흔든다",
    None,  # 2번은 실측 탈락 문장으로 채운다
    "금리 부담이 커지자 하락장의 그림자가 짙어진다",
    "EDT가 체인소를 들고 투자자의 구원자로 선다",
    "EDT는 추격 대신 비중을 줄여 분산을 지킨다",
    "공포가 걷히면 다음 방어선은 어디일까",
]


def _lines(beat2):
    lines = list(BODY)
    lines[1] = beat2
    return lines


@patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=True)
@patch("google.genai.Client")
class IncidentReproductionTest(unittest.TestCase):
    def setUp(self):
        for target, options in [("src.script_checkpoint.read_json", {"return_value": None}),
                                ("src.script_checkpoint.write_json", {})]:
            patcher = patch(target, **options)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.token = activate(Mock())
        self.addCleanup(reset, self.token)

    def _respond(self, client_cls, *responses):
        client_cls.return_value.models.generate_content.side_effect = [
            MagicMock(text=json.dumps(lines, ensure_ascii=False)) for lines in responses]
        return client_cls.return_value.models.generate_content

    def _run(self, client_cls, snapshot, rejected):
        call = self._respond(client_cls, _lines(rejected[0]), _lines(rejected[1]))
        lines, degraded = _generate_narrations("Chaos Reaper", "변동성", snapshot, hook_type="A")
        self.assertEqual(call.call_count, 2)  # 추가 유료 호출 없음
        return lines, degraded

    def test_1030_incident_publishes_with_rule_beat2(self, client_cls):
        lines, degraded = self._run(client_cls, SNAP_1030, REJECTED_1030)
        self.assertIsNone(degraded)
        self.assertEqual(lines[1], "미 10년물 금리 1.11% 하락, VIX 1.71% 상승. 카오스리퍼가 시장을 흔든다")
        self.assertEqual(lines[2], BODY[2])

    def test_1031_incident_publishes_with_rule_beat2(self, client_cls):
        lines, degraded = self._run(client_cls, SNAP_1031, REJECTED_1031)
        self.assertIsNone(degraded)
        self.assertIn("VIX 12.91% 상승", lines[1])
        _wrap_korean(lines[1], 19, max_lines=3)  # legacy 한도 안

    def test_other_body_beats_still_block(self, client_cls):
        bad = _lines(REJECTED_1030[0])
        bad[2] = "나스닥이 세 배 흔들린다"
        self._respond(client_cls, bad, bad)
        lines, degraded = _generate_narrations("Chaos Reaper", "변동성", SNAP_1030, hook_type="A")
        self.assertEqual(degraded, "story:fact_gate_rejected")
        self.assertEqual(len(lines), BEAT_COUNT)

    def test_hook_and_beat2_are_both_replaced(self, client_cls):
        bad = _lines(REJECTED_1030[0])
        bad[0] = "짧다"
        retry = _lines(REJECTED_1030[1])
        retry[0] = "짧다"
        self._respond(client_cls, bad, retry)
        lines, degraded = _generate_narrations("Chaos Reaper", "변동성", SNAP_1030, hook_type="A")
        self.assertIsNone(degraded)
        self.assertTrue(lines[1].startswith("미 10년물 금리 1.11% 하락, VIX 1.71% 상승"))
        self.assertEqual(lines[2:], BODY[2:])

    def test_rule_line_rejected_when_facts_missing_blocks(self, client_cls):
        # 운영에서는 VIX 가 필수 지표라 앞 단계에서 막히지만, 규칙 문장 경로 자체의 차단을 확인한다
        from src.story import _generate_narrations_safe
        snap = dict(SNAP_1030, VIX={"close": None, "change_pct": None})
        self._respond(client_cls, _lines(REJECTED_1030[0]), _lines(REJECTED_1030[1]))
        with self.assertLogs("src.story", level="WARNING") as logs:
            _lines_out, degraded = _generate_narrations_safe(
                client_cls.return_value, "prompt", "Chaos Reaper", "A", snap, ["x"] * BEAT_COUNT)
        self.assertEqual(degraded, "story:fact_gate_rejected")
        self.assertTrue(any("story_line_rule_fallback_rejected beat=2 reason=facts_missing" in m
                            for m in logs.output))

    def test_adaptive_policy_keeps_model_line_with_fixed_particle(self, client_cls):
        from src import caption_policy
        if not caption_policy.FONT_PATH.is_file():
            self.skipTest("install pinned caption font for layout tests")
        call = self._respond(client_cls, _lines(REJECTED_1031[0]))
        with patch.dict("os.environ", {"CAPTION_LAYOUT_POLICY": "caption-layout-v1"}):
            lines, degraded = _generate_narrations("Chaos Reaper", "변동성", SNAP_1031, hook_type="A")
        self.assertIsNone(degraded)
        self.assertEqual(call.call_count, 1)  # adaptive 에서는 1차 응답이 통과한다
        self.assertIn("VIX 12.91% 상승으로 Chaos Reaper가", lines[1])

    def test_rule_line_passes_adaptive_layout_for_extremes(self, client_cls):
        from src import caption_policy
        if not caption_policy.FONT_PATH.is_file():
            self.skipTest("install pinned caption font for layout tests")
        from src.story import _check_safe_line
        with patch.dict("os.environ", {"CAPTION_LAYOUT_POLICY": "caption-layout-v1"}):
            for tnx in (-99.99, 0.0, 150.25):
                for vix in (-99.99, 12.91, 999.99):
                    snap = {"TNX": {"close": 4.1, "change_pct": tnx}, "VIX": {"close": 18.4, "change_pct": vix}}
                    line, reason = _check_safe_line(1, rule_comparison_line("Chaos Reaper", snap), "A",
                                                    snap, "Chaos Reaper")
                    self.assertIsNone(reason, (tnx, vix))

    def test_malformed_twice_still_blocks(self, client_cls):
        client_cls.return_value.models.generate_content.side_effect = [
            MagicMock(text="쓰레기"), MagicMock(text="또 쓰레기")]
        _lines_out, degraded = _generate_narrations("Chaos Reaper", "변동성", SNAP_1030, hook_type="A")
        self.assertEqual(degraded, "story:fact_gate_rejected")


class RuleComparisonLineTest(unittest.TestCase):
    EXTREMES = (-0.01, 0.0, -12.34, 99.99, 150.25, -99.99)

    def test_rule_line_fits_legacy_and_passes_fact_rules_for_extremes(self):
        for villain in ("Debt Titan", "Chaos Reaper", "Bull Brute", "Unknown"):
            for tnx in self.EXTREMES:
                for vix in self.EXTREMES:
                    snap = {"TNX": {"close": 4.1, "change_pct": tnx}, "VIX": {"close": 18.4, "change_pct": vix}}
                    raw = rule_comparison_line(villain, snap)
                    self.assertIsNone(check_line(raw), raw)
                    line = resolve(raw, snap)
                    _wrap_korean(line, 19, max_lines=3)

    def test_falls_back_to_close_tokens_when_change_missing(self):
        snap = {"TNX": {"close": 4.1, "change_pct": None}, "VIX": {"close": 18.4, "change_pct": None}}
        self.assertEqual(resolve(rule_comparison_line("Debt Titan", snap), snap),
                         "미 10년물 금리 4.10%, VIX 18.40. 뎁트타이탄이 시장을 흔든다")

    def test_no_line_when_required_facts_missing(self):
        self.assertIsNone(rule_comparison_line("Debt Titan", {"TNX": {}, "VIX": {}}))


class ParticleAfterFactTest(unittest.TestCase):
    def test_incident_particles_are_corrected(self):
        self.assertIn("VIX 12.91% 상승으로 Chaos", resolve(REJECTED_1031[0], SNAP_1031))
        snap = {"VIX": {"change_pct": 1.71}, "TNX": {"change_pct": 0.5}}
        self.assertEqual(resolve("{{FACT:VIX.change_pct}}를 키웠다", snap), "VIX 1.71% 상승을 키웠다")

    def test_reading_based_final_sound(self):
        cases = {
            "{{FACT:VIX.close}}가 경고한다": "VIX 18.40이 경고한다",       # 영(ㅇ)
            "{{FACT:DXY.close}}가 버틴다": "달러인덱스 99.12가 버틴다",     # 이
            "{{FACT:TNX.close}}로 치솟았다": "미 10년물 금리 4.17%로 치솟았다",  # 퍼센트
            "{{FACT:SPX.change_pct}}은 작다": "S&P 500 0.30% 하락은 작다",
            "{{FACT:GOLD.close}}이 무너졌다": "금 선물 4031.40달러가 무너졌다",
            "{{FACT:NASDAQ.close}}와 맞선다": "나스닥 26184.71과 맞선다",    # 일(ㄹ) + 과
            "{{FACT:NASDAQ.close}}으로 밀렸다": "나스닥 26184.71로 밀렸다",  # ㄹ받침은 로
        }
        snap = {"VIX": {"close": 18.4}, "DXY": {"close": 99.12}, "TNX": {"close": 4.17},
                "SPX": {"change_pct": -0.3}, "GOLD": {"close": 4031.4}, "NASDAQ": {"close": 26184.71}}
        for raw, expected in cases.items():
            self.assertEqual(resolve(raw, snap), expected)

    def test_non_particle_suffix_is_untouched(self):
        snap = {"VIX": {"change_pct": 1.71}}
        self.assertEqual(resolve("{{FACT:VIX.change_pct}}에도 버틴다", snap), "VIX 1.71% 상승에도 버틴다")
        self.assertEqual(resolve("{{FACT:VIX.change_pct}}이었다", snap), "VIX 1.71% 상승이었다")


class VillainNameLayoutTest(unittest.TestCase):
    def test_villain_name_is_not_split_across_lines(self):
        from src import caption_policy
        if not caption_policy.FONT_PATH.is_file():
            self.skipTest("install pinned caption font for layout tests")
        from src.story import caption_protected
        from src.caption_layout import build_layout
        line = resolve(REJECTED_1031[0], SNAP_1031)
        layout = build_layout(line, caption_protected(SNAP_1031, line, "Chaos Reaper"))
        self.assertTrue(any("Chaos Reaper" in row for row in layout["lines"]), layout["lines"])


class OutcomeAnnotationTest(unittest.TestCase):
    def _report(self, text):
        from scripts.report_pipeline_outcome import main
        with TemporaryDirectory() as directory:
            log = Path(directory) / "pipeline.log"
            log.write_text(text, encoding="utf-8")
            summary = Path(directory) / "summary.md"
            out = io.StringIO()
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}), redirect_stdout(out):
                main(str(log))
            return out.getvalue(), summary.read_text(encoding="utf-8")

    def test_incident_log_surfaces_beat_and_reason_without_line_text(self):
        log = (
            "2026-10-31T07:01:27Z WARNING src.story story_line_rejected beat=2 reason=caption_over_3_lines "
            "line={{FACT:TNX.change_pct}}에도 {{FACT:VIX.change_pct}}로 Chaos Reaper가 폭주합니다.\n"
            "2026-10-31T07:01:49Z WARNING src.story story_fact_gate_failed beats=[2, 3]\n"
            "2026-10-31T07:01:49Z ERROR src.director story_fallback_blocked reason=story:fact_gate_rejected\n")
        annotations, summary = self._report(log)
        self.assertIn("title=story_line_rejected", annotations)
        self.assertIn("beat=2 reason=caption_over_3_lines", annotations)
        self.assertIn("beats=[2, 3]", annotations)
        self.assertNotIn("폭주", annotations + summary)
        self.assertNotIn("FACT", annotations + summary)
        self.assertIn("자막", summary)  # 조치 안내

    def test_line_text_cannot_override_fields(self):
        annotations, summary = self._report(
            "2026-11-02T01:00:00Z WARNING src.story story_line_rejected beat=2 reason=caption_over_3_lines "
            "line=가짜 reason=fake beat=9 type=[x y] 문장\n")
        self.assertIn("beat=2 reason=caption_over_3_lines", annotations)
        for leaked in ("fake", "beat=9", "가짜", "[x y]"):
            self.assertNotIn(leaked, annotations + summary)

    def test_caption_hint_only_when_gate_finally_failed(self):
        recovered = ("2026-11-02T01:00:00Z WARNING src.story story_line_rejected beat=2 reason=caption_over_3_lines line=x\n"
                     "2026-11-02T01:00:01Z WARNING src.story story_line_rule_fallback_applied beat=2 reason=caption_over_3_lines\n")
        _, summary = self._report(recovered)
        self.assertNotIn("자막 한도", summary)

    def test_rule_fallback_is_reported(self):
        annotations, _ = self._report(
            "2026-11-02T01:20:00Z WARNING src.story story_line_rule_fallback_applied beat=2 reason=caption_over_3_lines\n")
        self.assertIn("::warning title=story_line_rule_fallback_applied", annotations)


if __name__ == "__main__":
    unittest.main()
