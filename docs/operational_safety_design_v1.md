# EDT/GOC 운영 안정성 상세설계 v1

작성: 2026-09-30 KST. 상태: 상세설계 완료, 개발·DB 적용·운영 테스트 전.
기준: main 04cf5e261137252c7c6719688f7c05c554a309e3의 로컬 코드 검토.
마스터 보고: 9월 30일 EDT 운영 정상 수행. GOC full 운영 검수는 대기.

## 1. 채택 범위와 유지 계약

채택: 시장 수치 검증, 업로드 재시도 안정성, 무한 재생성 방지.
비용 금액 모니터링·자동 공개·성과 분석·이미지 카논 자동 판정은 이번 개발 범위에서 제외한다.
매일 KST 10:00 날짜 기반 EDT/GOC 단일 선택, private 업로드, GOC Kore, 기존 표준 운영 모델과 저비용 파일럿 모델을 유지한다.
파일럿의 episodes 쓰기·YouTube 업로드 0건 계약도 유지한다. 단, 재생성 방지용 제어 기록은 별도 테이블에 필수 저장한다. 따라서 새 버전 파일럿은 'episodes 쓰기 0건, 제어 DB 쓰기 있음'으로 로그·매뉴얼을 정정한다.

## 2. 코드 검토 결과

| 위치 | 현재 동작 | 설계 변경 |
|---|---|---|
| collector.py / market_sources.py | 값·source만 저장, 원본 관측일 누락 | 원본 값·관측일·단위·종류·출처 계약 추가 |
| director.py / story.py / goc.py | 모델 문장을 형식·자막 기준으로 검사 | 수치와 방향 검증을 미디어 생성 전에 적용 |
| drive_manager.py | 당일 검사 UTC 날짜, 실패 회차 삭제 재사용 | KST 슬롯 영속 잠금; 업로드 의심 회차 삭제 금지 |
| publisher.py | resumable=True, next_chunk 반복, 세션 영속 저장 없음 | 세션 저장 후 바이트 전송; 결과 불명 시 재업로드 금지 |
| main.py | 업로드 후 episodes 갱신, 예외 시 failed 덮어쓰기 | 업로드 원장 우선 기록, DB 복구 경로 분리 |
| story/goc/tts 및 SDK | 일부 재시도 제한 존재, 재실행 시 초기화 | API 호출 전 원자적 누적 예약, SDK 내부 재시도 1회로 통일 |

현재 무한 API 반복이 실증된 것은 아니다. 프로세스 내부 제한이 재실행·SDK 내부 동작을 포함하는 영속 상한이 아니라는 점을 보완한다.

## 3. 시장 수치 검증

### 3.1 원본 계약

각 지표에 close_raw, prev_close_raw(Decimal 문자열), close, change_pct, source, source_symbol, instrument_kind(index/yield/future), unit, observed_date, prev_observed_date, collected_at_utc, observation_timezone, raw_payload_hash, schema_version을 둔다. 장중값 여부는 observation_kind=latest/daily_close/unknown으로 구분한다. collected_at이나 episodes.market_as_of를 관측일로 대체하지 않는다.
원본 응답은 인증값을 제거한 최소 데이터만 보관한다. 날짜를 제공하지 않는 폴백은 숫자를 복구해도 날짜 검증 통과로 처리하지 않는다. 필수 지표의 날짜 미상은 실패; 선택 지표는 서사·제목·설명에서 제외한다.
변동률은 같은 상품·같은 소스의 연속 관측값으로 계산한다. 이전 값이 없거나 0이면 null. 금리 수준(%)과 금리 변화율(%)과 금리 차이(%p/bp)를 구분한다. 수치 검증은 원본과 출력의 일치 검증이며 원본 공급자의 정확성을 보증하지 않는다.

### 3.2 신선도

미국 휴일·주말을 고려한 버전 고정 거래일 달력을 사용한다. 지수/금리는 최신 완료 세션 기준, 선물/달러지수는 각각 공급자 관측일 정책을 사용한다. 미래 관측일은 오류; 완료 세션보다 뒤인 당일 장중 관측은 latest로 표시하고 종가로 표현하지 않는다. 기본 허용 지연은 기대 완료 세션 대비 1 거래일이며 지연 데이터는 날짜를 표시한다. 2 거래일 이상 지연된 필수값은 중단한다. 달력·소스별 정책을 확정하는 공급자 fixture 검증은 개발 착수의 첫 단계다.

### 3.3 생성 문장 계약

