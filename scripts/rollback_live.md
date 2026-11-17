# CAST-LINE live 첫 실행 롤백 절차 v1.0 (2026-11-17)

부록 V·W 계승 — live 승격 실패 시 마스터 실행 절차. 목표: **5분 내 원복, 발행물·원장 무손상 회수**.

## 0. 원칙

- live 실패의 유형은 2가지다 — 발행 **전** 실패(generate·render)와 발행 **후** 실패(업로드·원장).
- 원복의 축은 `CAST_EXECUTION_MODE` 1건이다. Variable만 `shadow`로 되돌리면 publish job은
  게이트(`vars.CAST_EXECUTION_MODE == 'live'`)에서 실행되지 않는다 — 코드 롤백은 원칙적으로 불필요.
- fal 생성 비용은 발생 시점에 지출 확정 — 롤백으로 환불되지 않는다(월 원장에 기록 유지).

## 1. 유형 A — 발행 전 실패 (generate·render 단계)

발행물이 없으므로 정리 대상도 없다.

1. Variable 원복: Settings → Secrets and variables → Actions → Variables →
   `CAST_EXECUTION_MODE` = `shadow` (삭제 말고 값 변경 — dry_run은 원장 모드 추적이 끊긴다)
2. 실패 run의 artifact 보존 확인: `cast-cuts-<run_id>`(컷 mp4·generate_ledger.json) —
   QC 재소요 방지용. 90일 보존 정책 내에서 잔존
3. 원장 반영: `/hero-shorts/costs.json`에 실패 회차의 지출분(완주된 컷만) 기록
4. 실패 원인이 코드 결함이면: 이슈 등록 → 핫픽스 → 패키지 재배포 → **shadow에서 재검증 후** live 재시도
   (live로 바로 재시도 금지 — shadow 1회 통과가 재승격 조건)

## 2. 유형 B — 발행 후 실패 (publish 단계, 발행물 존재)

### 2-1. YouTube 업로드는 됐는데 후단계 실패 (원장 기록 실패·리포트 실패)

1. Variable 원복: `CAST_EXECUTION_MODE` = `shadow`
2. 발행물 판정: private 기본(발행 메타데이터 `privacy: private`) — 공개 전이면 **삭제하지 않고 유지**,
   다음 live 재시도 시 `has_published_today_line('cast')` 중복 게이트가 당일 재발행을 차단한다
3. 재발행이 필요하면: 기존 업로드분을 YouTube Studio에서 **수동 삭제 후** 재실행
   (삭제 전 episode episode_no·video_id를 기록 — Supabase episodes 원장과 대조)
4. Supabase episodes에 부분 기록됐으면: `status` 확인 후 미완료 레코드는 수동 정리
   (확인 쿼리: `select episode_no, line, status from episodes where line='cast' order by created_at desc limit 5;`)

### 2-2. 렌더 합성 자체 실패로 발행물이 손상됐을 가능성

1. Variable 원복: `CAST_EXECUTION_MODE` = `shadow`
2. artifact의 `cut_XX.mp4` 7컷 무결성 확인(재생 가능 여부) — 컷이 정상이면 결함은 합성 단계 한정,
   컷 재생성 불필요(비용 절약)
3. 재시도: shadow 1회에서 합성 성공 확인 후 live 재승격

## 3. 원복 확인 체크리스트 (순서대로)

- [ ] `CAST_EXECUTION_MODE` = `shadow` (Variables 화면에서 값 확인)
- [ ] 실패 run conclusion·실패 스텝 기록 (부록에 run_id와 함께)
- [ ] artifact `cast-cuts-<run_id>` 존재 확인
- [ ] fal 지출분 월 원장 기록 (`/hero-shorts/costs.json`)
- [ ] YouTube Studio에서 cast 라인 업로드물 유무 확인 (유형 B만)
- [ ] Supabase `episodes`의 `line='cast'` 레코드 확인 (유형 B만)
- [ ] 부록에 롤백 기록 + 재승격 조건(shadow 1회 통과) 명시

## 4. 재승격 조건

- 원인 코드 결함이면: 핫픽스 + **shadow 1회 정상종료** 후 live 재시도
- 환경 일시 장애(fal 5xx 등)면: fal 상태 확인 후 **live 바로 재시도 가능** (단, 당일 2회 한정 —
  중복 게이트가 발행을 막으므로 발행 전 실패에 한한다)
- 재승격 시에도 `tier_force=T3` 여부·예상 비용을 부록에 기록 후 실행

## 5. 절대 금지

- Variable 삭제(값 변경이 아니라) — 이력 추적이 끊긴다
- live 연속 재시도(발행 전 실패 제외) — fal 비용 중복 지출
- Supabase episodes 수동 대량 삭제 — 부분 기록만 정밀 정리
- 발행물을 private 유지 없이 바로 공개 전환 — QC 없는 공개 금지 (캐논 규칙서 준수)
