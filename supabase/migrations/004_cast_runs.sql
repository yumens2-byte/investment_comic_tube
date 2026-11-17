-- 004_cast_runs.sql — CAST-LINE 전용 원장 (DN2026_0114)
-- C3/C4 분리: CAST 라인의 생성·비용·발행 상태는 episodes가 아닌 전용 테이블 cast_runs에 기록한다.
-- 003_add_line.sql은 폐기(미사용) — episodes.line 확장 방식은 채택하지 않는다.
--
-- 용도
--   - 비용 선기록: generate 단계가 fal 제출 직전 cost_usd를 가산(run cap·month cap 판정 근거)
--   - 중복 발행 차단: 같은 run_date_kst(KST)에 uploading/published는 1행만(부분 unique 인덱스)
--   - mode는 shadow/live만 기록한다(dry_run은 DB 0회).

create table if not exists public.cast_runs (
  run_id           text primary key,                -- GITHUB_RUN_ID
  run_date_kst     date not null,
  tier             text not null check (tier in ('T1','T2','T3')),
  mode             text not null check (mode in ('shadow','live')),
  status           text not null check (status in ('generating','generated','gen_failed','uploading','published','upload_failed')),
  cost_usd         numeric(8,2) not null default 0,
  episode_no       integer,
  youtube_video_id text unique,
  market_snapshot  jsonb,
  error            text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

-- 하루 1회 발행 잠금 (C4) — 동시 claim 경합 시 23505로 차단된다.
create unique index if not exists cast_runs_one_publish_per_day
  on public.cast_runs (run_date_kst)
  where status in ('uploading','published');

-- service_role 전용, 정책 없음 — db_client.py 원칙(RLS 유지 + service_role 키로만 접근).
alter table public.cast_runs enable row level security;

-- 확인 쿼리
--   select column_name, data_type from information_schema.columns
--     where table_schema = 'public' and table_name = 'cast_runs' order by ordinal_position;
--   select indexname, indexdef from pg_indexes where tablename = 'cast_runs';
--   select relrowsecurity from pg_class where relname = 'cast_runs';
--
-- 롤백
--   drop table if exists public.cast_runs;   -- 인덱스는 테이블과 함께 삭제된다
