# Investment Comic Tube

미국 시장 지표를 바탕으로 투자 코믹스 YouTube Shorts를 만드는 자동화 프로젝트입니다.
Gemini에서 반수동으로 수행하던 **수집 → 기획/대본 → 렌더링 → 검수 → 업로드 → 기록** 절차를,
사람의 승인 지점을 유지하면서 재현 가능한 파이프라인으로 옮기는 것이 목표입니다.

> 현재 상태는 **파일럿 프로토타입**입니다. 시장 데이터 수집과 FFmpeg 샘플 렌더링,
> YouTube 업로드 골격은 있지만 Gemini/Drive 연동과 승인 워크플로는 아직 구현되지 않았습니다.
> 따라서 현재 코드를 무인 운영용 프로덕션 시스템으로 간주하면 안 됩니다.

## 현재 동작

```text
yfinance 시세 수집
  → 임계값 기반 캐릭터/테마 선택
  → 8초짜리 FFmpeg 샘플 영상 생성
  → YouTube 업로드(자격 증명이 있을 때만)
```

| 단계 | 구현 상태 | 현재 제약 |
|---|---|---|
| 시장 데이터 | 파일럿 | TNX, VIX, NASDAQ의 최근 종가만 사용 |
| 대본 생성 | 스텁 | Gemini를 호출하지 않고 임계값 규칙으로 한 문장 생성 |
| 연속성 저장 | 스텁 | Google Drive 대신 항상 Ep.101을 반환 |
| 영상 생성 | 파일럿 | 검은 배경과 텍스트로 된 8초 영상 |
| 게시 | 부분 구현 | OAuth 환경 변수가 있으면 즉시 `public` 업로드 |
| 승인/재시도/감사 | 미구현 | 운영 상태 저장소와 승인 UI 없음 |

상세한 현행 분석, 목표 아키텍처, 데이터 계약, 안전장치와 단계별 구현 계획은
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)를 참고하세요.

## 로컬 실행

### 사전 조건

- Python 3.11 이상
- FFmpeg (`ffmpeg` 명령이 `PATH`에 있어야 함)
- 실제 업로드 시 YouTube Data API OAuth 자격 증명

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

`.env`는 자동으로 로드되지 않습니다. 셸, CI secret 또는 향후 설정 로더를 통해 환경 변수를
주입해야 합니다. 자격 증명 없이 실행하면 렌더링까지 진행되고 게시 단계는 건너뜁니다.

## 운영 전 필수 조건

1. 게시 기본값을 `private`로 바꾸고 사람의 승인 뒤에만 공개합니다.
2. 에피소드별 고유 ID와 단계 상태를 영속화하여 중복 생성/업로드를 막습니다.
3. 시장 데이터의 기준 시각, 출처, 결측 여부를 대본과 함께 저장합니다.
4. Gemini 응답을 구조화된 스키마로 검증하고 투자 조언/과장 표현 정책 검사를 수행합니다.
5. 실제 YouTube 업로드는 테스트 채널에서 검증한 뒤 활성화합니다.

## 권장 실행 모드

| 모드 | 용도 | 게시 동작 |
|---|---|---|
| `dry-run` | 개발 및 CI | 외부 쓰기 없음 |
| `review` | 일상 제작 | 비공개 업로드 후 승인 대기 |
| `publish` | 승인 완료 건 게시 | 승인 토큰이 있는 에피소드만 공개 |

현재 코드는 이 모드 분리를 아직 지원하지 않습니다. 구현 순서는 아키텍처 문서의
Phase 1부터 따르는 것을 권장합니다.

## 수집 파일럿 실행

제공된 에피소드 샘플을 계약 검증한 뒤 로컬 산출물 또는 Supabase에 멱등 upsert할 수 있습니다.

```bash
# 외부 쓰기 없는 기본 파일럿
python scripts/run_pilot.py tests/fixtures/episode_sample.json

# Supabase SQL Editor에서 최초 한 번 migration 적용 후 실행
# supabase/migrations/001_create_episodes.sql
export SUPABASE_URL='https://<project>.supabase.co'
export SUPABASE_KEY='<server-side-key>'
python scripts/run_pilot.py tests/fixtures/episode_sample.json --backend supabase
```

로컬 실행은 `artifacts/<episode_id>/episode.json`과 해시·상태를 담은 `manifest.json`을 생성합니다.
Supabase 실행은 `episode_id` 충돌 시 같은 행을 갱신하므로 네트워크 재시도로 행이 중복되지 않습니다.
`SUPABASE_KEY`는 브라우저 코드나 로그에 노출하지 마세요.

## 실행 로그와 OAuth 장애 대응

파이프라인 실행 시 `logs/pipeline.log`에 단계별 로그가 기록되고 FFmpeg 전체 출력은
`logs/ffmpeg.log`에 분리됩니다. GitHub Actions는 성공 여부와 관계없이 두 로그와 생성 영상을
14일 동안 artifact로 보관합니다.

`invalid_grant: Token has been expired or revoked`는 렌더링 오류가 아니라 YouTube refresh token이
만료·취소된 인증 오류입니다. Google OAuth 동의 절차로 채널을 다시 인증하고 repository의
`YOUTUBE_REFRESH_TOKEN` Actions secret을 새 값으로 교체한 뒤 workflow를 재실행해야 합니다.
로그에는 token 값이 기록되지 않습니다. 업로드 기본 공개 범위는 안전을 위해 `private`입니다.

## 스토리 → 영상 우선 파일럿

YouTube 인증과 업로드를 제외하고 샘플 스토리의 검증, 30초 세로 영상 생성, ffprobe QA,
manifest/Supabase 상태 갱신까지만 독립적으로 실행할 수 있습니다.

```bash
python scripts/run_video_pilot.py tests/fixtures/episode_sample.json
```

성공 조건은 `artifacts/EP-20260826-02/preview.mp4`가 H.264/AAC, 1080x1920, 30초로 생성되고
manifest가 `SCRIPT_READY`에서 `RENDERED`로 변경되는 것입니다. 이 영상은 프롬프트 기반 생성 영상이
아니라 장면별 자막과 길이/연속성/인코딩을 확인하는 스토리보드 프리뷰입니다.

GitHub Actions에서 **Run workflow → mode: video-pilot**을 선택하면 YouTube secret을 사용하거나
업로드하지 않고 동일 검증을 수행하며, 영상·manifest·로그를 artifact로 다운로드할 수 있습니다.
Supabase 상태까지 갱신하려면 `002_add_video_artifacts.sql` 적용 후 `--backend supabase`를 사용합니다.
