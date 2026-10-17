# 자막 적응형 배치·대본 복구 구현 및 운영 안내

기준: 2026-10-17, caption-layout-v1. 설계 근거는 기존 quality_beta_v3.md 및 story_fact_gate_quality_v1.md와 10/15~17 장애 로그다. 구현 브랜치의 검증 결과이며 운영 반영 완료를 뜻하지 않는다.

## 적용한 변경

- 본편은 실제 폰트 폭으로 줄바꿈한다. 60px·3줄 → 60px·4줄 → 58px·4줄 → 56px·4줄 순서다. 훅 규격은 유지한다.
- 시장 사실 구간(지표명·숫자·단위·방향)을 줄바꿈 중간에서 나누지 않고 문장을 잘라내지 않는다. 원문의 공백/NFC만 정규화한다.
- 표시 영역 후보는 1080×1920에서 x=90, y=1146, w=900, h=320이다. 실측 텍스트 마스크의 모든 픽셀이 이 안에 있어야 한다. 실제 Shorts UI 검수 전 후보다.
- Pillow 폭 측정과 FFmpeg 실제 렌더를 함께 사용한다. 로컬 fixture에서 두 엔진의 폭 차이가 최대 5px라, 초안의 2px 교정 후보를 8px로 조정했다. 영역 포함 검사는 오차를 허용하지 않는다.
- FFmpeg의 기본 다중 행 배치가 폰트 ascent/descent 때문에 4줄을 높게 만드는 것을 확인했다. 본편 각 줄을 별도 drawtext로 배치해 실제 글리프 높이와 12px 줄 간격을 사용한다. 동일 필터로 시험 마스크와 최종 영상을 그린다.
- adaptive-v1의 실제 설정값은 `CAPTION_LAYOUT_POLICY=caption-layout-v1`이다. 기본값은 `legacy-v1`로, 검수 전 자동 운영에 새 자막 정책을 적용하지 않는다.
- 안전 모드 EDT에서 최종 결과에 쓰이지 않던 독립 polish 호출을 제거했다. 최상위 narration은 검증된 훅이다. 텍스트 정상 호출은 1회, 수정 포함 최대 2회다.
- 원본 모델 응답을 검증 전에 보존한다. 문장별 검사 결과와 최종 대본을 private 저장소에 남기고, 재실행에서 원본 응답을 재검증한다. 기존에 유효했던 문장을 수정 응답보다 우선한다.
- 시장 파생값과 이전 회차 컨텍스트를 고정한다. 수집 시각만 달라진 경우를 제외하고 입력이 달라지면 자동 재사용하지 않는다. 현재 시점의 provenance 검사도 유지한다.
- episode 생성·슬롯 연결을 하나의 DB 함수로 묶었다. 슬롯 fence/lease 확인 후 생성하고 연결을 같은 트랜잭션에 저장한다. 삭제된 연결 회차·스크립트 변경·기존 업로드 원장은 거부한다. 기존 번호 충돌 회차를 자동 삭제하지 않는다.
- inspect는 새 슬롯 생성·lease 갱신·claim 없이 counts/limits/자산 키/업로드 상태를 조회한다. 세션·메타데이터는 출력하지 않는다.

## 배포 선행 조건

운영 DB에 이번 SQL을 적용하지 않고 코드만 병합하면 production은 유료 호출 전에 `caption_recovery_schema_required`로 중단한다. SQL을 먼저 적용하고 코드를 배포한다. 기존 pipeline_control_v1의 카운터·원장·권한은 유지한다.

1. 운영 스키마가 lower-case 상태 및 id/episode_no/market_snapshot/story_state 계약인지 확인한다. 저장소의 초기 001 migration은 현재 운영 계약과 다르므로 다시 실행하지 않는다.
2. `sql/operational_safety_v1.sql`이 이미 적용돼 있는지 확인한 뒤, 신규 `sql/caption_recovery_v1.sql`만 검토·적용한다. 새 함수는 SECURITY INVOKER이며 service_role만 호출 가능하다.
3. 코드 배포 전 기존 실행/업로드 receipt 상태를 확인한다. 진행 중인 작업을 중복 시작하지 않는다.
4. legacy 정책으로 새 스키마·checkpoint·호출 절약 경로를 검증한다. 적응형 자막은 무업로드 full 품질 파일럿에서 별도 검수한다.

## 명령과 모드

```bash
# 운영 기본 경로: 실제 실행일 KST 슬롯과 현재 시장 자료 사용
python main.py --run-today

# 새 자막용 고정 폰트 설치. SHA-256이 다르면 실패한다.
sudo python3 scripts/install_caption_font.py

# 4개 이미지/6개 음성을 쓰는 무업로드 full 품질 파일럿
CAPTION_LAYOUT_POLICY=caption-layout-v1 python main.py --production-video-pilot

# 읽기 전용 진단. 기존 슬롯이 없어도 생성하지 않는다.
python scripts/operational_recovery.py inspect --day YYYY-MM-DD --hero EDT

# 이미 렌더된 업로드 receipt 복구: 신규 대본/이미지/TTS 생성 없음
python scripts/operational_recovery.py resume-existing --day YYYY-MM-DD --hero EDT
```

`production-video-pilot`은 유료 API를 호출하는 모드다. full-preview 한도를 사용하고 episodes 생성·YouTube 업로드는 하지 않는다. 저비용 `video-pilot`은 이미지 1장·음성 1개이므로 6비트 품질 검수를 대신하지 않는다.

`resume-existing`은 현재 구현에서 기존 렌더 영상의 업로드 복구 전용이다. 렌더/receipt가 없는 대본만의 오프라인 전체 파이프라인 복구는 제공하지 않는다. 같은 날 `run-today`는 입력이 그대로이고 유효 checkpoint가 있으면 대본을 재생성하지 않는다. 과거 날짜의 신규 reserve는 금지한다. 과거 run의 GitHub Re-run도 실행 당일 슬롯을 사용하므로 날짜를 혼동하지 않는다.

