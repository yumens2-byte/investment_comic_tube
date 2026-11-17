# CAST-LINE 운영 전환 체크리스트 (v1.1.0, DN2026_0114)

> 이 세션에서는 GitHub Variables·Secrets API와 Supabase DDL에 접근할 수 없다(프록시 403).
> 등록과 적용은 회장이 GitHub 웹 UI와 Supabase SQL Editor에서 직접 수행한다.

## 1. Secrets

| Secret | 사용 job | 비고 |
|---|---|---|
| FAL_AI_KEY | generate | shadow·live만 사용. 없으면 원장 기록 전에 차단 |
| SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY | generate, publish | cast_runs 원장(shadow·live) |
| YOUTUBE_CLIENT_ID / CLIENT_SECRET / REFRESH_TOKEN | publish | 기존 일간 라인과 공용 |
| TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID | report, cast_watchdog | 운영자 전용 방 |
| FMP_API_KEY, ALPHAVANTAGE_API_KEY, FRED_API_KEY | plan | 기존 일간 라인과 공용 |

## 2. Variables

| Variable | 값 | 미등록 시 |
|---|---|---|
| CAST_EXECUTION_MODE | dry_run → shadow → live | dry_run |
| CAST_RUN_CAP_USD | 5 | 5 |
| CAST_MONTH_CAP_USD | 30 | 30 |
| CAST_CUT_SEC | 6 | 6 |
| CAST_YOUTUBE_PRIVACY | private (첫 live 3회 검수 후 public) | private |
| CAST_PLAYLIST_TITLE | EDT_UNIVERSE_CAST | EDT_UNIVERSE_CAST |
| PIPELINE_CHANNEL_KEY_CAST | EDT_UNIVERSE_CAST | EDT_UNIVERSE_CAST |

## 3. Supabase 004 적용 (shadow 전환 전 필수)

파일: `supabase/migrations/004_cast_runs.sql`. SQL Editor에서 실행한다.

확인 쿼리:

```sql
select column_name, data_type from information_schema.columns
where table_schema = 'public' and table_name = 'cast_runs' order by ordinal_position;
select indexname from pg_indexes where tablename = 'cast_runs';
```

기대 결과: 컬럼 12개, 인덱스 `cast_runs_pkey`·`cast_runs_youtube_video_id_key`·`cast_runs_one_publish_per_day`.

롤백: `drop table if exists public.cast_runs;`

003(episodes.line)은 폐기했다. 파일에는 주석 처리된 본문만 남아 있다. 운영 DB에 이미 적용했다면 그대로 둬도 무해하다(기본값 daily, 코드 참조 없음).

## 4. 전환 순서

1. 묶음을 반영한다. 삭제 4건을 포함한다: artifacts_cast/cast_plan.json, src/cast/__pycache__/*.pyc 3개. 003_add_line.sql은 폐기 주석본으로 교체된다.
2. dry_run 수동 dispatch를 실행하고 artifact cast-plan·cast-ledger를 확인한다.
3. 004를 적용한 뒤 shadow로 전환한다. 토요일 cron 3회를 돌리고, 매회 컷 QC와 cast_runs.cost_usd 대사를 한다.
4. live(private)로 전환한다. 3회 검수한 뒤 public으로 바꾼다.
