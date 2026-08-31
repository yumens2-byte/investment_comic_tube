# YouTube 자동 업로드 파이프라인 분석 및 설계

## 1. 목표와 범위

### 제품 목표

장 마감 후 미국 시장 데이터를 근거로 세로형 투자 코믹스 영상을 만들고, 운영자가 내용과
영상을 승인하면 YouTube에 게시한다. 완전 무인화보다 다음 세 가지를 우선한다.

1. **안전성**: 승인하지 않은 콘텐츠는 공개되지 않는다.
2. **재현성**: 어떤 데이터, 프롬프트, 에셋, 모델로 영상이 만들어졌는지 추적할 수 있다.
3. **복구 가능성**: 실패한 단계만 다시 실행해도 에피소드 중복이나 이중 게시가 발생하지 않는다.

### 범위

- 포함: 시세 수집, 대본/스토리보드 생성, 미디어 합성, 검수, YouTube 업로드, 실행 기록
- 제외(초기): 실시간 매매 신호, 개인화 투자 조언, 다채널 동시 게시, 완전 자동 공개

## 2. 현행 코드 분석

### 처리 흐름

`main.py`가 모든 단계를 동기식으로 한 번에 호출한다.

1. `collector.fetch_market_data`: yfinance에서 TNX/VIX/NASDAQ 최근 2일 종가를 조회한다.
2. `director.generate_connected_script`: TNX/VIX 임계값으로 빌런과 테마를 선택한다.
3. `drive_manager`: 직전 회차 조회와 문서 저장처럼 로그를 출력하지만 실제 저장은 하지 않는다.
4. `renderer.render_video`: FFmpeg로 검은 배경의 8초 MP4를 생성한다.
5. `publisher.upload_to_youtube`: OAuth 환경 변수가 있으면 영상을 곧바로 공개 업로드한다.

### 핵심 갭과 위험

| 우선순위 | 문제 | 영향 | 개선 방향 |
|---|---|---|---|
| P0 | 승인 없이 `public` 게시 | 잘못된 정보/미완성 영상 공개 | 기본 `private`, 승인 후 공개 전환 |
| P0 | 단계 상태와 게시 ID를 저장하지 않음 | 재실행 시 중복 게시 | episode/run 테이블과 idempotency key 도입 |
| P0 | 영상 파일이 없으면 성공처럼 mock ID 반환 | 장애가 성공으로 기록될 수 있음 | 명시적인 dry-run 어댑터와 실패 상태 분리 |
| P1 | Drive 연속성 조회가 항상 동일한 값 | 모든 실행이 같은 회차 생성 | DB에서 원자적으로 다음 회차 할당 |
| P1 | Gemini 실제 호출/스키마 검증 없음 | 대본 품질과 형식 보장 불가 | JSON schema 기반 응답 및 정책 검사 |
| P1 | 결측 시 0.0으로 대체 | 0을 실제 시장 값으로 오인 | 값과 함께 `quality/status` 저장, 생성 중단 정책 |
| P1 | 예외/재시도/타임아웃 없음 | 일시적 API 오류에 전체 작업 실패 | 단계별 제한 재시도와 실패 큐 |
| P2 | 렌더링 입력 escaping 미흡 | 따옴표 등에서 FFmpeg 필터 실패 | 텍스트 파일/안전한 필터 인자 사용 |
| P2 | 로그만 있고 구조화 관측성 없음 | 운영 원인 분석이 어려움 | run_id 포함 JSON 로그, 메트릭, 알림 |

## 3. 목표 아키텍처

```text
Scheduler / Manual Trigger
          │
          ▼
     Orchestrator ────────────────┐
       │     │                    │
       ▼     ▼                    ▼
  Market   Episode DB        Artifact Store
  Adapter  (state/audit)     (json/audio/image/mp4)
       │
       ▼
  Script Generator (Gemini) → Schema Validator → Policy Checker
                                                    │
                                                    ▼
                                  Storyboard / Asset / TTS / Renderer
                                                    │
                                                    ▼
                                          Quality Gate
                                                    │
                                          Review & Approval
                                                    │
                                                    ▼
                                     YouTube Publisher Adapter
```

### 컴포넌트 책임

- **Orchestrator**: 상태 전이, 단계별 재시도, 타임아웃, idempotency를 담당한다. 비즈니스 로직은 갖지 않는다.
- **Market Adapter**: 공급자별 응답을 공통 스키마로 변환하고 기준 시각/품질을 표시한다.
- **Episode DB**: 회차, 상태, 승인, YouTube video ID, 입력 해시를 영속화하는 유일한 기준점이다.
- **Script Generator**: Series Bible과 시장 스냅샷을 입력으로 받아 구조화 JSON만 반환한다.
- **Policy Checker**: 금칙어, 수익 보장 표현, 출처 없는 수치, 고지 문구를 검사한다.
- **Media Pipeline**: 스토리보드에 따라 이미지/TTS/BGM/자막을 만들고 FFmpeg로 합성한다.
- **Quality Gate**: 해상도, 길이, 오디오, 자막 safe area, 파일 무결성을 자동 검사한다.
- **Reviewer**: 대본/프리뷰를 승인 또는 반려한다. 승인 주체와 시각을 기록한다.
- **Publisher**: 비공개 업로드, 메타데이터 갱신, 공개 전환을 각각 멱등하게 실행한다.

