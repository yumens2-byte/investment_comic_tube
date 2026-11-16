# CAST-LINE 운영 전환 체크리스트 — 마스터 확인용 (2026-11-17)

> 본 문서는 코드에서 확인 가능한 요구사항을 정리한 것이다. 실제 등록·적용은
> 마스터가 GitHub 웹 UI·Supabase 대시보드에서 수행해야 한다 — 이 세션의 토큰으로는
> Secrets API·DB DDL을 검증할 수 없다(아래 "세션 검증 한계" 참조).

## 1. GitHub Secrets 등록 확인 (6종)

저장소 Settings → Secrets and variables → Actions. 이름은 워크플로우 참조와 정확히 일치해야 한다.

| # | Secret | 참조 위치 | 확인 방법 | 등록 상태 |
|---|--------|-----------|-----------|-----------|
| 1 | `FAL_AI_KEY` | `cast_t3.yml` generate + `src/cast/generate.py` | generate 단계 dry_run 통과 후 shadow에서 IN_PROGRESS 진입 시 유효 | ☐ 미확인 |
| 2 | `TELEGRAM_BOT_TOKEN` | `cast_watchdog.yml` | watchdog 수동 dispatch → 텔레그램 수신 여부 | ☐ 미확인 |
| 3 | `TELEGRAM_CHAT_ID` | `cast_watchdog.yml` | 상동 | ☐ 미확인 |
| 4 | `FMP_API_KEY` | `cast_t3.yml` plan (기존 pipeline과 동일) | 기존 EDT 라인에서 이미 운영 중 → 등록 이력상 존재 개연 | ☐ 미확인 |
| 5 | `ALPHAVANTAGE_API_KEY` | `cast_t3.yml` plan (폴백 체인) | 상동 | ☐ 미확인 |
| 6 | `FRED_API_KEY` | `cast_t3.yml` plan (폴백 체인) | 상동 | ☐ 미확인 |

참고: 기존 EDT 라인(`pipeline.yml`)이 4~6번을 이미 참조·운영 중이므로, 신규 등록이
필요한 것은 사실상 1~3번( fal 1종 + 텔레그램 2종)이다.

## 2. GitHub Variables 등록 확인 (2종)

| Variable | 용도 | 기본값 |
|----------|------|--------|
| `CAST_EXECUTION_MODE` | 발행 게이트 — dry_run/shadow/live 3단 | `dry_run` (미등록 시 자동) |
| `PIPELINE_CHANNEL_KEY` | 채널 식별 — cast 라인은 `EDT_UNIVERSE_CAST` 권장 | 없으면 기존 키 사용 |

## 3. Supabase 003 마이그레이션 적용

파일: `supabase/migrations/003_add_line.sql` (커밋 8a12bce)

```sql
alter table public.episodes
  add column if not exists line text not null default 'daily'
  check (line in ('daily', 'cast'));
create index if not exists episodes_line_market_as_of_idx
  on public.episodes (line, market_as_of)
  where status in ('PUBLISHED', 'UPLOADED_PRIVATE');
```

적용 방법: Supabase 대시보드 SQL Editor에서 위 SQL 실행 또는
`supabase db push`. 적용 확인 쿼리:

```sql
select column_name, data_type, column_default
from information_schema.columns
where table_name = 'episodes' and column_name = 'line';
```

- 기대 결과: `line | text | 'daily'::text`
- 롤백: `alter table public.episodes drop column if exists line;` (기존 daily 무영향)

## 4. 세션 검증 한계 (정직한 기록)

이 세션에서 확인한 것:
- 워크플로우 YAML이 참조하는 Secret/Variable **이름 목록** (소스 파싱 — 위 표)
- `gsk github` 토큰: 저장소 목록 조회 0건 — Secrets API 접근 불가 확인
- `gh` CLI: 미로그인 — Secrets API 접근 불가 확인

이 세션에서 확인할 수 없은 것 (마스터 확인 필요):
- 각 Secret의 실제 등록 여부·값 유효성 (GitHub API는 값 자체를 반환하지 않으므로
  등록 여부는 웹 UI 또는 실제 실행 발화로만 확인 가능)
- Supabase 실제 적용 여부 (DDL 실행 권한은 이 세션에 없음)

## 5. 전환 순서 권장안

1. Secrets 1~3번 등록 (4~6번은 기존 운영으로 존재 개연 — 이름 대조만)
2. Supabase 003 적용 + 확인 쿼리
3. `CAST_EXECUTION_MODE=dry_run` Variable 등록 → cast_t3 수동 dispatch → plan artifact 확인
4. `shadow` 전환 → 토요일 cron 1회 자동 실행 → fal 생성물 QC
5. 3주 반복 후 리포트 → live 승격 승인
