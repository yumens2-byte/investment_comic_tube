import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
from cryptography.fernet import Fernet

from src.pipeline_control import Control, ControlError, activate, reset, generate
from src.market_facts import resolve, fact, validate_provenance
from src.validation import ValidationError
from src.upload_state import upload


class FactsTest(unittest.TestCase):
    def test_typed_change_sign_and_yield(self):
        s={'SPX': {'change_pct': -1.125}, 'TNX': {'close': 4.9}}
        self.assertEqual(resolve('{{FACT:SPX.change_pct}}',s), 'S&P 500 1.13% 하락')
        self.assertEqual(fact('TNX','close',s),'미 10년물 금리 4.90%')

    def test_hallucination_unit_sign_missing_rejected(self):
        s={'SPX': {'change_pct': -1.1}}
        for line in ['증시 3% 상승','세 배 상승','{{FACT:SPX.change_pct}} 상승',
                     '{{FACT:SPX.change_pct}}%', '{{FACT:GOLD.close}}', '{{FACT:SPX.invalid}}']:
            with self.subTest(line=line), self.assertRaises(ValidationError):
                resolve(line,s)

    def test_holiday_dates_and_null_not_zero(self):
        s={k: {'close': 10, 'close_raw':'10','prev_close_raw':None,'change_pct':None,
               'source':'fixture','source_symbol':k,'observed_date':'2026-09-04',
               'observation_kind':'daily_close'} for k in ('TNX','VIX','NASDAQ','SPX','DXY')}
        validate_provenance(s,datetime(2026,9,7,10,tzinfo=ZoneInfo('America/New_York')))
        with self.assertRaises(ValidationError):
            fact('SPX','change_pct',s)
        s['TNX']['observed_date']='2026-09-08'
        with self.assertRaises(ValidationError):
            validate_provenance(s,datetime(2026,9,7,10,tzinfo=ZoneInfo('America/New_York')))

    def test_stale_and_inconsistent_previous_rejected(self):
        s={k: {'close': 10, 'close_raw':'10','prev_close_raw':'5','change_pct':1,
               'source':'fixture','source_symbol':k,'observed_date':'2026-09-29',
               'observation_kind':'daily_close'} for k in ('TNX','VIX','NASDAQ','SPX','DXY')}
        with self.assertRaises(ValidationError):
            validate_provenance(s,datetime(2026,9,30,10,tzinfo=ZoneInfo('America/New_York')))
        for m in s.values(): m['change_pct']=None; m['observed_date']='2026-09-20'
        with self.assertRaises(ValidationError):
            validate_provenance(s,datetime(2026,9,30,10,tzinfo=ZoneInfo('America/New_York')))


class GuardTest(unittest.TestCase):
    def test_reservation_failure_prevents_model_call(self):
        control=Mock(); control.reserve.side_effect=ControlError('limit')
        client=Mock(); token=activate(control)
        try:
            with self.assertRaises(ControlError): generate(client,'image',0,model='m',contents='x')
            client.models.generate_content.assert_not_called()
        finally: reset(token)

    def test_no_afc_hidden_calls(self):
        control=Mock(); client=Mock(); token=activate(control)
        try:
            generate(client,'tts',0,model='m',contents='x')
            control.reserve.assert_called_once()
            self.assertTrue(client.models.generate_content.call_args.kwargs['config'].automatic_function_calling.disable)
        finally: reset(token)

    def test_db_error_fails_closed(self):
        with patch('src.pipeline_control.get_client',side_effect=OSError('offline')):
            with self.assertRaises(ControlError): Control('production','EDT').claim()


class Receipt:
    def __init__(self,state=None): self.state=state or {}; self.patches=[]
    def upload(self,patch=None):
        if patch: self.state.update(patch); self.patches.append(dict(patch))
        return dict(self.state)


class UploadTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'v.mp4'; self.path.write_bytes(b'video')
        self.env=patch.dict(os.environ,{'UPLOAD_SESSION_FERNET_KEY':Fernet.generate_key().decode()}); self.env.start()
    def tearDown(self): self.env.stop(); self.tmp.cleanup()
    def response(self,code,headers=None):
        class Response(dict): pass
        r=Response(headers or {}); r.status=code; return r
    def test_session_is_persisted_before_bytes_and_completion_before_return(self):
        c=Receipt(); y=Mock()
        def request(uri,**kw):
            if kw['method']=='POST':
                self.assertEqual(c.state['state'],'session_creating')
                return self.response(200,{'location':'https://www.googleapis.com/upload/test'}),b''
            self.assertIn('session',c.state)
            if kw['headers']['Content-Length']=='0': return self.response(308),b''
            return self.response(201),b'{"id":"v1"}'
        y._http.request.side_effect=request
        self.assertEqual(upload(y,self.path,{},c,'ep1'),'v1')
        self.assertEqual(c.state['state'],'uploaded')
        y._http.request.reset_mock()
        self.assertEqual(upload(y,self.path,{},c,'ep1'),'v1')
        y._http.request.assert_not_called()
    def test_ambiguous_completion_recovered_with_same_session(self):
        c=Receipt(); y=Mock(); n=[0]
        def request(uri,**kw):
            n[0]+=1
            if n[0]==1:return self.response(200,{'location':'https://www.googleapis.com/upload/test'}),b''
            if n[0]==2:return self.response(308),b''
            if n[0]==3:raise OSError('response lost')
            return self.response(201),b'{"id":"already_uploaded"}'
        y._http.request.side_effect=request
        with patch('src.upload_state.time.sleep'):
            self.assertEqual(upload(y,self.path,{},c,'ep1'),'already_uploaded')
        self.assertEqual(sum(call.kwargs['method']=='POST' for call in y._http.request.call_args_list),1)
    def test_expired_session_never_restarts_post(self):
        key=os.environ['UPLOAD_SESSION_FERNET_KEY']
        c=Receipt({'state':'session_ready','session':Fernet(key.encode()).encrypt(b'https://www.googleapis.com/upload/test').decode(),
                   'started_at':__import__('time').time(),'failures':0})
        y=Mock(); y._http.request.return_value=(self.response(404),b'')
        with self.assertRaises(ControlError):upload(y,self.path,{},c)
        self.assertEqual(c.state['state'],'unknown')
        self.assertEqual(y._http.request.call_args.kwargs['method'],'PUT')
    def test_unknown_stops_and_transient_errors_bounded(self):
        with self.assertRaises(ControlError):upload(Mock(),self.path,{},Receipt({'state':'unknown'}))
        y=Mock();y._http.request.side_effect=[(self.response(200,{'location':'https://www.googleapis.com/upload/test'}),b'')]+[OSError('offline')]*3
        c=Receipt()
        with patch('src.upload_state.time.sleep'), self.assertRaises(ControlError):upload(y,self.path,{},c)
        self.assertEqual(c.state['failures'],3)
    def test_session_persistence_failure_sends_no_bytes(self):
        c=Receipt(); original=c.upload
        def fail(patch=None):
            if patch and patch.get('state')=='session_ready': raise ControlError('db lost')
            return original(patch)
        c.upload=fail;y=Mock();y._http.request.return_value=(self.response(200,{'location':'https://www.googleapis.com/upload/test'}),b'')
        with self.assertRaises(ControlError):upload(y,self.path,{},c)
        self.assertEqual(y._http.request.call_count,1)
        self.assertEqual(c.state['state'],'session_creating')

if __name__=='__main__':unittest.main()

class ProtectedRecoveryTest(unittest.TestCase):
    def test_uploaded_receipt_only_synchronizes_database(self):
        from src.pipeline_control import protected
        work=Mock(return_value=0)
        control=Mock();control.fence=1
        control.upload.return_value={'state':'uploaded','episode_id':'ep1','video_id':'v1'}
        with patch.dict(os.environ,{'OPERATIONAL_SAFETY_ENABLED':'true','UPLOAD_SESSION_FERNET_KEY':Fernet.generate_key().decode()}), patch('src.pipeline_control.Control',return_value=control), patch('src.asset_store.verify_store'), patch('src.drive_manager.update_episode') as update:
            self.assertEqual(protected('production')(work)(track='EDT'),0)
            work.assert_not_called();update.assert_called_once_with('ep1',status='published',youtube_video_id='v1')
    def test_unknown_receipt_stops_before_generation(self):
        from src.pipeline_control import protected
        work=Mock(return_value=0);control=Mock();control.fence=1;control.upload.return_value={'state':'unknown'}
        with patch.dict(os.environ,{'OPERATIONAL_SAFETY_ENABLED':'true','UPLOAD_SESSION_FERNET_KEY':Fernet.generate_key().decode()}), patch('src.pipeline_control.Control',return_value=control),patch('src.asset_store.verify_store'):
            self.assertEqual(protected('production')(work)(track='EDT'),1)
            work.assert_not_called()
    def test_bad_secret_stops_before_paid_calls(self):
        from src.pipeline_control import protected
        work=Mock();control=Mock();control.fence=1
        with patch.dict(os.environ,{'OPERATIONAL_SAFETY_ENABLED':'true','UPLOAD_SESSION_FERNET_KEY':'invalid'}), patch('src.pipeline_control.Control',return_value=control),patch('src.asset_store.verify_store'):
            self.assertEqual(protected('production')(work)(track='EDT'),1)
            work.assert_not_called()
    def test_db_reservation_not_swallowed_by_tts_retry(self):
        from src.tts import synthesize_narrations
        c=Mock();c.asset.return_value={};c.reserve.side_effect=ControlError('limit')
        token=activate(c);client=Mock()
        try:
            with patch.dict(os.environ,{'GEMINI_API_KEY':'fixture'}),patch('src.tts.make_client',return_value=client):
                with self.assertRaises(ControlError):synthesize_narrations(['검증'])
            client.models.generate_content.assert_not_called()
        finally:reset(token)