Actions 수동 실행은 `mode=production-video-pilot`, `caption_policy=caption-layout-v1`로 전체 무업로드 프리뷰를 만들 수 있다. `mode=full`은 실제 EDT 공개 업로드이므로 파일럿 대용으로 사용하지 않는다. 예약 실행은 기존 자막 정책을 유지한다.

## 응답·복구 계약

| 자산 키 | 의미 |
|---|---|
| script-context:market | 수집 시각을 제외한 모든 시장 필드 |
| script-context:generation | 시장·이전 회차·캐릭터 컨텍스트 |
| text-response:storyboard-1 / -2 | 최초/수정 원본 응답 |
| text-evaluation:storyboard-1 / -2 | 대입 문장·거부 사유·정책 |
| script:final | 검증된 대본·레이아웃 |
| episode:binding | 원자적으로 연결한 episode ID·대본 digest |

모델 응답 수신 후 저장 전 장애는 결과 유실 가능성이 있다. 예약은 환불하지 않으며 정확히 한 번 생성을 보장하지 않는다. 저장 실패·checksum 불일치는 다음 유료 호출 전 중단한다. 원본 응답은 정책 변경 시 재검증 가능하나 시장/이전 회차 변경은 거부한다.

최종 응답까지 모두 실패한 경우 단순 Re-run은 같은 응답을 복구하므로 새 시도를 만들지 않는다. 품질 검토 후 신규 시도를 결정했다면 사유와 유한 override를 남기고 해당 응답의 인덱스만 명시적으로 무효화한다. 예시는 절차 설명이며 자동 실행 지시가 아니다.

```bash
python scripts/operational_recovery.py grant-extra --day YYYY-MM-DD --hero EDT \
  --stage text --asset storyboard --extra 1 --reason '검토한 대본 재작성 사유'
python scripts/operational_recovery.py invalidate-asset --day YYYY-MM-DD --hero EDT \
  --stage text-response --asset storyboard-1 --reason '동일 사유: 새 응답 허용'
python scripts/operational_recovery.py invalidate-asset --day YYYY-MM-DD --hero EDT \
  --stage text-response --asset storyboard-2 --reason '동일 사유: 과거 수정 응답 혼입 방지'
```

당일 한도를 삭제하지 않는다. text 전체 추가 상한은 기존 2회다. 추가 1회만 허용한 경우 새 첫 응답도 실패하면 수정 reserve가 차단된다. 자동 재시도 횟수를 늘리거나 날짜를 바꿔 우회하지 않는다. 현재는 정책 변경으로 이미 생성된 응답이 통과하는 경우 무효화가 필요하지 않다.

## 검증 기록과 한계

- 기존 57자 장애 문장: legacy 19자/3줄에서 거부되는 것을 재현했다. 사실 구간을 보존한 픽셀 배치는 60px·3줄로 통과했고 FFmpeg 마스크도 영역 내였다.
- 4줄 후보: 한글 fixture를 60px·4줄로 표시하고 실제 마스크·0.5초 MP4 생성 검사를 수행했다. 360×640 축소 정지 프리뷰도 확인했다.
- 현재 개발 환경은 고정 NotoSansCJKkr-Bold OTF와 FFmpeg 6.1.1이다. 실제 모바일 Shorts UI·캐릭터 이미지/무기 가림·TTS 동기와 전체 운영 6비트 영상은 별도 검수 대상이다.
- Python 전체 테스트는 개발 결과 보고서를 따른다. 로컬 서버 PostgreSQL 전용 테스트는 이 환경의 비루트 프로세스 실행 제한 때문에 건너뛰었다.
- PGlite PostgreSQL 18.6 호환 엔진에서 신규 SQL·읽기 전용 조회·권한 차단·fence·원자적 연결·한도 보존 18개 검사를 통과했다. 실제 운영 Postgres 17 및 동시성 검증의 대체라고 보지 않는다.
- PR 회귀 CI는 격리된 Postgres 17 서비스로 실행했다. 코드 5f5e8ec 기준 [실행 39022445899](https://github.com/yumens2-byte/investment_comic_tube/actions/runs/39022445899)에서 Python 401개 전부 통과(skip 0). 기존/신규 서버 통합 및 동시 예약 상한 검사도 포함한다.
- 유료 Gemini 생성·운영 DB 변경·오늘 회차 복구·실제 YouTube 업로드는 개발 테스트에서 수행하지 않았다.

## 롤백

자막만 문제가 있으면 `CAPTION_LAYOUT_POLICY=legacy-v1`로 복귀한다. 응답·카운터·원장·새 읽기/연결 함수는 삭제하지 않는다. legacy 자막에서는 긴 문장이 다시 차단될 수 있다. 코드 전체를 원복할 때도 업로드 완료/불명 원장을 무시하는 구형 경로를 실행하지 않는다.

## 설계 대비 유보

- 4줄·56/58px·영역 좌표는 구현돼 있으나 실제 Shorts UI 승인 전이다.
- 여러 타임드 자막 cue로 한 문장을 나누는 기능과 음성 forced alignment는 이번 범위가 아니다.
- 과거 날짜의 원본 대본만으로 미디어를 모두 재조립하는 범용 복구 CLI는 미구현이다. 기존 영상 receipt 복구와 당일 checkpoint 재사용까지만 제공한다.
- 기존 번호가 다른 회차에 이미 사용된 경우 자동 삭제 대신 `episode_number_conflict`로 보류한다. 과거 데이터 정리는 별도 확인이 필요하다.