초기에는 별도 메시지 큐 없이 Python CLI + SQLite로 구현한다. 하루 한두 편 규모에서 검증한 뒤
동시 실행이 필요해질 때 PostgreSQL과 작업 큐로 교체한다.

## 4. 상태 모델과 멱등성

### 상태 머신

```text
DRAFT → DATA_READY → SCRIPT_READY → ASSETS_READY → RENDERED
       → QA_PASSED → REVIEW_PENDING → APPROVED
       → UPLOADED_PRIVATE → PUBLISHED

모든 실행 가능 상태 → FAILED
REVIEW_PENDING → REJECTED → DRAFT (새 revision 생성)
```

- 상태는 선행 조건이 충족될 때만 전진한다.
- 수정은 기존 산출물을 덮지 않고 `revision`을 증가시킨다.
- 재시도는 같은 `episode_id + revision + step`을 사용한다.
- `input_hash`가 같고 성공 산출물이 존재하면 단계를 다시 실행하지 않는다.
- YouTube 응답의 `video_id`를 저장한 뒤에는 upload 요청을 반복하지 않고 상태 조회를 수행한다.

### 최소 테이블

```sql
CREATE TABLE episodes (
  id TEXT PRIMARY KEY,
  episode_no INTEGER UNIQUE NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL,
  market_as_of TEXT,
  script_path TEXT,
  video_path TEXT,
  youtube_video_id TEXT UNIQUE,
  approved_by TEXT,
  approved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE step_runs (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL,
  step TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  input_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  error_code TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  UNIQUE (episode_id, step, input_hash)
);
```

## 5. 데이터 계약

### 시장 스냅샷

```json
{
  "schema_version": "1.0",
  "as_of": "2026-08-31T20:00:00Z",
  "source": "yfinance",
  "instruments": {
    "VIX": {"value": 18.42, "change_pct": 1.2, "currency": null, "quality": "ok"},
    "NASDAQ": {"value": 21500.1, "change_pct": -0.4, "currency": "USD", "quality": "ok"}
  },
  "quality": "ok"
}
```

`quality`는 `ok`, `stale`, `partial`, `invalid` 중 하나다. `partial` 또는 `invalid`이면 자동 대본
생성을 중지하고 검토 대상으로 보낸다. 결측치를 숫자 0으로 바꾸지 않는다.

### 대본 계약

```json
{
  "schema_version": "1.0",
  "episode_no": 102,
  "title": "공포 지수의 습격",
  "theme": "변동성 확대",
  "characters": ["Chaos Reaper"],
  "hook": "공포 지수가 다시 움직였다.",
  "scenes": [
    {
      "order": 1,
      "duration_sec": 3.0,
      "visual_prompt": "...",
      "narration": "...",
      "caption": "VIX 상승",
      "fact_refs": ["market.instruments.VIX"]
    }
  ],
  "description": "...",
  "tags": ["미국증시", "Shorts"],
  "disclaimer": "본 콘텐츠는 정보 제공 목적이며 투자 조언이 아닙니다."
}
```

검증 규칙은 총 길이 15~60초, 모든 수치의 `fact_refs` 존재, 장면 순서 연속, 필수 고지 포함,
제목/설명/태그의 플랫폼 길이 제한 준수다. 모델 출력은 파싱 실패 시 임의 보정하지 않고 한 번만
교정 요청한 뒤 `FAILED_VALIDATION`으로 종료한다.

## 6. 반수동 승인 UX

초기 MVP는 웹 UI 대신 아래 파일 기반 흐름으로 충분하다.

1. `python -m app create --date YYYY-MM-DD`: 데이터와 대본 초안 생성
2. 운영자가 `artifacts/<episode>/script.json` 검토/수정
3. `python -m app render --episode <id>`: 프리뷰 렌더링 및 자동 QA
4. `python -m app approve --episode <id> --reviewer <name>`: 입력 해시와 승인 기록
5. `python -m app upload --episode <id>`: 승인 해시와 현재 산출물 해시가 같을 때 비공개 업로드
6. YouTube Studio 최종 확인 후 `publish` 명령으로 공개 전환

수정 이후 해시가 달라지면 기존 승인은 자동 무효화한다. 운영이 안정되면 이 명령들을 작은
FastAPI 관리자 화면으로 감싼다.

## 7. 안전, 보안, 콘텐츠 정책