모델에 자유로운 숫자 작성을 맡기지 않는다. 검증된 facts 사전에서 fact_id를 선택하고 텍스트에는 {{FACT:SPX.change_pct}} 같은 토큰을 사용한다. 프로그램이 Decimal ROUND_HALF_UP 기반 규격 문자열을 대입한다. facts에는 값·단위·관측일·상승/하락/보합 방향을 함께 보관한다.
토큰 밖 숫자/한글 숫자 수치 주장, 미등록 fact, 단위 변조, 상승/하락 반대 표현, null을 0으로 표현한 문장은 거부한다. 회차 번호·6비트 식별자 등 비시장 숫자는 출력 필드별 명시 허용 목록으로 처리한다. 모호한 자유 서술을 완전히 의미 분석한다고 주장하지 않는다. 시장 사실 문장은 프로그램 템플릿으로 조립하고 모델은 비수치 캐릭터 서사만 작성한다.
첫 검증 실패 후 대본 재생성은 1회만 허용한다. EDT는 검증된 규칙 템플릿 폴백을 허용하며 같은 검사를 통과해야 한다. GOC는 2회 모두 실패하면 중단한다. 이미지·TTS 호출 전 통과 필수, 렌더/업로드 직전 대본·스냅샷 hash 동일성을 재검사한다. 제목·설명도 같은 formatter를 사용한다.
검증 보고서: indicator/fact_id/expected/actual/reason/source/observed_date/snapshot_hash. 개인정보·키·원본 전체 응답은 로그에 넣지 않는다.

## 4. 영속 제어 데이터 설계

기존 episodes 상태 열의 허용값을 바꾸지 않고 아래 별도 테이블을 추가한다. 실제 타입·제약은 운영 스키마 읽기 검증 후 migration으로 확정한다.

| 테이블 | 키와 주요 필드 | 보존/역할 |
|---|---|---|
| pipeline_slots | UNIQUE(channel_key,kst_date,mode_group); hero, policy_version, owner, lease_until, fence_token, state, episode_id | production 날짜 슬롯은 hero와 무관하게 하나; 파일럿은 preview 그룹 하나 |
| generation_attempts | UNIQUE(slot_id,stage,asset_key,ordinal); input_hash, model, status, reserved_at, completed_at, asset_uri, checksum, run_id | 호출 예약과 누적 횟수; 재실행/회차 삭제로 초기화 금지 |
| upload_intents | UNIQUE(production slot_id); episode_id, video_sha256, size, state, encrypted_session_uri, bytes_confirmed, youtube_video_id, error_code | 생성/완료/불명 결과 원장; 자동 삭제 없음 |

서버 전용 접근, exposed schema의 RLS 활성화, anon/authenticated 직접 접근 금지. RPC는 SECURITY INVOKER 기본, 서버 service_role만 실행 가능하도록 PUBLIC 실행 권한을 회수한다. API에 노출될 테이블·함수 grant를 migration에서 명시한다. 세션 URL은 비밀값으로 취급하고 암호화 저장, 로그·Actions 아티팩트에 제외한다.
claim_slot/reserve_generation/transition_upload RPC는 트랜잭션에서 행 잠금, 고유 제약, fence_token 비교를 함께 사용한다. 일일 lease 10분, 60초 heartbeat; 인수 시 fence_token 증가. 이전 worker는 이후 변경 및 API 호출 전에 토큰 재검증한다. 호출 예약 직후의 프로세스 중단도 횟수를 소비하며 자동 환불하지 않는다. 유료 호출이 전송됐는지 확실하지 않기 때문이다.
DB 사용 불가이면 유료 API·업로드 전에 중단한다. step_runs는 계속 보조 로그이고 제어 테이블을 대신하지 않는다. 신뢰된 단위테스트만 메모리 저장소를 주입한다.

## 5. 무한 재생성 방지

### 5.1 하드 상한 초안(최초 호출 포함)

| 모드 | 대본 | 이미지 | TTS | 렌더 |
|---|---|---|---|---|
| 운영/full 품질 파일럿 | 전체 최대 4회; EDT polish 최대1 + storyboard 최대2, GOC 최대2 | 슬롯당 최대2회, 4슬롯 총8회 | 비트당 최대2회, 6비트 총12회 | 동일 입력 최대2회 |
| 저비용 파일럿 | 0회 | 1회 | 1회 | 1회 |

