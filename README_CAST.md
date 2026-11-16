# CAST-LINE 수동 업로드 패키지 (2026-11-17)

기존 파이프라인에 영향 없는 신규 파일만 모은 전용 패키지다. 기존 파일 1건도 수정하지 않는다(기존 소스 무변경 확인: 파일럿테스트 기록).

## 구성

| 경로 | 내용 | 신규/기존 |
|---|---|---|
| src/cast/ | CAST-LINE 모듈 8개 + 캐논·REF 스냅샷 2개 + 테스트 4개 | 전부 신규 |
| supabase/migrations/003_add_line.sql | episodes.line 컬럼 (마스터 적용 필요) | 신규 |
| .github/workflows/cast_t3.yml | T3 주 1회 워크플로우 (토 KST 09:43) | 신규 |
| .github/workflows/cast_watchdog.yml | 능동조회형 워치독 (토 KST 11:23) | 신규 |
| scripts/cast_setup_checklist.md | Secrets 6종·Variables·003 적용 체크리스트 | 신규 |

## 적용 절차 (기존 저장소 루트에서)

1. 이 패키지의 cast-line/ 내용을 저장소 루트에 복사 (기존 파일 덮어쓰기 없음)
2. Supabase 003 마이그레이션 적용 (SQL Editor) — 체크리스트 §3 확인 쿼리로 검증
3. Secrets 신규 3종 등록: FAL_AI_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — 체크리스트 §1
4. Variable: CAST_EXECUTION_MODE=dry_run — 체크리스트 §2
5. cast_t3.yml 수동 dispatch(mode=auto, dry_run) → plan artifact 확인
6. shadow 전환(Variable 값 변경) → 토요일 cron 자동 실행
7. 3주 후 live 승격 승인

## 기존 파이프라인 영향도 (파일럿테스트 A/B 실측 — 2026-11-17)

| 대상 | 결과 |
|---|---|
| 기존 main (원본, 신규 파일 없음) | 57 failed, 406 passed, 9 skipped, 71 subtests |
| cast-line (신규 파일 추가) | **57 failed, 406 passed, 9 skipped, 71 subtests — 완전히 동일** |

- 실패 57건은 origin/main 원본 상태에서도 동일한 기존 환경 의존 실패(샌드박스 자격증명·외부 API 미설정) — 신규 파일과 무관하며 **추가 전후 0건 차이**다.
- 기존 진입점 import 무결성: main.py·collector·publisher·renderer·validation 정상 로드 확인 (cast 모듈과 공존)
- CAST 자체 테스트: 29건 전부 통과
- 충돌 자원 분리: 스케줄(토 09:43 vs 일간 10:17), concurrency 그룹, 원장(line 컬럼), 중복게이트(라인별), 채널키 — 5건 전부 분리 설계 반영
