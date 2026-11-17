"""python -m src.cast.__main__generate — T3 generate 단계 진입점.

입력: artifacts_cast/cast_plan.json (plan job 산출: tier, plan, mode, run_id, run_date_kst, market_snapshot)
동작: 컷별 fal 요청 조립(캐논+상황값, REF 주입) → 제출·폴링·저장 → 비용 원장 기록
모드(DN2026_0114, resolve_mode로 3값 외 dry_run 강등):
- dry_run: 요청 조립·계획 비용 산정만(fal·DB 호출 0회)
- shadow/live: 비용 상한(run·month) 판정 → cast_runs 선기록 → 생성
가드(DN2026_0114):
- 비용: 계획액 > CAST_RUN_CAP_USD, 월 누적+계획액 > CAST_MONTH_CAP_USD면 생성 전 차단,
  각 fal 제출 직전 누적 추정액 재판정 후 원장 add_cost(선기록)
- 시간: CAST_GEN_BUDGET_SEC를 남은 컷 수로 나눠 컷별 deadline 배분(최소 120초)
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from src.cast import VERSION, generate
from src.cast import ledger as cast_ledger
from src.cast.casting import HEROES
from src.cast.mode import resolve_mode
from src.cast.refsheet import load_ref_urls, ref_url_for

logger = logging.getLogger("cast.generate_main")

CANON_PROMPT = (
    "Comic-book cinematic shot, consistent character design: "
    "the hero as specified by the confirmed reference sheet, single action, single camera move, "
    "no text, no numbers, no letters anywhere in frame."
)

ARTIFACT_DIR = "artifacts_cast"
LEDGER_FILE = os.path.join(ARTIFACT_DIR, "generate_ledger.json")
MIN_CUT_WINDOW_SEC = 120   # DN2026_0114
SHORT_MIN_SEC, SHORT_MAX_SEC = 16, 58   # DN2026_0114 — compose 검증(15~60초)보다 1~2초 여유(R6-02 #8)


def _kst_today() -> str:
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _env_float(name: str, default: str) -> float:
    raw = os.environ.get(name) or default
    try:
        return float(raw)
    except ValueError as e:
        raise generate.GenerationError(f"{name} 해석 실패") from e


def _cut_sec() -> int:
    raw = os.environ.get("CAST_CUT_SEC") or "6"
    try:
        sec = int(raw)
    except ValueError as e:
        raise generate.GenerationError(f"CAST_CUT_SEC={raw!r} 정수 아님") from e
    if not 5 <= sec <= 15:
        raise generate.GenerationError(f"CAST_CUT_SEC={sec} — fal 지원 범위(5~15초) 이탈")
    return sec


def _write_ledger(state: dict) -> None:
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    with open(LEDGER_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, default=str)


def _image_url_for(cut: dict, refs: dict) -> str | None:
    # casting이 villain을 실명으로 치환하므로 HEROES 화이트리스트로 필터해야 한다
    # (구비교 c != "villain"은 항상 참 — 빌런 단독 컷 오인 결함, 부록 U)
    hero_refs = [c for c in cut["characters"] if c in HEROES]
    image_url = None
    for ch in hero_refs:                      # 첫 히어로 REF를 첫 프레임으로
        url = ref_url_for(refs, ch)
        if url:
            image_url = url
            break
    # REF 미주입 히어로 컷은 fail-closed — casting이 막지만 이중 방어
    if hero_refs and not image_url:
        raise generate.GenerationError(f"컷{cut['cut']} REF URL 미주입 — 생성 차단")
    return image_url


def _error_code(e: Exception) -> str:
    """원장 error 컬럼 — 유형명(+가드 사유 코드). 외부 응답 본문·키는 남기지 않는다."""
    msg = str(e)
    for code in ("cost_cap_run_runtime", "cost_cap_month_runtime", "cost_cap_month", "cost_cap_run",
                 "time_budget", "duration_out_of_range"):
        if msg.startswith(code):
            return f"{type(e).__name__}:{code}"
    return type(e).__name__


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    mode = resolve_mode(os.environ.get("CAST_EXECUTION_MODE"))  # DN2026_0114
    logger.info("[CAST] v%s 시작 stage=generate mode=%s", VERSION, mode)  # DN2026_0114

    plan_file = os.environ.get("CAST_PLAN_FILE", os.path.join(ARTIFACT_DIR, "cast_plan.json"))
    with open(plan_file) as f:
        plan_doc = json.load(f)
    tier, plan = plan_doc["tier"], plan_doc["plan"]
    run_id = str(plan_doc.get("run_id") or "local")
    run_date_kst = plan_doc.get("run_date_kst") or _kst_today()
    market_snapshot = plan_doc.get("market_snapshot")

    state: dict = {"tier": tier, "mode": mode, "run_id": run_id, "results": [],
                   "total_cost_usd": 0.0, "planned_cost_usd": None, "error": None}
    paid = mode in ("shadow", "live")
    opened = False   # DN2026_0114 (R6-02 #1): open_run 성공 전 실패는 원장 상태를 건드리지 않는다
    try:
        cut_sec = _cut_sec()
        per_cut = round(cut_sec * generate.FAL_PRICE_PER_SEC, 2)
        planned = round(len(plan) * cut_sec * generate.FAL_PRICE_PER_SEC, 2)
        state["planned_cost_usd"] = planned
        total_sec = len(plan) * cut_sec   # DN2026_0114 (R6-01 #5): 합성 15~60초를 과금 전에 검증
        if not SHORT_MIN_SEC <= total_sec <= SHORT_MAX_SEC:
            raise generate.GenerationError(
                f"duration_out_of_range total={total_sec}s cuts={len(plan)} cut_sec={cut_sec} "
                f"(허용 {SHORT_MIN_SEC}~{SHORT_MAX_SEC}s)")
        run_cap = _env_float("CAST_RUN_CAP_USD", "5")
        month_cap = _env_float("CAST_MONTH_CAP_USD", "30")
        budget = int(_env_float("CAST_GEN_BUDGET_SEC", "2400"))
        logger.info("cost_plan cuts=%d cut_sec=%d planned=$%.2f run_cap=$%.2f month_cap=$%.2f",
                    len(plan), cut_sec, planned, run_cap, month_cap)

        refs = load_ref_urls()
        if not paid:  # dry_run — 요청 조립만, DB·네트워크 0
            for cut in plan:
                request = generate.build_fal_request(
                    generate.build_cut_prompt(CANON_PROMPT, cut["role"]),
                    _image_url_for(cut, refs), cut_sec)
                state["results"].append({"cut": cut["cut"], "mode": "dry_run",
                                         "request_keys": sorted(request)})
                _write_ledger(state)
            logger.info("generate_finished tier=%s mode=dry_run planned=$%.2f", tier, planned)
            return

        # ── 비용 가드(사전) — DN2026_0114 ──
        if planned > run_cap:
            raise generate.GenerationError(f"cost_cap_run planned=${planned:.2f} cap=${run_cap:.2f}")
        spent = cast_ledger.month_spent_usd(run_date_kst)
        if spent + planned > month_cap:
            raise generate.GenerationError(
                f"cost_cap_month spent=${spent:.2f} planned=${planned:.2f} cap=${month_cap:.2f}")
        generate._fal_headers()   # 키 부재는 원장 기록·과금 가산 전에 차단
        # DN2026_0114 (R6-01 #3): 같은 run_id 재실행 시 기존 누적을 회당 상한에 합산
        prior = float(cast_ledger.open_run(run_id, run_date_kst, tier, mode, market_snapshot) or 0.0)
        opened = True
        state["prior_cost_usd"] = prior
        if prior + planned > run_cap + 1e-9:
            raise generate.GenerationError(
                f"cost_cap_run prior=${prior:.2f} planned=${planned:.2f} cap=${run_cap:.2f}")

        def on_submit() -> None:  # 제출 직전 런타임 가드 + 원장 선기록 — DN2026_0114
            if prior + state["total_cost_usd"] + per_cut > run_cap + 1e-9:
                raise generate.GenerationError(
                    f"cost_cap_run_runtime spent=${prior + state['total_cost_usd']:.2f} cap=${run_cap:.2f}")
            # DN2026_0114 (R6-01 #4): 재제출분까지 월 상한에 포함
            if spent + state["total_cost_usd"] + per_cut > month_cap + 1e-9:
                raise generate.GenerationError(
                    f"cost_cap_month_runtime spent=${spent + state['total_cost_usd']:.2f} cap=${month_cap:.2f}")
            cast_ledger.add_cost(run_id, per_cut)
            state["total_cost_usd"] = round(state["total_cost_usd"] + per_cut, 2)
            _write_ledger(state)

        start = time.time()
        for idx, cut in enumerate(plan):
            request = generate.build_fal_request(
                generate.build_cut_prompt(CANON_PROMPT, cut["role"]),
                _image_url_for(cut, refs), cut_sec)
            now = time.time()
            remaining = start + budget - now
            if remaining < MIN_CUT_WINDOW_SEC:   # 시간 가드 — DN2026_0114
                raise generate.GenerationError(f"time_budget remaining={remaining:.0f}s cut={cut['cut']}")
            deadline = now + max(MIN_CUT_WINDOW_SEC, remaining / (len(plan) - idx))
            out = os.path.join(ARTIFACT_DIR, f"cut_{cut['cut']:02d}.mp4")
            r = generate.generate_cut(request, out, cut_no=cut["cut"], deadline_ts=deadline,
                                      on_submit=on_submit)
            state["results"].append(r)
            _write_ledger(state)
            logger.info("cut_done cut=%d est_total=$%.2f", cut["cut"], state["total_cost_usd"])

        cast_ledger.mark(run_id, "generated")
        logger.info("generate_finished tier=%s mode=%s total=$%.2f", tier, mode, state["total_cost_usd"])
    except Exception as e:
        state["error"] = _error_code(e)
        state["error_detail"] = str(e)[:300]
        logger.error("generate_failed mode=%s err=%s", mode, state["error"])
        # 이미 uploading·published인 run_id면 상태를 덮어쓰지 않는다(R6-01 #2) — DN2026_0114
        if paid and opened:
            try:
                cast_ledger.mark(run_id, "gen_failed", error=state["error"])
            except Exception as me:  # best-effort — 원 예외를 가리지 않는다
                logger.warning("ledger_mark_failed err=%s", type(me).__name__)
        raise
    finally:
        _write_ledger(state)


if __name__ == "__main__":
    main()
