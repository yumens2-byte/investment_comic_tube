# 운영 안정성 개발 및 검증 v1 — 2026-09-30

상태: 개발·회귀 검증 완료. 2026-09-30 운영 EDT_ONLY_UNIVERSE에 제어 schema/RPC와 private pipeline-assets bucket 적용 및 service_role 권한 smoke 검증 완료. 코드 PR 운영 반영 진행 중이며 유료 API/업로드 베타는 마스터 실행 후 확인한다.

## 구현

- `OPERATIONAL_SAFETY_ENABLED=true`에서 시장 관측일/신선도/변동률 원본 대조와 facts 토큰을 적용한다. 수치·단위·방향은 프로그램에서 조립하고 미등록 수치/방향 문장은 차단한다. 이미지/TTS 전 검증, snapshot/story hash 변경 차단.
- KST 채널/날짜/모드의 DB 슬롯·lease/fence·호출 예약을 RPC 트랜잭션으로 관리한다. 같은 날짜 rerun·입력 변경에도 한도가 누적되며 SDK 재시도 1, AFC 비활성화. 실패 제어 기록은 폴백으로 삼키지 않는다.
- 성공 대본/이미지/음성/영상은 private `pipeline-assets` Storage에 checksum과 입력 hash로 보존·재사용한다. 파일럿은 episodes/YouTube 쓰기0, 제어 DB 쓰기 있음.
- 업로드 세션 생성 전 의도를 기록하고 세션 URI 암호화 저장 후 바이트 전송. 완료 원장 먼저 저장, episodes 실패 재실행은 DB만 복구. 연결/완료 응답 유실은 같은 세션 조회로 복구하고 불명/404는 신규 업로드 차단한다.
- 조회·video_id 연결·운영자 미업로드 확인·원래 슬롯 resume·유한 추가 생성 시도 CLI 제공. 결함 캐시 삭제는 invalidate-asset로 명시 처리하며 생성 횟수는 유지한다. 기본 절차는 자동 한도 초기화를 하지 않는다.
- 기존 경로는 flag 미설정/false에서 유지한다. 이를 활성화된 안전성으로 보고하지 않는다.

## 상세설계에서 확정/조정한 사항

논리적 슬롯/시도/업로드 원장을 한 `pipeline_slots` 테이블의 별도 JSONB 영역에 묶었다. RPC의 행 잠금으로 원자성을 유지하고 attempts 배열에 호출 예약·운영자 복구/추가 시도 근거를 보존한다. 기존 episodes 허용 상태는 바꾸지 않는다.
거래일 기본 달력은 고정 `exchange-calendars==4.13.2`의 XNYS다. 금리·선물·달러의 공급자별 전문 달력을 별도로 검증한 것으로 보지 않는다. 관측일 최신/종가 구분과 1거래일 허용 정책을 적용하고, 공급자별 실데이터 fixture 검증은 활성화 전 베타에서 필요하다.
자동 이미지 재생성 루프를 새로 늘리지 않았다. 이미지 최대2회 상한은 rerun 포함이며, 현재 실행은 실패 슬롯에서 한 번 호출 후 중단한다. TTS와 대본은 기존 1회 수정 재시도를 한도 안에서 유지한다.
수치 없는 자유 서술에 대한 완전한 의미 검증이나 원본 공급자의 수치 정확성 보증은 제공하지 않는다.

## 활성화 전 준비

1. 운영 schema를 읽어 호환성 확인하고 `sql/operational_safety_v1.sql` 정의를 적용한다. 이 파일은 검토용 배포 SQL이며 2026-09-30 운영에 적용 완료했다.
2. private `pipeline-assets` bucket 생성. public bucket은 preflight에서 거부한다. service_role 서버 접근만 허용한다.
3. 기본 암호화는 기존 SUPABASE_SERVICE_ROLE_KEY에서 채널별·용도별 HMAC-SHA256 키를 서버에서 도출한다. 추가 Secret 등록은 필수가 아니다. 선택적으로 UPLOAD_SESSION_FERNET_KEY를 설정할 수 있으나, 세션이 남아 있는 동안 service_role 키·채널 식별자·명시 암호화 키를 바꾸면 복호화에 영향이 있으므로 먼저 복구를 완료한다. 키 값을 로그·문서에 노출하지 않는다.
4. 채널 기본값은 EDT_UNIVERSE_INVEST_AREA99다. 다른 채널이면 Actions Variable PIPELINE_CHANNEL_KEY를 고정 식별자로 설정한다. 하나의 채널에 실행마다 다른 값을 쓰지 않는다.
5. 운영 워크플로는 기본값 true로 활성화한다. OPERATIONAL_SAFETY_ENABLED=false 변수는 비활성화하므로 활성화를 기대한다면 해당 변수가 없는지 또는 true인지 확인한다. 구버전 실행이 진행 중이지 않은지 먼저 확인한다. config/runtime healthcheck와 저비용 파일럿으로 확인한다.

