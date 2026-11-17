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
3. 원장 대사: `cast_runs.cost_usd`(제출 시 선기록된 추정액)를 fal 콘솔 청구액과 대조 (DN2026_0114)
4. 실패 원인이 코드 결함이면: 이슈 등록 → 핫픽스 → 패키지 재배포 → **shadow에서 재검증 후** live 재시도
   (live로 바로 재시도 금지 — shadow 1회 통과가 재승격 조건)

## 2. 유형 B — 발행 후 실패 (publish 단계, 발행물 존재)

### 2-1. YouTube 업로드는 됐는데 후단계 실패 (원장 기록 실패·리포트 실패)

1. Variable 원복: `CAST_EXECUTION_MODE` = `shadow`
2. 발행물 판정: private 기본(발행 메타데이터 `privacy: private`) — 공개 전이면 **삭제하지 않고 유지**,
   `cast_runs`의 해당 행이 `uploading`(또는 `published`)으로 남아 같은 KST 날짜 재발행이 차단된다
   (업로드 중 예외·video_id 없음도 실재 불명이므로 `uploading` 유지 — DN2026_0114 fail-closed)
3. video_id 대사: artifact `cast-publish-<run_id>`의 `publish_result.json`(status `uploaded_unmarked`면
   업로드 성공·원장 미반영) 또는 YouTube Studio에서 실재 확인
4. 원장 수동 정정 (확인 쿼리: `select run_id, run_date_kst, status, youtube_video_id, error, cost_usd
   from cast_runs order by created_at desc limit 5;`)
   - 업로드 실재: `update cast_runs set status='published', youtube_video_id='<id>', episode_no=<n>,
     updated_at=now() where run_id='<run_id>';` (n = 업로드된 영상 제목의 `Ep.C<n>` 번호)
   - 업로드 부재 확인 후 재발행: `update cast_runs set status='upload_failed', updated_at=now() where run_id='<run_id>';`
     → 잠금 해제. 재발행 경로 2가지:
     · 같은 run에서 **Re-run failed jobs**(publish만 재실행) — 컷 artifact 재사용, fal 비용 0
     · 컷 artifact가 없거나 손상이면 새 dispatch(새 run_id) — fal 재생성 비용 발생(T3 약 $2.52)

### 2-2. 렌더 합성 자체 실패로 발행물이 손상됐을 가능성

1. Variable 원복: `CAST_EXECUTION_MODE` = `shadow`
2. artifact의 `cut_XX.mp4` 7컷 무결성 확인(재생 가능 여부) — 컷이 정상이면 결함은 합성 단계 한정,
   컷 재생성 불필요(비용 절약)
3. 재시도: shadow 1회에서 합성 성공 확인 후 live 재승격

### 2-3. 재실행 주의 (DN2026_0114, R6-04)

- **Re-run all jobs 금지**: plan부터 다시 돌아 같은 run_id의 기존 누적 비용이 회당 상한에 합산된다(T3면 대개 차단).
  일부 컷만 생성된 채 실패하면 이전 완전본 컷 artifact가 overwrite될 수 있다.
- **Re-run failed jobs는 첫 시도의 모드를 그대로 쓴다**: Variable을 shadow로 바꿔도 같은 run의 publish 재실행은
  attempt 1의 `needs.plan.outputs.mode`(live)로 실행된다. live 재발행을 원하지 않으면 재실행하지 않는다.
- **업로드 착수 전 실패로 `uploading`이 남은 경우**(claim 직후 회차 번호 조회·메타데이터 실패, YouTube 서비스 미구성 등):
  publish_result.json이 없거나 `uploaded_unmarked`가 아니고 YouTube Studio에 영상이 없으면
  `update cast_runs set status='upload_failed', updated_at=now() where run_id='<run_id>';` 로 잠금을 해제한다.

## 3. 원복 확인 체크리스트 (순서대로)

- [ ] `CAST_EXECUTION_MODE` = `shadow` (Variables 화면에서 값 확인)
- [ ] 실패 run conclusion·실패 스텝 기록 (부록에 run_id와 함께)
- [ ] artifact `cast-cuts-<run_id>` 존재 확인
- [ ] fal 지출분 대사 (`cast_runs.cost_usd` ↔ fal 청구)
- [ ] YouTube Studio에서 cast 라인 업로드물 유무 확인 (유형 B만)
- [ ] Supabase `cast_runs` 해당 run_id 상태 확인·정정 (유형 B만)
- [ ] 부록에 롤백 기록 + 재승격 조건(shadow 1회 통과) 명시

## 4. 재승격 조건

- 원인 코드 결함이면: 핫픽스 + **shadow 1회 정상종료** 후 live 재시도
- 환경 일시 장애(fal 5xx 등)면: fal 상태 확인 후 **live 바로 재시도 가능** (단, 당일 2회 한정 —
  중복 게이트가 발행을 막으므로 발행 전 실패에 한한다)
- 재승격 시에도 `tier_force=T3` 여부·예상 비용을 부록에 기록 후 실행

## 5. 절대 금지

- Variable 삭제(값 변경이 아니라) — 이력 추적이 끊긴다
- live 연속 재시도(발행 전 실패 제외) — fal 비용 중복 지출
- Supabase `cast_runs` 행 삭제 — 상태 정정(update)만 한다(비용 이력 보존)
- 발행물을 private 유지 없이 바로 공개 전환 — QC 없는 공개 금지 (캐논 규칙서 준수)