production은 (채널,KST 날짜) 누적, 모든 저비용 preview는 동일 날짜 preview 슬롯을 EDT/GOC 모드가 공유한다. 이름·run_id·모델·프롬프트 hash를 바꿔도 같은 슬롯 횟수를 초기화하지 않는다. full 품질 파일럿은 별도 full-preview 그룹으로 운영과 동일 상한, 기본 Actions 선택으로 추가하지 않는다.
수치 재검증 실패 재생성은 대본 한도 안에서만 수행한다. 성공 자산은 checksum과 입력 hash·검증 보고서가 일치하면 재사용한다. 입력이 달라지면 이전 성공물은 재사용하지 않되 누적 횟수는 유지한다. 자산은 private 영속 저장소에 저장해 새 runner에서도 재사용한다. 로컬 파일 또는 14일 Actions artifact만을 자동 복구 저장소로 삼지 않는다.
SDK 자동 재시도 attempts=1; 외부 래퍼가 예약 후 호출한다. 자동 함수 호출도 비활성화하여 숨은 모델 호출을 막는다. quota/인증/유효하지 않은 모델 오류는 즉시 중단; 일시적 timeout/5xx·빈 응답·허용된 품질 실패만 남은 한도에서 재시도한다. 생성 오류를 다른 모델 호출로 우회하지 않는다.
상한 초과 시 generation_limit_reached로 실패하며 다음 단계 호출 0건. 날짜 자동 증가로 미완료 회차를 재개하지 않는다. 다음날은 신규 시장 슬롯으로 시작하되 이전 업로드 결과 불명은 채널 단위에서 해소 전 신규 업로드를 막는다.
운영자가 추가 시도를 요청하는 경우 별도 명시적 override 기록(사유/승인자/추가 횟수/만료)을 요구한다. 단순 Actions rerun에는 override 권한이 없다. 무제한 override 값은 허용하지 않는다.

## 6. 업로드 재시도 안정성

### 6.1 상태와 순서

1. KST 슬롯 선점 및 기존 published*·upload 원장 확인. 기존 회차를 삭제하기 전에 반드시 업로드 원장 조회.
2. 미디어 생성·검증 후 private 저장소에 MP4와 sha256/size/metadata hash 저장. upload_intent=prepared를 필수 기록.
3. 상태를 session_creating으로 선기록한 뒤 resumable 세션 생성 요청. 세션 URI를 session_ready로 영속 저장한 다음 최초 바이트를 전송한다. 세션 생성 응답 유실 시 즉시 새 세션을 반복 생성하지 않고 session_unknown으로 보류한다.
4. 동일 세션에 bounded retry로 전송. 완료 응답의 video_id는 thumbnail/playlist보다 먼저 원장 uploaded에 저장한다. 이후 episodes.published*와 youtube_video_id를 복구 가능한 projection으로 갱신한다.
5. episodes 갱신 실패 시 원장은 uploaded 그대로, 다음 실행은 DB 동기화만 수행한다. main의 일반 except가 업로드 의심/완료 상태를 failed로 덮어쓰거나 reclaim 삭제하도록 하지 않는다.
6. 썸네일·재생목록 실패는 업로드 성공과 분리한다. 재실행은 videos.insert를 재호출하지 않고 부가 단계만 재시도하며 재생목록 중복 추가도 조회 후 수행한다.

### 6.2 장애별 정책

| 장애 | 다음 실행 동작 |
|---|---|
| byte 전송 전 검증 실패 | 업로드 0건; 기존 검증된 자산만 재사용 |
| 연결 끊김/5xx | 저장된 세션 상태 조회, 확인된 byte 이후부터 같은 파일 재개 |
| 완료 응답 유실 / uploaded DB 기록 실패 | 세션 상태 조회로 완료 video_id 회수 후 DB 동기화 |
| 세션 URL 저장 실패 | byte 전송 금지; 불명 상태 보존 |
| 세션 만료/404 또는 조회 불가 | upload_unknown; 자동 신규 videos.insert 금지, 운영자 채널 검토 |
| 이미 video_id 있음 | 업로드 호출 0건; episodes/부가 단계 복구만 |
| stale worker/동시 실행 | fence mismatch 중단; 인수 worker는 기존 세션 조회 |

