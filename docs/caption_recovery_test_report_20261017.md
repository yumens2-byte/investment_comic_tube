# 자막·대본 복구 개발 테스트 결과 — 2026-10-17

## 결과와 범위

적응형 본편 자막, 원본 대본 checkpoint, polish 호출 제거, 읽기 전용 진단, 원자적 회차 연결을 구현했다. 새 자막은 opt-in이고 예약 운영은 legacy-v1을 유지한다. 운영 DB/생성 한도/유료 API/YouTube 발행은 변경하거나 실행하지 않았다.

| 검증 | 결과 | 한계 |
|---|---|---|
| 기존 기준선 | Python 377개 중 370 통과, PostgreSQL 전용 7개 skip | 변경 전 결과 |
| 변경 후 전체 | Python 401개 중 390 통과, 로컬 PostgreSQL 전용 11개 skip | 실제 서버 통합은 CI에서 별도 수행 |
| 신규 SQL | PGlite PostgreSQL 18.6에서 18개 검사 통과 | 실제 Postgres 17/동시성의 대체가 아님 |
| 실제 렌더 | FFmpeg 자막 마스크 및 0.5초 MP4 생성 통과 | 합성 배경; 카논·전체 TTS 동기 검증 아님 |
| 코드 기본 검사 | compileall, git diff --check 통과 | 런타임 운영 검증 아님 |
| 원격 CI | PostgreSQL 17 서비스에서 401개 전부 통과, skip 0 | [실행 39022445899](https://github.com/yumens2-byte/investment_comic_tube/actions/runs/39022445899), 코드 5f5e8ec |

## 장애 재현

10/17 실제 거부 문장을 기존 _wrap_korean(19, max_lines=3)에 넣으면 실패한다. 동일 사실 구간을 보존한 픽셀 배치는 60px·3줄에서 통과한다. 4줄 fixture도 같은 60px 폰트로 영역 내 렌더됐다. 360×640 축소 프리뷰에서 텍스트 누락·화면 경계 이탈이 없는 것을 확인했다. 실제 Shorts UI의 버튼/설명과 캐릭터 가림은 검수 전이다.

## 복구와 안전성 검사

- 원본 응답을 검증 전에 저장하며, 한도 소진 후에도 캐시된 응답은 재호출 없이 복구된다.
- checksum 불일치·컨텍스트 변경·저장 실패는 재생성으로 숨기지 않고 중단한다.
- 기존 성공 문장은 수정 응답에서 바뀌어도 기존 것을 유지한다.
- 안전 모드 director가 polish를 호출하지 않는 것을 검사했다.
- 과거 날짜 reserve를 거부하고 inspect가 claim/release를 호출하지 않는 것을 검사했다.
- SQL에서 상태 조회가 슬롯·lease를 바꾸지 않으며, 신규 함수는 anon/authenticated에 차단된다.
- 슬롯 소유자/fence 확인, 회차 재연결 멱등성, 대본 변경 거부, 업로드 원장 보호 및 한도 누적을 확인했다.
- 서버 PostgreSQL CI는 격리된 Postgres 17 서비스만 사용하며 운영 자격 증명을 받지 않는다.

## 배포 선행 조건

sql/caption_recovery_v1.sql의 두 함수를 먼저 배포해야 한다. 미배포 상태에서는 production claim이 유료 호출 전에 중단된다. 신규 자막을 적용하려면 고정 폰트를 설치하고 caption-layout-v1 full 품질 파일럿을 검수해야 한다. 개발 완료와 운영 승인·오늘 회차 복구는 구분한다.

## 남은 검증

- 원격 CI의 PostgreSQL 17 통합·동시 예약 검사는 통과했다. 후속 코드 변경 시 재검증한다.
- 실제 4개 이미지/6개 TTS 영상과 Shorts 앱 UI 검수.
- 당시 원본이 저장되지 않은 10/17 회차는 별도 복구 절차 적용 필요.
- 과거 대본만으로 미디어 전체를 재조립하는 범용 복구 CLI는 이번 구현에 포함하지 않았다.

구현·운영 안내: [caption_recovery_v1.md](caption_recovery_v1.md).
