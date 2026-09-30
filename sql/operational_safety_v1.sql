-- Operational deployment definition, applied after schema compatibility verification.
-- Session encryption derives a separate-purpose key from existing service_role by default.
begin;
insert into storage.buckets(id,name,public,file_size_limit)
values('pipeline-assets','pipeline-assets',false,52428800)
on conflict(id) do nothing;
do $$ begin
 if exists(select 1 from storage.buckets where id='pipeline-assets' and public) then
  raise exception 'pipeline-assets must be private';
 end if;
end $$;
create table if not exists public.pipeline_slots (
 channel text not null, kst_date date not null, mode_group text not null,
 hero text not null check (hero in ('EDT','GOC')), owner text,
 fence bigint not null default 1, lease_until timestamptz,
 counts jsonb not null default '{}', limits jsonb not null default '{}', attempts jsonb not null default '[]', assets jsonb not null default '{}',
 upload jsonb not null default '{}',
 primary key(channel,kst_date,mode_group)
);
alter table public.pipeline_slots enable row level security;
revoke all on public.pipeline_slots from public, anon, authenticated;
grant select,insert,update on public.pipeline_slots to service_role;
create or replace function public.pipeline_control_v1(
 p_channel text,p_day date,p_group text,p_owner text,p_fence bigint,
 p_action text,p_payload jsonb default '{}'
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare r public.pipeline_slots; n int; total int; lim int; total_lim int;
 k text; stage text; u jsonb; patch jsonb;
begin
 if p_group not in ('production','preview','full-preview') then
  return jsonb_build_object('error','invalid_mode_group'); end if;
 if p_action='healthcheck' then return jsonb_build_object('version',1); end if;
 perform pg_advisory_xact_lock(hashtextextended(p_channel,0));
 if p_action='claim' then
  if p_group='production' and exists(select 1 from public.pipeline_slots s
   where s.channel=p_channel and s.mode_group='production' and s.kst_date<>p_day
   and s.upload->>'state' in ('session_creating','session_ready','in_progress','unknown')) then
   return jsonb_build_object('error','previous_upload_unresolved'); end if;
  insert into public.pipeline_slots(channel,kst_date,mode_group,hero,owner,lease_until)
   values(p_channel,p_day,p_group,p_payload->>'hero',p_owner,now()+interval '10 minutes')
   on conflict do nothing;
 end if;
 select * into r from public.pipeline_slots where channel=p_channel and kst_date=p_day
  and mode_group=p_group for update;
 if not found then return jsonb_build_object('error','slot_missing'); end if;
 if p_action='claim' then
  if r.owner<>p_owner and r.lease_until>now() then
   return jsonb_build_object('error','slot_busy'); end if;
  if r.owner is distinct from p_owner then r.fence:=r.fence+1; end if;
  update public.pipeline_slots set owner=p_owner,fence=r.fence,
   lease_until=now()+interval '10 minutes' where channel=p_channel and kst_date=p_day and mode_group=p_group;
  return jsonb_build_object('fence',r.fence,'hero',r.hero,'upload',r.upload);
 end if;
 if r.owner is distinct from p_owner or r.fence<>p_fence or r.lease_until<=now() then
  return jsonb_build_object('error','stale_worker'); end if;
 if p_action='reserve' then
  stage:=p_payload->>'stage'; k:=stage||':'||(p_payload->>'asset');
  lim:=case when p_group='preview' then 1 when stage='text' then
   case when p_payload->>'asset'='polish' then 1 else 2 end else 2 end;
  total_lim:=case stage when 'text' then 4 when 'image' then 8 when 'tts' then 12 when 'render' then 2 else 0 end;
  if p_group='preview' then total_lim:=case when stage='text' then 0 else 1 end; end if;
  lim:=lim+coalesce((r.limits->>k)::int,0);
  total_lim:=total_lim+coalesce((r.limits->>(stage||':total'))::int,0);
  n:=coalesce((r.counts->>k)::int,0); total:=coalesce((r.counts->>(stage||':total'))::int,0);
  if n>=lim or total>=total_lim then return jsonb_build_object('error','generation_limit_reached'); end if;
  r.counts:=r.counts||jsonb_build_object(k,n+1,stage||':total',total+1);
  r.attempts:=r.attempts||jsonb_build_array(p_payload||jsonb_build_object('ordinal',n+1,'owner',p_owner,'reserved_at',now()));
 elsif p_action='upload' then
  if p_group<>'production' then return jsonb_build_object('error','pilot_upload_forbidden'); end if;
  patch:=p_payload; u:=r.upload;
  if patch<>'{}' then
   if u->>'state'='uploaded' and patch->>'state' is distinct from 'uploaded' then
    return jsonb_build_object('error','upload_state_regression'); end if;
   if u->>'sha256' is not null and patch->>'sha256' is not null and u->>'sha256'<>patch->>'sha256' then
    return jsonb_build_object('error','upload_file_changed'); end if;
   if u->>'video_id' is not null and patch->>'video_id' is not null and u->>'video_id'<>patch->>'video_id' then return jsonb_build_object('error','video_id_changed'); end if;
   r.upload:=u||patch;
  end if;
 elsif p_action='override' then
  stage:=p_payload->>'stage'; k:=stage||':'||(p_payload->>'asset');
  n:=(p_payload->>'extra')::int;
  if (p_group='preview' and stage='text') or stage not in ('text','image','tts','render') or n<1 or n>2 or
   coalesce((r.limits->>(stage||':total'))::int,0)+n>2 or length(coalesce(p_payload->>'reason',''))<5 then
   return jsonb_build_object('error','invalid_finite_override'); end if;
  r.limits:=r.limits||jsonb_build_object(k,coalesce((r.limits->>k)::int,0)+n,
   stage||':total',coalesce((r.limits->>(stage||':total'))::int,0)+n);
  r.attempts:=r.attempts||jsonb_build_array(p_payload||jsonb_build_object('action','override','owner',p_owner,'at',now()));
 elsif p_action='reconcile' then
  if r.upload->>'state' not in ('unknown','session_creating','session_ready','in_progress') or length(coalesce(p_payload->>'reason',''))<5 then
   return jsonb_build_object('error','invalid_reconciliation'); end if;
  if p_payload->>'video_id' is not null then
   r.upload:=r.upload||jsonb_build_object('state','uploaded','video_id',p_payload->>'video_id');
  elsif p_payload->>'verified_absent'='true' then
   if coalesce((r.upload->>'manual_restarts')::int,0)>=1 then return jsonb_build_object('error','manual_restart_limit'); end if;
   r.upload:=(r.upload-'session'-'started_at'-'failures')||jsonb_build_object('state','prepared','manual_restarts',1);
  else return jsonb_build_object('error','reconciliation_evidence_required'); end if;
  r.attempts:=r.attempts||jsonb_build_array(p_payload||jsonb_build_object('action','reconcile','owner',p_owner,'at',now()));
 elsif p_action='invalidate_asset' then
  if length(coalesce(p_payload->>'reason',''))<5 or p_payload->>'key' is null then
   return jsonb_build_object('error','asset_invalidation_reason_required'); end if;
  r.assets:=r.assets-(p_payload->>'key');
  r.attempts:=r.attempts||jsonb_build_array(p_payload||jsonb_build_object('action','invalidate_asset','owner',p_owner,'at',now()));
 elsif p_action='asset' then
  k:=p_payload->>'key'; patch:=p_payload->'patch';
  if patch<>'{}' then r.assets:=r.assets||jsonb_build_object(k,patch); end if;
 elsif p_action not in ('heartbeat','release') then return jsonb_build_object('error','invalid_action');
 end if;
 update public.pipeline_slots set owner=case when p_action='release' then null else r.owner end, counts=r.counts,limits=r.limits,attempts=r.attempts,assets=r.assets,upload=r.upload,
  lease_until=case when p_action='release' then now() else now()+interval '10 minutes' end
  where channel=p_channel and kst_date=p_day and mode_group=p_group;
 return case when p_action='upload' then r.upload when p_action='asset' then coalesce(r.assets->k,'{}')
  else jsonb_build_object('ok',true,'counts',r.counts) end;
end $$;
revoke all on function public.pipeline_control_v1(text,date,text,text,bigint,text,jsonb) from public,anon,authenticated;
grant execute on function public.pipeline_control_v1(text,date,text,text,bigint,text,jsonb) to service_role;
commit;
