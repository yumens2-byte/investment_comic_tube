-- 005_cast_story.sql — CAST-LINE 스토리 레이어 컬럼 (DN2026_0115 P2)
-- cast_runs에 회차별 빌런·연속성 상태·대본을 기록한다.
--   villain     : 출연 빌런(villain_gate 적용 후 캐논 등재명)
--   story_state : 다음 회차 연속성 입력(season·streak·omen·ending_type·unresolved 등, storyline.next_state)
--   script      : 비트별 내레이션 대본(script.build_script, version/source/ending_type/beats)
-- 읽기: ledger.fetch_last_story — status published(+shadow면 generated) & story_state not null 최신 1행.
-- 기존 컬럼·인덱스·제약 무변경. dry_run은 DB 0회.

alter table public.cast_runs
  add column if not exists villain text,
  add column if not exists story_state jsonb,
  add column if not exists script jsonb;

-- 확인 쿼리
-- select column_name, data_type from information_schema.columns
--  where table_schema = 'public' and table_name = 'cast_runs'
--    and column_name in ('villain', 'story_state', 'script')
--  order by column_name;
-- → 3행 (script jsonb, story_state jsonb, villain text)

-- 롤백
-- alter table public.cast_runs
--   drop column if exists villain,
--   drop column if exists story_state,
--   drop column if exists script;