class AdditionalUploadTest(unittest.TestCase):
    setUp = UploadTest.setUp
    tearDown = UploadTest.tearDown
    response = UploadTest.response
    def test_no_progress_responses_bounded(self):
        y=Mock();c=Receipt()
        y._http.request.side_effect=[(self.response(200,{'location':'https://www.googleapis.com/upload/test'}),b'')]+[(self.response(308),b'')]*4
        with self.assertRaises(ControlError):upload(y,self.path,{},c)
        self.assertEqual(c.state['failures'],3)
        self.assertEqual(y._http.request.call_count,5)

class SessionEncryptionTest(unittest.TestCase):
    def test_server_derived_key_stable_and_channel_separated(self):
        from src.session_crypto import session_cipher
        with patch.dict(os.environ,{'UPLOAD_SESSION_FERNET_KEY':'','SUPABASE_SERVICE_ROLE_KEY':'s'*64,'PIPELINE_CHANNEL_KEY':'channel-a'}):
            ciphertext=session_cipher().encrypt(b'session-uri')
            self.assertEqual(session_cipher().decrypt(ciphertext),b'session-uri')
            with patch.dict(os.environ,{'PIPELINE_CHANNEL_KEY':'channel-b'}):
                with self.assertRaises(Exception):session_cipher().decrypt(ciphertext)
    def test_explicit_key_and_missing_server_secret(self):
        from src.session_crypto import session_cipher
        with patch.dict(os.environ,{'UPLOAD_SESSION_FERNET_KEY':Fernet.generate_key().decode()}):
            self.assertEqual(session_cipher().decrypt(session_cipher().encrypt(b'x')),b'x')
        with patch.dict(os.environ,{'UPLOAD_SESSION_FERNET_KEY':'','SUPABASE_SERVICE_ROLE_KEY':''}):
            with self.assertRaises(ValueError):session_cipher()


class ActiveSafetyImageTest(unittest.TestCase):
    def test_prompt_is_pure_with_active_control(self):
        from src.image_generator import _build_prompt
        token = activate(Mock())
        try:
            with patch('src.image_generator.make_client') as factory:
                for track in ('EDT', 'GOC'):
                    with self.subTest(track=track):
                        prompt = _build_prompt({'track': track, 'villain': 'Debt Titan'}, 'market watch', True)
                        self.assertIn('market watch', prompt)
                        self.assertIn('NO numbers', prompt)
                factory.assert_not_called()
        finally:
            reset(token)

    def test_active_preview_reaches_reserved_image_call(self):
        from src.image_generator import generate_scene_images
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(
            parts=[SimpleNamespace(inline_data=SimpleNamespace(data=b'fixture-image'))])
        control = Mock()
        token = activate(control)
        try:
            with tempfile.TemporaryDirectory() as directory, \
                 patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only'}), \
                 patch('google.genai.Client', return_value=client), \
                 patch('src.image_generator._load_reference_images', return_value=[]), \
                 patch('src.asset_store.restore', return_value=False), \
                 patch('src.asset_store.save'), \
                 patch('src.content_quality.validate_image_assets'):
                paths, error = generate_scene_images(
                    {'track': 'EDT', 'villain': 'Debt Titan'}, output_dir=directory,
                    scenes=['market watch'], model_name='preview-fixture', image_size='1K')
                self.assertIsNone(error)
                self.assertEqual(Path(paths[0]).read_bytes(), b'fixture-image')
                control.reserve.assert_called_once()
                client.models.generate_content.assert_called_once()
        finally:
            reset(token)
