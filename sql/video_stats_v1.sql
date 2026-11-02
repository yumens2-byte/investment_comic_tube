-- DN2026_0092 M1 성과 측정: 발행 영상 일별 통계 스냅샷 (video_id, KST 수집일 단위 1행)
-- 쓰기 주체: scripts/collect_performance.py (live 모드만, service_role)
-- 권한 패턴: sql/operational_safety_v1.sql 의 pipeline_slots 와 동일 (RLS 활성 + service_role 전용)
begin;
create table if not exists public.video_stats (
 video_id text not null,
 episode_no integer,
 collected_date date not null,
 collected_at timestamptz not null default now(),
 views bigint,
 likes bigint,
 comments bigint,
 privacy_status text,
 run_mode text not null,
 primary key(video_id, collected_date)
);
alter table public.video_stats enable row level security;
revoke all on public.video_stats from public, anon, authenticated;
grant select,insert,update on public.video_stats to service_role;
commit;
