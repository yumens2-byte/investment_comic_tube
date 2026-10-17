"""Real local PostgreSQL tests; opt-in, never connects to operational Supabase."""
import concurrent.futures
import json
import os
import subprocess
import unittest
from pathlib import Path

@unittest.skipUnless(os.getenv('TUBE_TEST_POSTGRES')=='1','local PostgreSQL integration opt-in')
class PostgresControlTest(unittest.TestCase):
    def sql(self,sql):
        host=os.getenv('TUBE_TEST_PGHOST','/tmp/tube-pg-socket')
        if host not in ('/tmp/tube-pg-socket','127.0.0.1','localhost'):
            raise RuntimeError('tests require isolated local PostgreSQL')
        r=subprocess.run(['psql','-h',host,'-p','55432','-U',os.getenv('TUBE_TEST_PGUSER','nobody'),
                          '-d',os.getenv('TUBE_TEST_PGDATABASE','postgres'),'-X','-qAt','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,check=True)
        return r.stdout.strip()
    @classmethod
    def setUpClass(cls):
        self=cls()
        self.sql("drop table if exists public.pipeline_slots cascade;")
        for role in ('anon','authenticated','service_role'):
            if not self.sql(f"select 1 from pg_roles where rolname='{role}';"):
                self.sql(f"create role {role}" + (' bypassrls' if role=='service_role' else '') + ';')
        self.sql("create schema if not exists storage; create table if not exists storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint);")
        self.sql(Path('sql/operational_safety_v1.sql').read_text())
        self.sql('''create table if not exists public.episodes(id text primary key,episode_no integer unique,
          revision integer,status text,market_as_of text,villain text,market_snapshot jsonb,story_state jsonb,
          created_at text,updated_at text); grant select,insert,update on public.episodes to service_role;''')
        self.sql(Path('sql/caption_recovery_v1.sql').read_text())
    def call(self,action,payload=None,owner='w1',fence=1,day='2026-09-30',group='production'):
        data=json.dumps(payload or {}).replace("'","''")
        return json.loads(self.sql(f"set role service_role; select public.pipeline_control_v1('test','{day}','{group}','{owner}',{fence},'{action}','{data}'::jsonb);"))
    def setUp(self):self.sql('truncate public.pipeline_slots,public.episodes;')

    def bind(self,script=None,owner='w1',fence=1):
        script=script or {'track':'EDT','episode':45,'villain':'Chaos Reaper',
                          'market_snapshot':{},'story_state':{},'storyboard':[{'narration':'시장 위험'}]*6}
        data=json.dumps(script).replace("'","''")
        return json.loads(self.sql(f"set role service_role; select public.pipeline_bind_episode_v1('test','2026-09-30','production','{owner}',{fence},'{data}'::jsonb);"))

    def test_read_only_status_does_not_claim_slot(self):
        query="set role service_role; select public.pipeline_status_v1('test','2026-09-30','production');"
        self.assertFalse(json.loads(self.sql(query))['exists'])
        self.assertEqual(self.sql('select count(*) from pipeline_slots;'),'0')
        self.call('claim',{'hero':'EDT'})
        before=self.sql('select row_to_json(s) from pipeline_slots s;')
        self.sql(query)
        self.assertEqual(self.sql('select row_to_json(s) from pipeline_slots s;'),before)

    def test_episode_binding_is_atomic_idempotent_and_fenced(self):
        self.call('claim',{'hero':'EDT'})
        self.assertEqual(self.bind(owner='old')['error'],'stale_worker')
        result=self.bind()
        self.assertEqual(result,self.bind())
        self.assertEqual(self.sql('select count(*) from episodes;'),'1')
        self.call('release');claimed=self.call('claim',{'hero':'EDT'},owner='w2')
        self.assertEqual(self.bind()['error'],'stale_worker')
        self.assertEqual(self.bind(owner='w2',fence=claimed['fence']),result)

    def test_binding_rejects_changed_or_deleted_episode(self):
        self.call('claim',{'hero':'EDT'});self.bind()
        script={'track':'EDT','episode':45,'villain':'changed','market_snapshot':{},'story_state':{},'storyboard':[{'narration':'시장 위험'}]*6}
        self.assertEqual(self.bind(script)['error'],'episode_script_changed')
        self.sql('truncate episodes;')
        self.assertEqual(self.bind()['error'],'bound_episode_missing')

    def test_caption_functions_are_server_only(self):
        for role in ('anon','authenticated'):
            with self.subTest(role=role),self.assertRaises(subprocess.CalledProcessError):
                self.sql(f"set role {role};select public.pipeline_status_v1('test','2026-09-30','production');")
    def test_retries_persist_and_concurrent_reservations_bounded(self):
        self.call('claim',{'hero':'EDT'})
        def reserve(_):return self.call('reserve',{'stage':'image','asset':'0','input_hash':'x'})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:r=list(pool.map(reserve,range(8)))
        self.assertEqual(sum(not x.get('error') for x in r),2)
        self.call('release');claimed=self.call('claim',{'hero':'EDT'},owner='w2')
        self.assertEqual(self.call('reserve',{'stage':'image','asset':'0'},owner='w2',fence=claimed['fence'])['error'],'generation_limit_reached')
        self.assertEqual(self.call('reserve',{'stage':'tts','asset':'0'})['error'],'stale_worker')
    def test_uploaded_cannot_regress_or_change_file(self):
        self.call('claim',{'hero':'EDT'})
        self.call('upload',{'state':'uploaded','video_id':'v','sha256':'a'})
        self.assertEqual(self.call('upload',{'state':'unknown'})['error'],'upload_state_regression')
        self.assertEqual(self.call('upload',{'state':'uploaded','sha256':'b'})['error'],'upload_file_changed')
        self.assertEqual(self.call('upload')['video_id'],'v')
    def test_unresolved_previous_day_blocks_new_production(self):
        self.call('claim',{'hero':'EDT'});self.call('upload',{'state':'unknown'})
        self.assertEqual(self.call('claim',{'hero':'GOC'},day='2026-10-01')['error'],'previous_upload_unresolved')
    def test_preview_never_uploads_and_shares_single_call(self):
        self.call('claim',{'hero':'EDT'},group='preview')
        self.assertEqual(self.call('upload',group='preview')['error'],'pilot_upload_forbidden')
        self.call('reserve',{'stage':'image','asset':'0'},group='preview')
        self.assertEqual(self.call('reserve',{'stage':'image','asset':'1'},group='preview')['error'],'generation_limit_reached')
        self.assertEqual(self.call('reserve',{'stage':'text','asset':'goc'},group='preview')['error'],'generation_limit_reached')
    def test_public_access_denied(self):
        with self.assertRaises(subprocess.CalledProcessError):self.sql("set role anon; select * from public.pipeline_slots;")
        with self.assertRaises(subprocess.CalledProcessError):self.sql("set role authenticated; select public.pipeline_control_v1('t','2026-09-30','preview','o',1,'claim','{}');")

    def test_override_finite_audited_and_reconciliation_one_restart(self):
        self.call('claim',{'hero':'EDT'})
        self.call('reserve',{'stage':'image','asset':'0'});self.call('reserve',{'stage':'image','asset':'0'})
        self.assertFalse(self.call('override',{'stage':'image','asset':'0','extra':1,'reason':'reviewed failure'}).get('error'))
        self.assertFalse(self.call('reserve',{'stage':'image','asset':'0'}).get('error'))
        self.assertEqual(self.call('override',{'stage':'image','asset':'0','extra':2,'reason':'reviewed failure'})['error'],'invalid_finite_override')
        self.call('upload',{'state':'unknown','sha256':'a'})
        self.assertFalse(self.call('reconcile',{'verified_absent':True,'reason':'operator channel review'}).get('error'))
        self.assertEqual(self.call('upload')['state'],'prepared')
        self.call('upload',{'state':'unknown'})
        self.assertEqual(self.call('reconcile',{'verified_absent':True,'reason':'operator channel review'})['error'],'manual_restart_limit')

    def test_healthcheck_read_only_and_cache_invalidation_retains_counts(self):
        self.assertEqual(self.call('healthcheck')['version'],1)
        self.assertEqual(self.sql('select count(*) from public.pipeline_slots;'),'0')
        self.call('claim',{'hero':'EDT'})
        self.call('reserve',{'stage':'image','asset':'0'})
        self.call('asset',{'key':'image:0','patch':{'checksum':'a'}})
        self.call('invalidate_asset',{'key':'image:0','reason':'image QA failed'})
        self.assertEqual(self.call('asset',{'key':'image:0'}),{})
        self.assertEqual(self.call('heartbeat')['counts']['image:0'],1)
