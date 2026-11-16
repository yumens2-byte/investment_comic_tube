-- CAST-LINE C3 분리: episodes에 line 컬럼 추가 (2026-11-17 상세설계 §6)
-- 기존 daily 라인 무손실: 기존 행은 'daily'로 채우고, 기본값도 daily.
-- CAST 라인 발행 기록은 line='cast'로 구분된다. 기존 쿼리는 역호환(무영향).
alter table public.episodes
  add column if not exists line text not null default 'daily'
  check (line in ('daily', 'cast'));

-- 라인별 당일 중복 방지 인덱스 (validate_not_published_today 확장용)
create index if not exists episodes_line_market_as_of_idx
  on public.episodes (line, market_as_of)
  where status in ('PUBLISHED', 'UPLOADED_PRIVATE');
