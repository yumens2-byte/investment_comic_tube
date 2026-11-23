# CAST-LINE — 주 1회 앙상블 Shorts (v1.2.0, DN2026_0114·0115)

EDT 일간 라인(pipeline.yml)과 병렬로 도는 주 1회 파이프라인이다. 기존 파이프라인 파일은 수정하지 않는다.
예외는 DN2026_0114로 승인된 2건이다: tests.yml에 CAST pytest 단계 추가, .gitignore에 `artifacts_cast/` 추가.

## 흐름

| job | 하는 일 | 모드별 외부 호출 |
|---|---|---|
| test | `pytest src/cast/` | 없음 |
| plan | 시장 데이터 수집 → 등급(T1/T2/T3) → 캐스팅 → `cast_plan.json`(tier·plan·mode·run_id·run_date_kst·villain·market_snapshot) | 시장 데이터 API |
| generate | REF URL 확인 → fal minimax/h3 i2v 컷 생성(내레이션 회차: 컷별 5~10초, 그 외 `CAST_CUT_SEC`) → `generate_ledger.json` | dry_run: fal·DB 0 / shadow·live: fal + cast_runs |
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

## P2 스토리라인·내레이션 (v1.2.0, DN2026_0115)

- **스토리 결정**(plan):
  - `storyline.decide`가 `market_regime.select_villain`의 3축 점수로 이번 주 축과 빌런을 정한다.
  - 화면 빌런은 축별 Notion 캐논 빌런이다(DN2026_0120): 금리=Debt Titan, 변동성=Volatility Hydra, 과열=Algorithm Reaper. 회전·스트릭은 market_regime 원 빌런(villain_raw) 기준이다. 영상 캐논에 없는 빌런은 Debt Titan이 그 축의 공격 형태로 대체한다.
  - 시즌 4막(DN2026_0120): 1주 도입 → 2주 고조(밀림·동료 합류) → 3주 결전 → 4주 결말(엔딩은 다음 시즌 예고로 고정).
  - 이어받기(DN2026_0120): story_state에 recap(막·빌런·전환·교훈·예고)과 직전 2회 대사(lines)를 저장해 다음 대본에 넘긴다. 대사는 역할·막별 후보 풀에서 고르고, 직전 2회와 같은 문장은 금지한다.
  - 배역(DN2026_0120): 대본이 비트별 화면 캐릭터(1~2명)를 고르고, situation_en에 그 이름이 없으면 위반으로 처리한다. NEXT_OMEN 컷에는 다음 회 예고 빌런이 나온다.
  - 2순위 축은 다음 주 예고(omen)로 쓴다. 다음 회차는 예고가 맞았는지(hit) 빗나갔는지(twist)를 회수한다.
  - TURN 비트 주역은 축으로 정한다. 금리는 EDT, 변동성은 FG-01, 과열은 LEV-01이다.
  - 시즌은 4주, 같은 빌런은 최대 3주 연속이다.
- **plan 진입점**: `python -m src.cast.__main__plan` 모듈로 실행한다(워크플로 heredoc에서 옮김, 테스트 대상).
- **대본**(plan, `script.py`):
  - Gemini(`gemini-3.6-flash`)가 비트별 한국어 1문장과 영문 상황값을 만든다.
  - 수치는 FACT 토큰으로만 넣는다(`market_facts`). 문장 길이 상한은 첫 비트 30자(지표 토큰 1개 허용, 대입 후), 나머지 24자(숫자·토큰 금지)이고, 투자 권유 금칙어를 검사한다(DN2026_0116, 실측 낭독 속도 초당 약 3.1자).
  - 위반이 있으면 1회 재호출하고, 그래도 위반인 비트는 템플릿 문장으로 바꾼다.
  - dry_run과 키가 없는 경우에는 결정적 템플릿을 쓴다(외부 호출 0).
- **TTS**(generate, `narration.py`): 내레이션 회차는 `CAST_CUT_SEC`를 쓰지 않고 TTS 실측으로 컷 길이를 정한다(DN2026_0116).
  - `src.tts.synthesize_narrations`로 합성한다. 모델은 `CAST_TTS_MODEL`(기본 gemini-3.8-flash-tts), 음성은 `TTS_VOICE`(기본 Charon)이다.
  - 첫 fal 제출 전에 실행한다. TTS wav의 앞뒤 무음(-45dB)을 잘라낸 뒤 길이를 잰다(실패 시 원본 길이).
  - DN2026_0119: TTS(gemini-3.8-flash-tts)는 보낸 글을 그대로 낭독하므로, 대사는 text·톤은 `speech_metadata.style`로 분리해 보낸다(src/tts.py `style_mode="speech_metadata"`, CAST 전용 — 일간 라인은 기존 방식). 0116의 속도 지시 접두어·과길이 재합성은 폐기(원인이 지시문 낭독이었음). 대사만 기준 3자/초 미만이면 `narration_slow_suspect` 경고.
  - 컷 길이 = max(5, ceil(내레이션/tempo + 0.6초)) ≤ 10. 10초를 넘는 비트는 최대 1.25배(atempo, 음높이 유지)로 압축한다(shadow 실측 초당 2.0~3.4자).
  - 1.25배를 넘는 문장은 차단한다. 합성 길이(Σ 내레이션/tempo + 0.6초)가 58초를 넘으면 전 컷 공통 tempo를 1.25배까지 올려 맞추고, 그래도 넘으면 fal 호출 없이 차단한다. 오류에는 전 비트 길이를 남긴다. 컷 합계 16초 미만이면 짧은 컷부터 1초씩 늘린다.
  - 스토리 상태(record_story)는 TTS·실측 비용 판정을 통과한 뒤, 첫 fal 제출 직전에 기록한다.
  - 비용 가드: 사전에는 최악값(전 컷 10초), TTS 후 실측 컷 길이로 회당·월 상한을 다시 판정한다. fal 제출마다 해당 컷 길이로 가산한다.
- **합성**(publish, `compose.py`):
  - 컷 길이는 max(5초, 내레이션 길이 + 0.6초)로 자르고, 원본 컷보다 길게 늘이지 않는다.
  - 내레이션은 컷 시작점에 배치하고 BGM 0.15와 섞은 뒤 loudnorm -14 LUFS로 맞춘다.
  - 자막은 일간 라인과 같은 adaptive 레이아웃(`caption_layout`, Noto CJK)을 쓴다.
- **원장**: 005가 cast_runs에 villain·story_state·script를 추가한다. 연속성 조회는 live에서 published만, shadow에서는 generated도 포함한다. 현재 run은 조회에서 제외한다.
- **영상 프롬프트**: 캐논 `_cast_video_canon`의 캐릭터별 영문 고정 블록에 대본 situation_en을 덧붙인다. 화면 텍스트는 금지다.

### 추가 운영 설정

| 종류 | 이름 | 값 |
|---|---|---|
| Secret | GEMINI_API_KEY | 일간 라인과 공용 |
| Variable | CAST_TTS_MODEL / CAST_TTS_VOICE | gemini-3.8-flash-tts / Charon (선택) |
| DB | supabase/migrations/005_cast_story.sql | shadow 전환 전 적용 필수(plan이 연속성 조회 시 컬럼 필요) |

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

`PYTHONPATH=. python -m pytest -q src/cast/` — 273 passed (2026-11-18, 기준 e3745a2의 41에서 증가).
