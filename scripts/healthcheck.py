"""배포 반영 상태 및 운영 건전성 자동 점검.

두 가지 모드:
  --mode config  : 배포가 의도대로 반영됐는지 정적 검증 (API 호출 0, 비용 0)
                   cron / privacy / 코드 상수 / 모듈 계약 / DB 스키마
  --mode runtime : 최근 회차가 실제로 정상 발행됐는지 검증 (API 호출 0, 비용 0)
                   DB 기록만 조회하므로 Gemini 지출에 영향 없음

실패(FAIL)가 하나라도 있으면 exit code 1 을 반환해 GitHub Actions 가 실패로 표시된다.
WARN 은 통과시키되 로그에 남긴다.

주의: Secrets 는 존재 여부만 확인하고 값은 절대 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import inspect
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "1.0.0"

WORKFLOW_PATH = Path(".github/workflows/pipeline.yml")

# 기대 설정값. 운영 정책이 바뀌면 여기만 고친다.
EXPECTED_CRON = "0 0 * * *"          # 매일 KST 09:00 (UTC 00:00)
EXPECTED_PRIVACY = "private"
EXPECTED_FIRST_EPISODE_BASE = 0      # next_ep = base + 1 이므로 0 이면 1화부터

REQUIRED_SECRETS = [
    "GEMINI_API_KEY",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
]

REQUIRED_EPISODE_COLUMNS = [
    "episode_no", "status", "villain",
    "market_snapshot", "story_state", "degraded_reason",
    "youtube_video_id", "video_path",
]

# 운영 중 실제로 쓰는 모델명. 코드와 어긋나면 배포 누락이다.
EXPECTED_MODELS = {
    "src.story": ("STORY_MODEL", "gemini-3.6-flash"),
    "src.director": ("NARRATION_MODEL", "gemini-3.6-flash"),
    "src.image_generator": ("IMAGE_MODEL", "gemini-3.1-flash-image"),
    "src.tts": ("TTS_MODEL", "gemini-3.1-flash-tts-preview"),
}

RUNTIME_STALE_HOURS = 30  # 매일 발행이므로 30시간 넘게 신규 회차가 없으면 이상

# 검증이 반드시 엄격 모드로 배포돼야 한다. 완화 모드로 배포되면
# 지표가 비어도 근거 없는 콘텐츠가 발행된다.
REQUIRED_VALIDATION_INDICATORS = ["TNX", "VIX", "NASDAQ", "SPX", "DXY"]


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, name: str, detail: str = "") -> None:
        print(f"  PASS  {name}{(' -- ' + detail) if detail else ''}")

    def fail(self, name: str, detail: str) -> None:
        print(f"  FAIL  {name} -- {detail}")
        self.failures.append(f"{name}: {detail}")

    def warn(self, name: str, detail: str) -> None:
        print(f"  WARN  {name} -- {detail}")
        self.warnings.append(f"{name}: {detail}")


def check_workflow_config(r: Report) -> None:
    print("\n[1] 워크플로우 설정")
    if not WORKFLOW_PATH.exists():
        r.fail("workflow_file", f"{WORKFLOW_PATH} 없음")
        return

    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    crons = re.findall(r"cron:\s*'([^']+)'", text)
    if not crons:
        r.fail("cron", "schedule cron 정의 없음")
    elif len(crons) > 1:
        r.fail("cron", f"cron 정의가 {len(crons)}개 -- 중복 실행 위험: {crons}")
    elif crons[0] != EXPECTED_CRON:
        r.fail("cron", f"기대 '{EXPECTED_CRON}' != 실제 '{crons[0]}'")
    else:
        r.ok("cron", f"'{crons[0]}' (매일 KST 09:00)")

    privacy = re.search(r"YOUTUBE_DEFAULT_PRIVACY:\s*(\S+)", text)
    if not privacy:
        r.fail("privacy", "YOUTUBE_DEFAULT_PRIVACY 미설정 -- 공개 발행 위험")
    elif privacy.group(1) != EXPECTED_PRIVACY:
        r.fail("privacy", f"기대 '{EXPECTED_PRIVACY}' != 실제 '{privacy.group(1)}'")
    else:
        r.ok("privacy", privacy.group(1))

    missing_wiring = [s for s in REQUIRED_SECRETS if f"secrets.{s}" not in text]
    if missing_wiring:
        r.fail("workflow_secret_wiring", f"워크플로우에 배선되지 않은 Secrets: {missing_wiring}")
    else:
        r.ok("workflow_secret_wiring", f"{len(REQUIRED_SECRETS)}종 배선 확인")


def check_code_constants(r: Report) -> None:
    print("\n[2] 코드 상수 / 모듈 계약")
    import importlib

    for module_name, (const, expected) in EXPECTED_MODELS.items():
        try:
            module = importlib.import_module(module_name)
        except Exception as e:  # noqa: BLE001
            r.fail("module_import", f"{module_name} import 실패: {type(e).__name__}: {e}")
            continue
        actual = getattr(module, const, None)
        if actual != expected:
            r.fail("model_constant", f"{module_name}.{const} 기대 '{expected}' != 실제 '{actual}'")
        else:
            r.ok("model_constant", f"{module_name}.{const}={actual}")

    # 회차 넘버링 기준값
    try:
        from src.drive_manager import DEFAULT_STATE

        base = DEFAULT_STATE.get("episode")
        if base != EXPECTED_FIRST_EPISODE_BASE:
            r.fail("episode_base", f"기대 {EXPECTED_FIRST_EPISODE_BASE} != 실제 {base} (첫 회차가 {base + 1}화로 시작됨)")
        else:
            r.ok("episode_base", f"{base} -> 첫 회차 1화")
    except Exception as e:  # noqa: BLE001
        r.fail("episode_base", f"{type(e).__name__}: {e}")

    # 입력 검증 설정 (완화 모드로 배포되면 근거 없는 콘텐츠가 나간다)
    try:
        from src.validation import REQUIRED_INDICATORS, _strict_mode

        missing = [i for i in REQUIRED_VALIDATION_INDICATORS if i not in REQUIRED_INDICATORS]
        if missing:
            r.fail("validation_indicators", f"필수 지표 검증 목록에서 누락: {missing}")
        else:
            r.ok("validation_indicators", f"{len(REQUIRED_INDICATORS)}종 필수 검증")

        if not _strict_mode():
            r.fail("validation_strict", "STRICT_VALIDATION=false -- 지표 누락에도 발행됨")
        else:
            r.ok("validation_strict", "엄격 모드")
    except Exception as e:  # noqa: BLE001
        r.fail("validation_module", f"{type(e).__name__}: {e}")

    # 폴백 체인 (1차 소스 장애 시 대체 경로가 살아있는지)
    try:
        from src.market_sources import FALLBACK_ORDER

        no_fallback = [i for i in REQUIRED_VALIDATION_INDICATORS if not FALLBACK_ORDER.get(i)]
        if no_fallback:
            r.fail("fallback_chain", f"폴백 경로 없는 필수 지표: {no_fallback}")
        else:
            r.ok("fallback_chain", f"필수 {len(REQUIRED_VALIDATION_INDICATORS)}종 모두 폴백 보유")

        # 폴백 소스 키가 하나도 없으면 이중화가 사실상 무력하다
        keys = [k for k in ("FMP_API_KEY", "ALPHAVANTAGE_API_KEY", "FRED_API_KEY")
                if os.environ.get(k)]
        if not keys:
            r.warn("fallback_keys", "폴백 API 키 미등록 -- 무인증 stooq 만 동작")
        else:
            r.ok("fallback_keys", f"등록된 폴백 소스 {len(keys)}종")
    except Exception as e:  # noqa: BLE001
        r.fail("fallback_module", f"{type(e).__name__}: {e}")

    # main 이 검증을 실제로 호출하는지 (모듈만 있고 배선 누락되는 경우 방지)
    main_src = Path("main.py")
    if main_src.exists():
        text = main_src.read_text(encoding="utf-8")
        for call in ("validate_market_data(", "validate_storyboard("):
            if call not in text:
                r.fail("validation_wiring", f"main.py 가 {call} 를 호출하지 않음")
        if "validate_market_data(" in text and "validate_storyboard(" in text:
            r.ok("validation_wiring", "main.py 배선 확인")
    else:
        r.fail("validation_wiring", "main.py 없음")

    # 함수 계약(반환 튜플 개수가 바뀌면 호출부가 조용히 깨진다)
    contracts = [
        ("src.story", "build_storyboard", ["market_data", "villain", "theme", "prev_state"]),
        ("src.image_generator", "generate_scene_images", ["script_data", "output_dir", "scenes", "count"]),
        ("src.tts", "synthesize_narrations", ["narrations", "output_dir"]),
        ("src.renderer", "render_video", ["script_data", "image_paths", "scenes"]),
    ]
    for module_name, func_name, expected_params in contracts:
        try:
            module = importlib.import_module(module_name)
            func = getattr(module, func_name)
            params = list(inspect.signature(func).parameters)
        except Exception as e:  # noqa: BLE001
            r.fail("function_contract", f"{module_name}.{func_name}: {type(e).__name__}: {e}")
            continue
        missing = [p for p in expected_params if p not in params]
        if missing:
            r.fail("function_contract", f"{module_name}.{func_name} 파라미터 누락: {missing}")
        else:
            r.ok("function_contract", f"{module_name}.{func_name}({', '.join(params)})")


def check_environment(r: Report) -> None:
    print("\n[3] 실행 환경 / Secrets 주입")
    if shutil.which("ffmpeg"):
        r.ok("ffmpeg", "PATH 확인")
    else:
        r.fail("ffmpeg", "ffmpeg 실행파일 없음 -- 렌더링 불가")

    # 한글 폰트: 없으면 자막이 전부 두부(□)로 나가 콘텐츠가 못 쓰게 된다
    try:
        from src.renderer import find_kr_font

        font = find_kr_font()
        if font:
            r.ok("kr_font", font)
        else:
            r.fail("kr_font", "한글 글리프 폰트 없음 -- 자막이 두부(□)로 렌더링됨")
    except Exception as e:  # noqa: BLE001
        r.fail("kr_font", f"{type(e).__name__}: {e}")

    if shutil.which("ffprobe"):
        r.ok("ffprobe", "PATH 확인")
    else:
        r.fail("ffprobe", "ffprobe 없음 -- 장면 길이 산출 불가")

    for secret in REQUIRED_SECRETS:
        # 값은 절대 출력하지 않고 존재/길이만 본다
        value = os.environ.get(secret, "")
        if not value:
            r.fail("secret_present", f"{secret} 비어있음")
        elif len(value) < 10:
            r.warn("secret_present", f"{secret} 값이 비정상적으로 짧음(len={len(value)})")
        else:
            r.ok("secret_present", f"{secret} (len={len(value)})")


def check_db_schema(r: Report) -> None:
    print("\n[4] DB 스키마")
    try:
        from src.db_client import get_client

        client = get_client()
        result = client.table("episodes").select(",".join(REQUIRED_EPISODE_COLUMNS)).limit(1).execute()
    except Exception as e:  # noqa: BLE001
        r.fail("db_schema", f"episodes 조회 실패(컬럼 누락 가능): {type(e).__name__}: {e}")
        return

    r.ok("db_schema", f"episodes 필수 {len(REQUIRED_EPISODE_COLUMNS)}개 컬럼 조회 성공")

    try:
        step = client.table("step_runs").select("id, episode_id, step, status").limit(1).execute()
        r.ok("db_schema", f"step_runs 조회 성공(rows={len(step.data or [])})")
    except Exception as e:  # noqa: BLE001
        r.fail("db_schema", f"step_runs 조회 실패: {type(e).__name__}: {e}")

    rows = result.data or []
    if not rows:
        r.ok("db_state", "episodes 0건 (초기화 상태 -- 다음 실행이 1화)")


def check_runtime_health(r: Report) -> None:
    print("\n[5] 최근 발행 상태")
    try:
        from src.db_client import get_client

        client = get_client()
        result = (
            client.table("episodes")
            .select("episode_no, status, youtube_video_id, degraded_reason, updated_at, villain")
            .order("episode_no", desc=True)
            .limit(5)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        r.fail("runtime_db", f"조회 실패: {type(e).__name__}: {e}")
        return

    rows = result.data or []
    if not rows:
        r.warn("runtime_latest", "발행된 회차가 없음 -- 아직 첫 실행 전이면 정상")
        return

    latest = rows[0]
    ep = latest.get("episode_no")
    status = latest.get("status")
    video_id = latest.get("youtube_video_id")
    degraded = latest.get("degraded_reason")

    if status in ("published", "published_degraded") and video_id:
        r.ok("runtime_latest", f"Ep.{ep} status={status} video_id={video_id}")
    elif status == "failed":
        r.fail("runtime_latest", f"Ep.{ep} 파이프라인 실패 (degraded_reason={degraded})")
    else:
        r.fail("runtime_latest", f"Ep.{ep} 발행 미완료 status={status} video_id={video_id}")

    if degraded:
        r.warn("runtime_quality", f"Ep.{ep} 품질 저하: {degraded}")
    else:
        r.ok("runtime_quality", f"Ep.{ep} degraded 없음")

    # 신선도: 매일 발행이므로 오래 멈춰 있으면 스케줄이 안 도는 것
    updated = latest.get("updated_at")
    if updated:
        try:
            ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - ts
            if age > timedelta(hours=RUNTIME_STALE_HOURS):
                r.fail("runtime_freshness", f"최신 회차가 {age.total_seconds() / 3600:.1f}시간 전 -- 스케줄 미동작 의심")
            else:
                r.ok("runtime_freshness", f"{age.total_seconds() / 3600:.1f}시간 전")
        except ValueError:
            r.warn("runtime_freshness", f"updated_at 파싱 불가: {updated}")

    # 빌런 고착 감지 (절대 임계값 고착 사고 재발 방지)
    villains = [row.get("villain") for row in rows if row.get("villain")]
    if len(villains) >= 5 and len(set(villains)) == 1:
        r.warn("villain_variety", f"최근 {len(villains)}회 연속 '{villains[0]}' -- market_regime 가중치 점검 권장")
    elif villains:
        r.ok("villain_variety", f"최근 {len(villains)}회 빌런 {len(set(villains))}종")


def main() -> int:
    parser = argparse.ArgumentParser(description="배포 반영 및 운영 상태 자동 점검")
    parser.add_argument("--mode", choices=["config", "runtime", "all"], default="config")
    args = parser.parse_args()

    print(f"[healthcheck] v{VERSION} mode={args.mode}")
    r = Report()

    if args.mode in ("config", "all"):
        check_workflow_config(r)
        check_code_constants(r)
        check_environment(r)
        check_db_schema(r)

    if args.mode in ("runtime", "all"):
        check_runtime_health(r)

    print("\n" + "=" * 60)
    if r.failures:
        print(f"RESULT: FAIL ({len(r.failures)} failures, {len(r.warnings)} warnings)")
        for item in r.failures:
            print(f"  - {item}")
        return 1

    print(f"RESULT: PASS ({len(r.warnings)} warnings)")
    for item in r.warnings:
        print(f"  - {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