- OAuth refresh token과 API key는 `.env` 파일을 커밋하지 않고 CI secret/Secret Manager로 주입한다.
- YouTube OAuth scope는 필요한 최소 권한만 사용하고 테스트/운영 채널 자격 증명을 분리한다.
- 로그에 토큰, 전체 프롬프트의 민감 정보, OAuth 응답을 남기지 않는다.
- 공개 기본값은 `private`; `public` 전환에는 저장된 승인 레코드가 필수다.
- 출처가 확인되지 않은 가격/등락률은 영상과 설명에 포함하지 않는다.
- “확실한 수익”, “무조건 매수” 같은 단정적 투자 표현을 차단한다.
- 생성 이미지, 음성, BGM은 라이선스/생성 출처를 에피소드 manifest에 기록한다.
- 데이터 제공자와 YouTube/Gemini의 약관, 할당량, 합성 콘텐츠 표시 정책은 운영 전 별도 검토한다.

## 8. 오류 처리와 관측성

### 오류 분류

- `TRANSIENT`: 네트워크, 429, 5xx. 지수 백오프로 최대 3회 재시도한다.
- `VALIDATION`: 스키마/정책/미디어 QA 실패. 자동 재시도하지 않고 수정 대기한다.
- `AUTH`: 토큰 만료/권한 부족. 즉시 중단하고 운영자에게 알린다.
- `QUOTA`: 공급자 할당량 초과. 다음 허용 시각까지 예약한다.
- `PERMANENT`: 잘못된 파일/요청. 입력 수정 전 재시도하지 않는다.

모든 로그에 `run_id`, `episode_id`, `revision`, `step`, `attempt`, `duration_ms`, `result`를 포함한다.
핵심 지표는 단계 성공률, 제작 소요 시간, Gemini/미디어 단가, 승인 반려율, 게시 성공률이다.

## 9. 설정 계약

환경 변수는 `.env.example`을 기준으로 관리한다. 향후 `Settings` 객체에서 시작 시 한 번 검증하고,
모듈이 직접 `os.environ`을 읽지 않도록 한다.

- `APP_MODE`: `dry-run`, `review`, `publish`
- `DATA_PROVIDER`: 시장 데이터 공급자
- `GEMINI_API_KEY`, `GEMINI_MODEL`: 생성 모델 설정
- `YOUTUBE_*`: OAuth 자격 증명과 기본 공개 범위
- `DATABASE_URL`, `ARTIFACT_DIR`: 상태와 산출물 저장 위치

## 10. 구현 로드맵과 완료 조건

### Phase 1 — 안전한 골격 (P0)

- CLI, typed settings, SQLite repository, 상태 머신 구현
- dry-run을 명시적 모드로 분리하고 존재하지 않는 영상은 실패 처리
- YouTube 기본 비공개 및 승인 검증
- 단위 테스트와 CI 추가

**완료 조건:** 동일 에피소드를 세 번 재실행해도 DB 레코드와 업로드가 하나이며, 승인 없는 공개가
통합 테스트에서 차단된다.

### Phase 2 — 데이터와 대본 (P1)

- 시장 스냅샷/Series Bible 버전 관리
- Gemini structured output + JSON schema 검증
- fact reference와 콘텐츠 정책 검사
- 프롬프트/응답 비용 및 모델 버전 manifest 기록

**완료 조건:** 결측 데이터, 잘못된 JSON, 금칙 표현 fixture가 모두 차단되고 정상 fixture만
`SCRIPT_READY`에 도달한다.

### Phase 3 — 미디어와 검수 (P1)

- 장면 이미지, TTS, BGM, 자막 어댑터 구현
- FFmpeg 합성과 ffprobe 기반 QA
- 프리뷰/승인/반려 흐름 구현

**완료 조건:** 1080x1920, 15~60초, H.264/AAC 영상과 safe-area 자막을 자동 검사하며 수정 시
승인이 무효화된다.

### Phase 4 — 게시와 운영 (P1/P2)

- resumable private upload, thumbnail/metadata, 공개 전환 분리
- 재시도, 알림, 운영 대시보드, 백업/복구 문서
- 테스트 채널 soak test 후 운영 채널 활성화

**완료 조건:** 강제 네트워크 오류 뒤에도 중복 없이 복구되고, 2주간 테스트 채널에서 수동 개입이
필요한 장애와 모든 비용이 기록된다.

## 11. 첫 개발 스프린트 백로그

| 순서 | 작업 | 산출물 | 예상 규모 |
|---|---|---|---|
| 1 | 설정/CLI 골격 | `app/config.py`, `app/cli.py` | 0.5일 |
| 2 | Episode/StepRun 모델과 SQLite | migration, repository tests | 1일 |
| 3 | 상태 머신/멱등 실행기 | transition unit tests | 1일 |
| 4 | 수집기 데이터 계약 적용 | fixture 기반 collector tests | 1일 |
| 5 | 게시 안전장치 | private upload adapter, fake adapter | 1일 |
| 6 | 승인 명령 | hash 검증, audit record | 0.5일 |
| 7 | CI와 운영 문서 | lint/test workflow, runbook | 0.5일 |

이 순서에서는 콘텐츠 생성 품질보다 먼저 “잘못 공개되지 않고, 중복 실행되지 않는” 기반을 만든다.