## 복구 CLI

저장소 루트에서 `PYTHONPATH=.`로 실행한다. DB/YouTube Secret을 서버 환경에 제공하되 shell history에 값을 입력하지 않는다.

```bash
python scripts/operational_recovery.py inspect --day 2026-09-30 --hero EDT
python scripts/operational_recovery.py resume --day 2026-09-30 --hero EDT
python scripts/operational_recovery.py attach-video --day 2026-09-30 --hero EDT --video-id VIDEO_ID --reason 'Studio 영상과 회차 검토 완료'
python scripts/operational_recovery.py confirm-absent --day 2026-09-30 --hero EDT --verified-absent --reason 'Studio 및 실행 증거 검토 후 미업로드 확인'
python scripts/operational_recovery.py invalidate-asset --day 2026-09-30 --hero EDT --stage image --asset 0 --reason '해당 이미지 품질검증 실패 확인'
python scripts/operational_recovery.py grant-extra --day 2026-09-30 --hero EDT --stage image --asset 0 --extra 1 --reason '원인 수정 후 한 번 추가 검증'
```

attach-video는 API에서 private·제목 일치도 확인하나 영상 내용 일치는 운영자가 확인한다. API 조회 권한 부족이면 연결하지 않고 실패한다. confirm-absent는 일일 슬롯의 원래 자산으로 단 한 번 신규 세션 시작을 허용한다. grant-extra는 단계별 일일 추가 총2회까지만 허용하고 기본 counts를 지우지 않는다. 다른 캐릭터 preview의 최초 hero와 다르면 해당 날짜 제어 슬롯의 hero로 조회/추가 시도를 진행한다.
원장 결과 불명 시 자동 새 세션을 생성하지 않는다. 예전 날짜 복구는 날짜를 지정한 resume CLI를 사용한다. unknown 원장을 무시하는 구버전으로 flag를 되돌리는 운영 롤백을 하지 않는다.

## 테스트

Python 단위·회귀 **342건 통과**(전체 discovery 349건 중 PostgreSQL 전용7건은 별도 실행). 로컬 PostgreSQL 통합 **7건 통과**. 컴파일과 git diff 공백 검사 통과.

신규 Python: 시장 단위/반올림·부호/미등록 숫자·null·휴일·지연, 호출 예약 실패 시 API0, AFC 차단, 세션 영속 저장 선행, 완료 응답 유실 재조회,404 차단, bounded 오류, uploaded DB-only 복구, 잘못된 암호화 키 선차단.
로컬 PostgreSQL:8개 동시 예약 중2개만 허용, rerun/lease 인수 한도 유지·stale worker 차단, uploaded 상태/파일 보호, 이전 날짜 unknown 차단, preview 단일 호출·업로드0, public 권한 차단, 유한 추가 시도·미업로드 확인 기록.
운영 DB schema/RPC 권한·호출 상한·파일럿 업로드 차단은 트랜잭션 롤백 smoke로 검증했다. Storage는 private metadata 생성·설정까지 확인했으며 runner에서 실제 업로드/다운로드와 공급자 API·YouTube 회복은 운영베타 전이다. 마스터가 Actions 파일럿과 운영베타를 실행한다.

## 운영 반영 확인
- 프로젝트: EDT_ONLY_UNIVERSE (hdjhehtdejmtobwwkpgf), 기존 episodes/step_runs 구조 변경 없음.
- RPC v1, pipeline_slots RLS, anon 테이블 조회 차단, authenticated RPC 실행 차단, service_role 실행 허용 확인.
- pipeline-assets: public=false, 파일 상한50MiB.
- 서버 권한 smoke: 슬롯 선점→이미지1회 허용→2회차 한도 차단→preview upload 차단. 전부 롤백, control row0 확인.
- Security advisor는 INFO RLS-no-policy만 보고했다. 서버 전용 테이블에서 공개 정책을 두지 않는 설계에 해당하며 별도 공개 권한을 추가하지 않는다.
- 워크플로 기본 활성 true와 고정 채널 key를 코드에 반영; 이미 존재하는 Repository Variable이 false면 우선하므로 Actions healthcheck/파일럿 로그에서 활성 상태를 확인한다.
