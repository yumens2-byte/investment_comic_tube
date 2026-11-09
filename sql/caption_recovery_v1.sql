-- Apply after operational_safety_v1.sql and production schema compatibility review.
-- Additive, no table/status changes; counters and upload receipts are preserved.
begin;
create or replace function public.pipeline_status_v1(p_channel text,p_day date,p_group text)
returns jsonb language plpgsql stable security invoker set search_path = '' as $$
declare r public.pipeline_slots;
begin
 select * into r from public.pipeline_slots
  where channel=p_channel and kst_date=p_day and mode_group=p_group;
 if not found then return jsonb_build_object('version',2,'exists',false,'counts','{}'::jsonb,
  'limits','{}'::jsonb,'asset_keys','[]'::jsonb,'upload','{}'::jsonb); end if;
 return jsonb_build_object('version',2,'exists',true,'day',r.kst_date,'hero',r.hero,
  'counts',r.counts,'limits',r.limits,'asset_keys',(select coalesce(jsonb_agg(k),'[]') from jsonb_object_keys(r.assets) k),
  'upload',r.upload-'session'-'metadata','episode',coalesce(r.assets->'episode:binding','{}'::jsonb));
end $$;
revoke all on function public.pipeline_status_v1(text,date,text) from public,anon,authenticated;
grant execute on function public.pipeline_status_v1(text,date,text) to service_role;

create or replace function public.pipeline_bind_episode_v1(
 p_channel text,p_day date,p_group text,p_owner text,p_fence bigint,p_script jsonb)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare r public.pipeline_slots; binding jsonb; ep text; number int; stamp text; digest text; old record;
begin
 if p_group <> 'production' then return jsonb_build_object('error','pilot_episode_write_forbidden'); end if;
 perform pg_advisory_xact_lock(hashtextextended(p_channel,0));
 select * into r from public.pipeline_slots where channel=p_channel and kst_date=p_day and mode_group=p_group for update;
 if not found then return jsonb_build_object('error','slot_missing'); end if;
 if r.owner is distinct from p_owner or r.fence is distinct from p_fence or r.lease_until is null or r.lease_until<=now() then
  return jsonb_build_object('error','stale_worker'); end if;
 if r.upload<>'{}' then return jsonb_build_object('error','upload_receipt_protects_episode'); end if;
 if r.hero <> p_script->>'track' or r.hero <> 'EDT' then return jsonb_build_object('error','episode_track_conflict'); end if;
 if jsonb_typeof(p_script->'market_snapshot') is distinct from 'object' or
    jsonb_typeof(p_script->'story_state') is distinct from 'object' or
    jsonb_typeof(p_script->'storyboard') is distinct from 'array' then
  return jsonb_build_object('error','invalid_episode_script'); end if;
 if jsonb_array_length(p_script->'storyboard')<>6 then
  return jsonb_build_object('error','invalid_episode_script'); end if;
 -- Bind to the immutable final script; subsequent local file changes cannot rebind.
 digest:=encode(sha256(convert_to((p_script-'episode_id')::text,'UTF8')),'hex');
 binding:=r.assets->'episode:binding';
 if binding is not null then
  if binding->>'script_digest'<>digest then return jsonb_build_object('error','episode_script_changed'); end if;
  if not exists(select 1 from public.episodes where id=binding->>'episode_id') then
   return jsonb_build_object('error','bound_episode_missing'); end if;
  return jsonb_build_object('episode_id',binding->>'episode_id');
 end if;
 number:=(p_script->>'episode')::int;
 if number is null or number<1 then return jsonb_build_object('error','invalid_episode_number'); end if;
 -- DN2026_0110: 미발행 실패 회차가 번호를 점유하면 다음 번호(발행 기준)가 매일 충돌한다
 -- (11-06 Ep.58 aborted_validation → 11-07~09 연속 episode_number_conflict).
 -- drive_manager._reclaim_failed_episode 와 같은 규칙으로, 발행·업로드·진행 중 흔적이 없는 행만 회수한다.
 for old in select e.id,e.status,to_jsonb(e)->>'youtube_video_id' as video
   from public.episodes e where e.episode_no=number for update loop
  if old.status in ('published','published_degraded') or old.video is not null
     or exists(select 1 from public.pipeline_slots s where s.upload->>'episode_id'=old.id)
     or exists(select 1 from public.pipeline_slots s
               where s.assets->'episode:binding'->>'episode_id'=old.id
                 and s.lease_until is not null and s.lease_until>now()) then
   return jsonb_build_object('error','episode_number_conflict');
  end if;
  delete from public.step_runs where episode_id=old.id;
  delete from public.episodes where id=old.id;
  raise log 'pipeline_bind_episode_v1 reclaimed episode_no=% old_id=% old_status=%',number,old.id,old.status;
 end loop;
 ep:='ep-'||lpad(number::text,4,'0')||'-'||substr(md5(p_channel||p_day::text||p_group),1,8);
 stamp:=to_char(now() at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00';
 insert into public.episodes(id,episode_no,revision,status,market_as_of,villain,market_snapshot,story_state,created_at,updated_at)
 values(ep,number,1,'script_ready',stamp,p_script->>'villain',p_script->'market_snapshot',p_script->'story_state',stamp,stamp);
 update public.pipeline_slots set assets=assets||jsonb_build_object('episode:binding',
  jsonb_build_object('episode_id',ep,'script_digest',digest)),lease_until=now()+interval '10 minutes'
  where channel=p_channel and kst_date=p_day and mode_group=p_group;
 return jsonb_build_object('episode_id',ep);
end $$;
revoke all on function public.pipeline_bind_episode_v1(text,date,text,text,bigint,jsonb) from public,anon,authenticated;
grant execute on function public.pipeline_bind_episode_v1(text,date,text,text,bigint,jsonb) to service_role;
commit;
