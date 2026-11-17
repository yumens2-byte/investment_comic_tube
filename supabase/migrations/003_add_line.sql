-- ⚠️ 폐기 (DN2026_0114, 2026-11-17): 적용 금지. CAST 원장은 004_cast_runs.sql(전용 테이블)로 대체됐다.
-- 이미 운영 DB에 적용된 경우 무해하다(기본값 daily, 코드 참조 없음). 이력 정합을 위해 파일만 보존한다.
-- 운영 적용 여부 확인: select 1 from information_schema.columns where table_name='episodes' and column_name='line';

-- CAST-LINE C3 분리: episodes에 line 컬럼 추가 (2026-11-17 상세설계 §6)
-- 기존 daily 라인 무손실: 기존 행은 'daily'로 채우고, 기본값도 daily.
-- CAST 라인 발행 기록은 line='cast'로 구분된다. 기존 쿼리는 역호환(무영향).
-- [폐기] alter table public.episodes
-- [폐기]   add column if not exists line text not null default 'daily'
-- [폐기]   check (line in ('daily', 'cast'));

-- 라인별 당일 중복 방지 인덱스 (validate_not_published_today 확장용)
-- [폐기] create index if not exists episodes_line_market_as_of_idx
-- [폐기]   on public.episodes (line, market_as_of)
-- [폐기]   where status in ('PUBLISHED', 'UPLOADED_PRIVATE');