YouTube가 임의의 idempotency key를 받아 정확히 한 번 생성을 보장한다고 가정하지 않는다. 세션 재개·원장·불명 상태 정지로 중복 위험을 줄인다. 세션 결과를 회수할 수 없는 경우 채널 검토 후 video_id 연결 또는 '미업로드 확인' 복구 결정을 명시적으로 기록해야 한다. search 결과 없음만으로 미업로드 확정하지 않는다.
세션 상태 조회/전송 일시 오류는 RPC에 영속 누적하여 동일 실패 지점 최대3회(최초 포함), 정상 byte 진행 시 해당 지점 카운터 종료. 업로드 단계 전체 누적 wall time 15분, 무진행 120초 상한. 큰 영상은 사전 파일 상한과 이 제한 내 성공 여부를 운영 fixture에서 조정한다. 외부 반환 progress가 멈추는 next_chunk 무한 반복을 제거한다.

## 7. 구현 파일과 개발 순서

1. src/market_facts.py 신규: 원본 계약, Decimal formatter, 날짜 정책, facts 토큰·검증 보고서. collector.py/market_sources.py/story.py/goc.py/director.py/publisher.py 연결.
2. 제어 schema migration 및 RPC; src/pipeline_control.py 신규: KST 슬롯, 예약, lease/fence. drive_manager.py reclaim와 UTC 검사 보완.
3. src/generation_guard.py 신규: SDK 호출 단일 경로, API별 한도; image_generator.py/tts.py/director.py/story.py/goc.py 전체 적용. 호출 수를 로그 추정이 아닌 영속 예약 기준으로 검증.
4. src/upload_state.py 신규 및 publisher.py: resumable 세션 분리, 원장, 제한된 resume. main.py 업로드 전후 상태·예외 처리 수정.
5. private 자산 보존·복구, healthcheck.py 제어 schema/RPC 읽기 검증, 운영자 복구 CLI(조회 / video_id 연결 / 미업로드 확인). Secrets 추가 필요 여부는 저장소 암호화 방식에 맞춰 문서화.
6. tests와 설계/운영자 매뉴얼 업데이트. Actions 기본 모드는 그대로 유지하고 회귀 테스트 통과 뒤 PR 작성. 이 문서 작성으로 개발 또는 운영 배포를 수행한 것은 아니다.

## 8. 단위·통합·파일럿 수용 기준

| 영역 | 필수 시나리오 | 합격 기준 |
|---|---|---|
| 시장 | 정상/반올림 경계/NaN/단위%p 혼동/없는 전일값/부호 반전/미등록 숫자·한글 수치 | 오류 문장이 이미지·TTS·업로드까지 진행하지 않음 |
| 날짜 | 미국 휴일·주말·DST/KST 자정/지연·미래·미상 날짜 | 수집시각과 관측일 분리, 정책 fixture대로 허용/차단 |
| 한도 | 프로세스 중단/같은날 rerun/모델·문구 변경/동시 claim/SDK hidden retry | 일일 상한 불변, 초과 API 호출0, 성공 자산 재사용 |
| 업로드 | 완료 후 DB 장애/완료 응답 유실/세션 저장 실패/404/무진행/부가 단계 실패 | 재실행 신규 insert0 또는 불명 보류; 원장 삭제 없음 |
| 원자성 | 두 worker reserve/lease 인수/DB 단절 | 한도 초과 예약 불가, 오래된 worker 제어 변경 거부 |
| 회귀 | EDT/GOC, private, Kore, 10시 스케줄, 저비용 preview | 기존 계약 유지; 파일럿 episodes/YouTube 0건 |

테스트는 무과금 API mock + 로컬 DB 트랜잭션 통합으로 먼저 수행한다. 실제 API 제한 확인은 저비용 파일럿 1회만, full 영상 전체 재생성 대신 업로드 복구 테스트는 사전 준비된 소형 영상으로 격리 테스트 채널에서 수행한다. 마스터가 Actions를 실행한다. 운영 채널에 의도적 중복 업로드 장애를 주입하지 않는다.
배포 전: 기존 회차/DB 제약·권한 확인, 보조 테이블 선배포, 원장 없이 실행하던 진행 중 업로드가 없는지 확인, 새 코드 적용. 롤백은 생성 전 기능 비활성화에 한정하며 uploaded/unknown 원장을 무시하는 옛 upload 코드로 되돌리지 않는다.

## 9. 근거

- YouTube 공식 resumable 프로토콜: https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol — 세션 URI 보존, 중단 상태 조회, 308 Range 기반 재개, 완료 응답 회수. 세션은 만료될 수 있다.
- Supabase DB 함수: https://supabase.com/docs/guides/database/functions — 원자적 제어 함수와 서버 접근 설계의 참고. 운영 schema/RPC는 아직 생성하지 않았다.
