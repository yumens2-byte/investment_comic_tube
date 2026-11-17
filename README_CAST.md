# CAST-LINE — 주 1회 앙상블 Shorts (v1.1.0, DN2026_0114)

EDT 일간 라인(pipeline.yml)과 병렬로 도는 주 1회 파이프라인이다. 기존 파이프라인 파일은 수정하지 않는다.
예외는 DN2026_0114로 승인된 2건이다: tests.yml에 CAST pytest 단계 추가, .gitignore에 `artifacts_cast/` 추가.

## 흐름

| job | 하는 일 | 모드별 외부 호출 |
|---|---|---|
| test | `pytest src/cast/` | 없음 |
| plan | 시장 데이터 수집 → 등급(T1/T2/T3) → 캐스팅 → `cast_plan.json`(tier·plan·mode·run_id·run_date_kst·villain·market_snapshot) | 시장 데이터 API |
| generate | REF URL 확인 → fal minimax/h3 i2v 컷 생성(컷 6초) → `generate_ledger.json` | dry_run: fal·DB 0 / shadow·live: fal + cast_runs |
| publish | shadow: CAST 전용 합성(compose.py)만 하고 결과 artifact를 남긴다. live: 합성 → cast_runs claim → YouTube 업로드 → published 기록 | shadow: 없음 / live: YouTube, cast_runs |
| report | 결과를 텔레그램으로 알림(실패해도 run 결과는 바뀌지 않음) | Telegram |
| cast_watchdog | 토 KST 15:43. 오늘 schedule 실행이 성공했는지 조회 | GitHub API, Telegram |

## 모드

`RUN_MODE = (workflow_dispatch && inputs.mode) || vars.CAST_EXECUTION_MODE || 'dry_run'`이다.
dry_run·shadow·live 외의 값은 dry_run으로 강등된다(src/cast/mode.py). plan job이 값을 한 번 정규화하고, 이후 job은 `needs.plan.outputs.mode`를 쓴다.

## 안전장치

- **비용**:
  - 실행 전 계획 비용(컷 수 × 컷 초 × $0.06)이 회당 상한 `CAST_RUN_CAP_USD`(기본 5)를 넘으면 차단한다.
  - 계획 비용에 이번 달 누적(cast_runs)을 더한 값이 `CAST_MONTH_CAP_USD`(기본 30)를 넘어도 차단한다.
  - 제출 직전마다 런타임 가드를 다시 확인한다.
- **재시도**:
  - fal이 터미널 실패를 반환한 경우에만 재제출한다.
  - 폴링 일시 오류와 다운로드 실패는 같은 작업으로 다시 시도한다. ffprobe 실패는 재제출하지 않는다.
- **시간**: 생성 예산은 `CAST_GEN_BUDGET_SEC`(2400초)이고, 컷마다 데드라인을 둔다. generate job timeout은 50분이다.
- **중복 발행**:
  - cast_runs의 부분 unique 인덱스로 같은 KST 날짜에 uploading·published는 1건만 허용한다.
  - 업로드 전에 claim한다. DB가 불가하면 업로드하지 않는다(fail-closed).
  - 업로드 후 published 기록이 실패하면 행이 uploading으로 남는다. 이 경우 같은 날 재실행이 차단되므로, 확인 후 수동으로 정리한다.
- **부분 생성물**: 계획 컷 수와 실제 mp4 수가 다르면 발행하지 않는다.
- **재시도**: 같은 run을 Re-run하면 이전 시도의 비용이 회당 상한에 합산된다. T3 기준으로 이전 시도가 $2.48 넘게 썼다면 재실행이 차단된다. 재시도는 새 dispatch(새 run_id)로 한다. 이미 uploading·published인 run은 재생성과 재업로드가 모두 차단된다.
- **업로드 예외**: 업로드 중 예외가 나거나 video_id가 없으면 실재 여부를 알 수 없다. 이 경우 행은 `uploading`으로 남고 같은 날 재발행이 막힌다. 정정 절차는 scripts/rollback_live.md 2-1을 따른다.
- **알려진 한계**: 기존 src/publisher.py는 video_id 전체를 로그에 남긴다. CAST는 기존 파일을 수정하지 않으므로, 이 문제는 레포 카드 Known Issue로 관리한다.

## 운영 설정

| 종류 | 이름 | 값·기본 |
|---|---|---|
| Secret | FAL_AI_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN, FMP/ALPHAVANTAGE/FRED_API_KEY | — |
| Variable | CAST_EXECUTION_MODE | dry_run(미등록 시) |
| Variable | CAST_RUN_CAP_USD / CAST_MONTH_CAP_USD | 5 / 30 |
| Variable | CAST_CUT_SEC | 6 (5~15) |
| Variable | CAST_YOUTUBE_PRIVACY | private (첫 live 3회 검수 후 public) |
| Variable | CAST_PLAYLIST_TITLE | EDT_UNIVERSE_CAST |
| Variable | PIPELINE_CHANNEL_KEY_CAST | EDT_UNIVERSE_CAST |
| DB | supabase/migrations/004_cast_runs.sql | shadow 전환 전 적용 필수 |

003_add_line.sql(episodes.line)은 폐기했다. 본문 SQL은 주석 처리된 채 이력용으로만 남아 있다. 운영 DB에 이미 적용했다면 기본값이 daily라서 무해하다.

## 전환 순서

1. 반영 묶음을 적용한다(삭제 4건 포함). `CAST_EXECUTION_MODE=dry_run`을 확인한다.
2. Supabase에 004를 적용하고 확인 쿼리를 실행한다.
3. cast_t3을 수동 dispatch(mode=dry_run)해 plan·ledger artifact를 확인한다.
4. shadow로 전환하고 토요일 cron 3회를 돌린다. 매회 컷을 QC하고 cast_runs의 cost_usd를 대사한다.
5. live로 전환한다(private). 3회 검수한 뒤 `CAST_YOUTUBE_PRIVACY=public`으로 바꾼다.

## 롤백

- 운영 차단: `CAST_EXECUTION_MODE=dry_run`
- 코드 롤백: 기준 커밋 e3745a2의 src/cast·워크플로로 되돌린다.
- DB 롤백: `drop table public.cast_runs;`(다른 테이블 무영향)

## 테스트

`PYTHONPATH=. python -m pytest -q src/cast/` — 143 passed (2026-11-17, 기준 e3745a2의 41에서 증가).
