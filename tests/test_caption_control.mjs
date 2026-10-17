// Isolated PostgreSQL WASM integration; no connection to operational Supabase.
// npm install --prefix /tmp/tube-pglite --ignore-scripts @electric-sql/pglite@0.5.10
// TUBE_PGLITE_MODULE=/tmp/tube-pglite/node_modules/@electric-sql/pglite/dist/index.js node tests/test_caption_control.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const {PGlite} = await import(process.env.TUBE_PGLITE_MODULE || '@electric-sql/pglite');
const db = new PGlite();
await db.exec(`
 create role anon; create role authenticated; create role service_role bypassrls;
 create schema storage;
 create table storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint);
 create table public.episodes(id text primary key,episode_no integer unique,revision integer,status text,
 market_as_of text,villain text,market_snapshot jsonb,story_state jsonb,created_at text,updated_at text);
 grant select,insert,update on public.episodes to service_role;
`);
await db.exec(readFileSync('sql/operational_safety_v1.sql','utf8'));
await db.exec(readFileSync('sql/caption_recovery_v1.sql','utf8'));
const one = async (sql, params=[]) => (await db.query(sql, params)).rows[0];
const call = async (action,payload={},owner='owner',fence=1,day='2026-10-17') =>
 (await one('select public.pipeline_control_v1($1,$2,$3,$4,$5,$6,$7) as value',
 ['test',day,'production',owner,fence,action,JSON.stringify(payload)])).value;
const status = async () => (await one("select public.pipeline_status_v1('test','2026-10-17','production') as value")).value;
const script = {track:'EDT',episode:45,villain:'Chaos Reaper',market_snapshot:{TNX:{close:5.1}},
 story_state:{track:'EDT'},storyboard:Array.from({length:6},()=>({narration:'시장 위험'}))};
const bind = async (value=script,owner='owner',fence=1,group='production') =>
 (await one('select public.pipeline_bind_episode_v1($1,$2,$3,$4,$5,$6) as value',
 ['test','2026-10-17',group,owner,fence,JSON.stringify(value)])).value;
let checks=0;
const check=(actual,expected)=>{assert.deepEqual(actual,expected);checks++;};
try {
 await db.exec('set role service_role');
 check((await status()).exists,false);
 check((await one('select count(*)::int as n from pipeline_slots')).n,0);
 await call('claim',{hero:'EDT'});
 await call('reserve',{stage:'text',asset:'storyboard'});
 check((await status()).counts['text:storyboard'],1);
 const before = await one('select owner,fence,lease_until from pipeline_slots');
 await status();
 check(await one('select owner,fence,lease_until from pipeline_slots'),before);
 check((await bind(script,'stale')).error,'stale_worker');
 check((await bind(script,'owner',0)).error,'stale_worker');
 const result = await bind();
 check(typeof result.episode_id,'string');
 check(await bind(),result);
 check((await one('select count(*)::int as n from episodes')).n,1);
 check((await bind({...script,episode_id:result.episode_id})),result);
 check((await bind({...script,villain:'changed'})).error,'episode_script_changed');
 check((await bind(script,'owner',1,'preview')).error,'pilot_episode_write_forbidden');
 await call('upload',{state:'unknown',metadata:{private:'hidden'},session:'encrypted'});
 check((await bind()).error,'upload_receipt_protects_episode');
 const receipt=(await status()).upload;
 check('metadata' in receipt,false);check('session' in receipt,false);
 await call('release');
 const second=await call('claim',{hero:'EDT'},'new-owner');
 check((await call('reserve',{stage:'text',asset:'storyboard'},'new-owner',second.fence)).ok,true);
 check((await call('reserve',{stage:'text',asset:'storyboard'},'new-owner',second.fence)).error,'generation_limit_reached');
 await db.exec('reset role; set role anon');
 let denied=false;try {await status();}catch {denied=true;}
 check(denied,true);
 await db.exec('reset role');
 console.log(JSON.stringify({checks,postgres:(await one('select version() as v')).v,result:'PASS'}));
} finally {await db.close();}
