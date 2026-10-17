"""Incident fixtures, actual FFmpeg text geometry, and durable crash recovery."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src import caption_policy as policy
from src.caption_layout import build_layout, canonical, validate_layout, verify_mask, LayoutError
from src.market_facts import fact, resolve
from src.pipeline_control import activate, reset, ControlError, Control
from src.script_checkpoint import response, freeze_context, stable_market
from src.renderer import _wrap_korean, _render_segment

SNAPSHOT = {'TNX': {'close': 5.1, 'change_pct': .18},
            'VIX': {'close': 19.99, 'change_pct': 21.45},
            'NASDAQ': {'close': 26140.03, 'change_pct': -2.15}}
INCIDENT = 'Chaos Reaper가 {{FACT:TNX.change_pct}} 대비 {{FACT:VIX.change_pct}}를 폭발시켰다.'


class MemoryControl:
    slot = 'fixture/2026-10-17/production'
    def __init__(self):
        self.records = {}
        self.count = 0
        self.maximum = 2
    def asset(self, key, value=None):
        if value is not None:
            self.records[key] = deepcopy(value)
        return deepcopy(self.records.get(key, {}))
    def reserve(self, *args):
        if self.count >= self.maximum:
            raise ControlError('generation_limit_reached')
        self.count += 1


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        self.control = MemoryControl()
        self.token = activate(self.control)
        self.addCleanup(reset, self.token)
        self.blobs = {}
        def request(method, key, data=None):
            if method == 'POST':
                self.blobs[key] = data
                return b''
            return self.blobs[key]
        patcher = patch('src.asset_store._request', side_effect=request)
        patcher.start(); self.addCleanup(patcher.stop)
        freeze_context('generation', {'market': SNAPSHOT, 'previous': 44})
        self.generate = Mock(return_value=Mock(text='malformed but recoverable'))

    def test_response_is_cached_before_parsing_and_survives_exhaustion(self):
        first = response(Mock(), self.generate, 'storyboard', 1, model='m', contents='old')
        self.control.count = self.control.maximum
        restored = response(Mock(), self.generate, 'storyboard', 1, model='m2', contents='new-policy')
        self.assertEqual(first.text, restored.text)
        self.assertEqual(self.generate.call_count, 1)

    def test_checkpoint_context_change_never_calls_model(self):
        with self.assertRaisesRegex(ControlError, 'input_context_changed'):
            freeze_context('generation', {'market': {'changed': 1}, 'previous': 44})
        self.generate.assert_not_called()

    def test_cache_checksum_mismatch_is_not_regenerated(self):
        response(Mock(), self.generate, 'storyboard', 1, model='m', contents='p')
        record = self.control.records['text-response:storyboard-1']
        self.blobs[record['key']] = b'changed'
        with self.assertRaisesRegex(ControlError, 'checksum_mismatch'):
            response(Mock(), self.generate, 'storyboard', 1, model='m', contents='p')
        self.assertEqual(self.generate.call_count, 1)

    def test_storage_failure_stops_before_repair(self):
        with patch('src.script_checkpoint.write_json', side_effect=ControlError('storage')):
            with self.assertRaisesRegex(ControlError, 'storage'):
                response(Mock(), self.generate, 'storyboard', 1, model='m', contents='p')
        self.assertEqual(self.generate.call_count, 1)

    def test_raw_responses_have_distinct_ordinals(self):
        response(Mock(), self.generate, 'storyboard', 1, model='m', contents='first')
        self.generate.return_value.text = 'fixed'
        response(Mock(), self.generate, 'storyboard', 2, model='m', contents='repair')
        self.assertIn('text-response:storyboard-1', self.control.records)
        self.assertIn('text-response:storyboard-2', self.control.records)

    @patch.dict(os.environ, {'GEMINI_API_KEY': 'fixture', 'CAPTION_LAYOUT_POLICY': policy.ADAPTIVE})
    def test_actual_safe_story_uses_one_reservation_and_recovers_without_api(self):
        from src.story import _generate_narrations
        from src.pipeline_control import generate
        raw = ['공포의 파도가 시장을 덮친다', INCIDENT,
               '변동성이 커지며 방어선이 흔들린다', 'EDT가 체인소로 위협을 막는다',
               '추격을 멈추고 비중과 손실 한도를 점검한다', '다음 시장 신호가 방어선을 시험한다']
        client = Mock(); client.models.generate_content.return_value = Mock(text=json.dumps(raw, ensure_ascii=False))
        with patch('src.story.make_client', return_value=client):
            first = _generate_narrations('Chaos Reaper', '변동성', SNAPSHOT, hook_type='A')
            self.control.maximum = self.control.count
            second = _generate_narrations('Chaos Reaper', '변동성', SNAPSHOT, hook_type='A')
        self.assertIsNone(first[1])
        self.assertEqual(first, second)
        self.assertEqual(self.control.count, 1)
        client.models.generate_content.assert_called_once()

    def test_safe_director_does_not_call_polish(self):
        from src.director import generate_connected_script
        storyboard = [{'narration': '검증된 첫 문장'}] * 6
        with patch('src.director.fetch_latest_episode_state', return_value={'episode': 44}), \
             patch('src.director.fetch_recent_cliffhangers', return_value=[]), \
             patch('src.script_checkpoint.freeze_context'), \
             patch('src.director.build_storyboard', return_value=(storyboard, {'villain_streak': 1}, None)), \
             patch('src.director._polish_narration') as polish:
            script = generate_connected_script(SNAPSHOT, persist=False)
        polish.assert_not_called()
        self.assertEqual(script['narration'], '검증된 첫 문장')

    def test_stable_market_keeps_derivatives_but_not_collection_clock(self):
        a = {'TNX': {'close_raw': '5.1', 'sma20': 5, 'collected_at_utc': 'a'}}
        b = deepcopy(a); b['TNX']['collected_at_utc'] = 'b'
        self.assertEqual(stable_market(a), stable_market(b))
        b['TNX']['sma20'] = 6
        self.assertNotEqual(stable_market(a), stable_market(b))


@unittest.skipUnless(policy.FONT_PATH.is_file(), 'install pinned caption font for render tests')
class PixelCaptionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def test_incident_fits_at_original_font_without_four_lines(self):
        text = resolve(INCIDENT, SNAPSHOT)
        with self.assertRaises(ValueError):
            _wrap_korean(text, 19, max_lines=3)
        protected = [fact('TNX', 'change_pct', SNAPSHOT), fact('VIX', 'change_pct', SNAPSHOT)]
        layout = build_layout(text, protected)
        self.assertEqual(layout['font_size_px'], 60)
        self.assertEqual(layout['line_count'], 3)
        self.assertEqual(canonical(' '.join(layout['lines'])), canonical(text))
        for value in protected:
            self.assertTrue(any(value in line for line in layout['lines']))
        bbox = verify_mask(text, layout, Path(self.directory.name))
        self.assertGreaterEqual(bbox[0], 90)
        self.assertLessEqual(bbox[2], 990)

    def test_four_line_candidate_retains_all_text(self):
        text = '시장의 위험을 확인하고 비중과 손실 한도를 점검한다 ' * 2
        layout = build_layout(text)
        self.assertEqual(layout['line_count'], 4)
        self.assertEqual(canonical(' '.join(layout['lines'])), canonical(text))
        verify_mask(text, layout, Path(self.directory.name))

    def test_impossible_unspaced_word_is_rejected_without_truncation(self):
        with self.assertRaisesRegex(LayoutError, 'overflow'):
            build_layout('한' * 100)

    def test_unsupported_glyph_and_changed_font_fail_closed(self):
        with self.assertRaisesRegex(LayoutError, 'unsupported_glyph'):
            build_layout('위험 \U0010ffff')
        with patch('src.caption_policy.FONT_SHA256', 'wrong'):
            with self.assertRaisesRegex(LayoutError, 'checksum_mismatch'):
                build_layout('시장 위험')

    def test_tampered_layout_is_not_rendered(self):
        layout = build_layout('시장 위험을 점검한다')
        layout['lines'] = ['수익을 보장한다']
        with self.assertRaisesRegex(LayoutError, 'manifest_changed'):
            validate_layout('시장 위험을 점검한다', layout)

    def test_same_input_has_deterministic_layout(self):
        self.assertEqual(build_layout('한글 English 12.34% 혼합 문장'),
                         build_layout('한글 English 12.34% 혼합 문장'))

    def test_attached_fact_prefix_does_not_invent_or_delete_spaces(self):
        text = '오늘VIX 21.45% 상승에 대비한다'
        layout = build_layout(text, ['VIX 21.45% 상승'])
        self.assertEqual(canonical(' '.join(layout['lines'])), text)

    @patch.dict(os.environ, {'CAPTION_LAYOUT_POLICY': policy.ADAPTIVE})
    def test_actual_video_renderer_consumes_manifest_without_rewrapping(self):
        from PIL import Image
        base = Path(self.directory.name)
        image = base / 'source.png'; Image.new('RGB', (1080, 1920), '#263548').save(image)
        text = resolve(INCIDENT, SNAPSHOT); layout = build_layout(text)
        verify_mask(text, layout, base / 'masks')
        _render_segment(str(image), text, None, .5, base / 'segment.mp4', base / 'ffmpeg.log',
                        False, caption_layout=layout)
        self.assertGreater((base / 'segment.mp4').stat().st_size, 1000)


class ControlDiagnosticsTest(unittest.TestCase):
    def test_inspect_does_not_claim_or_mutate(self):
        from scripts.operational_recovery import main
        with patch('scripts.operational_recovery.Control') as cls, patch('sys.argv',
                ['ops', 'inspect', '--day', '2026-10-17', '--hero', 'EDT']), patch('builtins.print'):
            cls.return_value.status.return_value = {'version': 2, 'exists': False}
            main()
            cls.return_value.claim.assert_not_called()
            cls.return_value.release.assert_not_called()

    def test_historical_reservation_cannot_use_old_daily_budget(self):
        control = Control('production', 'EDT'); control.day = '2000-01-01'
        with patch.object(control, 'rpc') as rpc:
            with self.assertRaisesRegex(ControlError, 'historical_generation_forbidden'):
                control.reserve('text', 'storyboard', {})
            rpc.assert_not_called()

    def test_production_requires_schema_before_claim(self):
        control = Control('production', 'EDT')
        with patch.object(control, 'status', side_effect=ControlError('schema')), patch.object(control, 'rpc') as rpc:
            with self.assertRaises(ControlError):
                control.claim()
            rpc.assert_not_called()

    def test_unknown_policy_does_not_fall_back(self):
        with patch.dict(os.environ, {'CAPTION_LAYOUT_POLICY': 'misspelled'}):
            with self.assertRaisesRegex(ValueError, 'unknown_caption_policy'):
                policy.adaptive()
