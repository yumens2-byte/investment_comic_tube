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
from src.cast import keyframe as cast_keyframe  # DN2026_0123 D7
from src.cast import ledger as cast_ledger
from src.cast import narration as cast_narration  # DN2026_0115
from src.cast.casting import (  # DN2026_0115 — 캐논 로더(읽기·import만)
    HEROES,
    _load_canon,
)
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
KEYFRAME_TIME_SHARE_SEC = 600   # DN2026_0123: 키프레임 단계 시간 상한(넘으면 남은 컷은 폴백)
SHORT_MIN_SEC, SHORT_MAX_SEC = 16, 58   # DN2026_0114 — compose 검증(15~60초)보다 1~2초 여유(R6-02 #8)


def _kst_today() -> str:
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _env_float(name: str, default: str) -> float:
    raw = os.environ.get(name) or default
    try:
        return float(raw)
    except ValueError as e:
        raise generate.GenerationError(f"{name} 해석 실패") from e


def _script_beats_present(plan_doc: dict) -> bool:
    script = plan_doc.get("script")
    return bool(isinstance(script, dict) and script.get("beats"))


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


def _first_frame(cut: dict, refs: dict, keyframes: dict, warn: bool = True) -> str | None:
    """DN2026_0123 D7: 키프레임 URL 우선, 없으면 기존 규칙(히어로 REF 원본 / 빌런 단독 None)."""
    url = keyframes.get(int(cut["cut"]))
    if url:
        return url
    fallback = _image_url_for(cut, refs)
    if fallback is None and warn:
        logger.warning("keyframe_missing_villain_only cut=%s — 이미지 없이 제출(16:9 t2v 가능)", cut["cut"])
    return fallback


def _make_keyframes(plan, beat_of, canon, axis, omen_axis, state, *, prior, spent, planned,
                    run_cap, month_cap, start, run_id) -> dict[int, str]:
    """DN2026_0123 D7 — 컷별 키프레임 URL. 이미지·업로드 실패는 폴백(예외 없음).
    단, 원장 가산 실패(CastLedgerUnavailable)는 fail-closed 로 전파한다(원장 없이 과금 금지).

    비용: 장당 KEYFRAME_PRICE_USD 를 이미지 호출 직전 원장·state 에 가산(재시도 포함).
    원장 cost_usd 는 numeric(8,2) 라 장당 $0.07 로 저장된다(과대 계상 ≤$0.003/장, 상한 판정에는 보수적).
    남은 fal 예상비(planned) + 이번 장이 회당·월 상한을 넘으면 그 장부터 생략한다."""
    price = cast_keyframe.KEYFRAME_PRICE_USD
    state["keyframes"], state["keyframe_cost_usd"] = [], 0.0
    if not cast_keyframe.enabled():
        logger.info("keyframe_skipped reason=disabled")
        return {}
    api_key, fal_key = os.environ.get("GEMINI_API_KEY"), os.environ.get("FAL_AI_KEY") or os.environ.get("FAL_KEY")
    if not api_key or not fal_key:
        logger.warning("keyframe_skipped reason=no_key")
        return {}
    try:
        client = cast_keyframe._make_client(api_key)
    except Exception as e:  # noqa: BLE001
        logger.warning("keyframe_skipped reason=client_%s", type(e).__name__)
        return {}

    def on_attempt() -> None:
        total = state["total_cost_usd"]
        if prior + total + planned + price > run_cap + 1e-9 or spent + total + planned + price > month_cap + 1e-9:
            raise cast_keyframe.KeyframeError("cost_cap")
        cast_ledger.add_cost(run_id, price)
        state["total_cost_usd"] = round(total + price, 3)
        state["keyframe_cost_usd"] = round(state["keyframe_cost_usd"] + price, 3)
        _write_ledger(state)

    urls: dict[int, str] = {}
    kf_start = time.time()   # R6-13: 키프레임 단계 자체 시각 기준(TTS 소요와 무관)
    for cut in plan:
        if time.time() - kf_start > KEYFRAME_TIME_SHARE_SEC:
            logger.warning("keyframe_time_share_exceeded cut=%s — 남은 컷 폴백", cut["cut"])
            break
        a = omen_axis if cut.get("role") == "NEXT_OMEN" and omen_axis else axis
        try:
            r = cast_keyframe.make_keyframe(cut, beat_of[int(cut["cut"])]["situation_en"], canon or {}, a,
                                            os.path.join(ARTIFACT_DIR, "keyframes"), client=client,
                                            fal_key=fal_key, on_attempt=on_attempt)
        except cast_keyframe.KeyframeError as e:
            logger.warning("keyframe_fallback cut=%s reason=%s", cut["cut"], str(e)[:80])
            state["keyframes"].append({"cut": int(cut["cut"]), "ok": False, "reason": str(e)[:80]})
            if "cost_cap" in str(e):
                break
            continue
        urls[r["cut"]] = r["url"]
        state["keyframes"].append({"cut": r["cut"], "ok": True, "size": r["size"], "attempts": r["attempts"]})
        _write_ledger(state)
    logger.info("keyframes_done ok=%d/%d cost=$%.3f", len(urls), len(plan), state["keyframe_cost_usd"])
    return urls


def _keyframe_plan(plan: list[dict], beat_of: dict, canon: dict | None, axis: str | None,
                   omen_axis: str | None) -> list[dict]:
    """dry_run 용 — 프롬프트 조립만(외부 호출 0)."""
    out = []
    for cut in plan:
        a = omen_axis if cut.get("role") == "NEXT_OMEN" and omen_axis else axis
        try:
            prompt = cast_keyframe.build_prompt(cut["characters"], beat_of[int(cut["cut"])]["situation_en"],
                                                canon or {}, a)
            out.append({"cut": cut["cut"], "prompt_chars": len(prompt),
                        "refs": len(cast_keyframe.ref_paths(cut["characters"]))})
        except cast_keyframe.KeyframeError as e:   # 실행 시 폴백될 컷 — dry_run 은 사유만 기록
            out.append({"cut": cut["cut"], "error": str(e)[:80]})
    return out


def _error_code(e: Exception) -> str:
    """원장 error 컬럼 — 유형명(+가드 사유 코드). 외부 응답 본문·키는 남기지 않는다."""
    msg = str(e)
    for code in ("narration_failed", "script_mismatch", "cost_cap_run_runtime",  # DN2026_0115
                 "cost_cap_month_runtime", "cost_cap_month", "cost_cap_run",
                 "time_budget", "duration_out_of_range",
                 "cut_sec_too_short_for_narration"):   # R4-08: 원장 error 사유 코드 일관성
        if msg.startswith(code):
            return f"{type(e).__name__}:{code}"
    return type(e).__name__


def _script_beats(plan_doc: dict, plan: list[dict]) -> list[dict] | None:
    """DN2026_0115 — script 있으면 beats 순서·개수·cut 이 plan 과 같아야 한다(I-2). 없으면 None(하위호환)."""
    script = plan_doc.get("script")
    if not script:
        return None
    beats = script.get("beats") if isinstance(script, dict) else None
    if not isinstance(beats, list) or len(beats) != len(plan):
        raise generate.GenerationError(
            f"script_mismatch beats={len(beats) if isinstance(beats, list) else None} plan={len(plan)}")
    for cut, beat in zip(plan, beats):
        if int(beat.get("cut", -1)) != int(cut["cut"]) or not beat.get("narration") \
                or not beat.get("situation_en"):
            raise generate.GenerationError(f"script_mismatch cut={cut['cut']}")
    return beats


def _cut_prompt(cut: dict, beat: dict | None, canon: dict | None, axis: str | None,
                omen_axis: str | None = None) -> str:
    """DN2026_0115 — 대본 있으면 v2(캐논+공격형태+situation_en), 없으면 기존 상황값.
    NEXT_OMEN 컷은 다음 주 예고 축(omen_axis)의 공격 형태를 쓴다(R6-05 #14)."""
    if beat is None:
        return generate.build_cut_prompt(CANON_PROMPT, cut["role"])
    if cut.get("role") == "NEXT_OMEN" and omen_axis:
        axis = omen_axis
    return generate.build_cut_prompt_v2(cut["characters"], beat["situation_en"], canon or {}, axis)


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
        narrated = _script_beats_present(plan_doc)
        cut_sec = None if narrated else _cut_sec()   # DN2026_0116 R6-03: 내레이션 회차는 CAST_CUT_SEC 미사용
        price = generate.FAL_PRICE_PER_SEC
        if narrated:
            # DN2026_0116: 내레이션 회차는 TTS 실측으로 컷별 길이(5~10초)를 정한다.
            # 사전 비용 가드는 최악값(전 컷 10초), 실측 후 다시 판정한다. 길이 하한·상한은 narration.fit_cuts.
            cut_secs = {int(c["cut"]): cast_narration.CUT_MAX_SEC for c in plan}
        else:
            cut_secs = {int(c["cut"]): cut_sec for c in plan}
            total_sec = len(plan) * cut_sec   # DN2026_0114 (R6-01 #5): 합성 15~60초를 과금 전에 검증
            if not SHORT_MIN_SEC <= total_sec <= SHORT_MAX_SEC:
                raise generate.GenerationError(
                    f"duration_out_of_range total={total_sec}s cuts={len(plan)} cut_sec={cut_sec} "
                    f"(허용 {SHORT_MIN_SEC}~{SHORT_MAX_SEC}s)")
        planned = round(sum(cut_secs.values()) * price, 2)
        state["planned_cost_usd"] = planned
        run_cap = _env_float("CAST_RUN_CAP_USD", "5")
        month_cap = _env_float("CAST_MONTH_CAP_USD", "30")
        budget = int(_env_float("CAST_GEN_BUDGET_SEC", "2400"))
        logger.info("cost_plan cuts=%d narrated=%s planned=$%.2f run_cap=$%.2f month_cap=$%.2f",
                    len(plan), narrated, planned, run_cap, month_cap)

        refs = load_ref_urls()
        beats = _script_beats(plan_doc, plan)                       # DN2026_0115
        canon = _load_canon() if beats else None
        axis = plan_doc.get("axis")
        beat_of = {int(b["cut"]): b for b in beats} if beats else {}
        if not paid:  # dry_run — 요청 조립만, DB·네트워크·TTS 0
            if beats:   # DN2026_0115 — 내레이션 계획(문장·글자수)만
                state["narration_plan"] = cast_narration.plan_only(beats)
                if cast_keyframe.enabled():   # DN2026_0123 D7 — 키프레임 프롬프트 조립만
                    state["keyframe_plan"] = _keyframe_plan(plan, beat_of, canon, axis, plan_doc.get("omen_axis"))
            for cut in plan:
                request = generate.build_fal_request(
                    _cut_prompt(cut, beat_of.get(int(cut["cut"])), canon, axis, plan_doc.get("omen_axis")),
                    _first_frame(cut, refs, {}, warn=False), min(cut_secs[int(cut["cut"])], 15))
                state["results"].append({"cut": cut["cut"], "mode": "dry_run",
                                         "request_keys": sorted(request),
                                         "prompt_chars": len(request["prompt"])})
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
        # 내레이션 회차는 최악값 대신 TTS 실측 후 재판정(아래), TTS 전에는 최소값(전 컷 5초)으로 — DN2026_0116
        floor_cost = round(len(plan) * cast_narration.CUT_MIN_SEC * price, 2) if narrated else planned
        if prior + floor_cost > run_cap + 1e-9:
            raise generate.GenerationError(
                f"cost_cap_run prior=${prior:.2f} planned=${floor_cost:.2f} cap=${run_cap:.2f}")
        # DN2026_0115: 내레이션 TTS — open_run 이후·첫 fal 제출 전. 실패 시 fal 0회로 종료.
        # TTS 비용은 fal cost 가드 대상 아님(OPEX 별도 등록).
        start = time.time()   # R6-05 #5: TTS 소요도 생성 시간 예산에 포함
        if beats:
            try:
                state["narration"] = cast_narration.synthesize(beats, os.path.join(ARTIFACT_DIR, "audio"))
            except cast_narration.NarrationError as e:
                raise generate.GenerationError(f"narration_failed {e}") from e
            # DN2026_0116: 실측 컷 길이로 비용 재판정(fal 제출 전)
            cut_secs = {int(n["cut"]): int(n["cut_sec"]) for n in state["narration"]}
            planned = round(sum(cut_secs.values()) * price, 2)
            state["planned_cost_usd"] = planned
            if prior + planned > run_cap + 1e-9:
                raise generate.GenerationError(
                    f"cost_cap_run prior=${prior:.2f} planned=${planned:.2f} cap=${run_cap:.2f}")
            if spent + planned > month_cap + 1e-9:
                raise generate.GenerationError(
                    f"cost_cap_month spent=${spent:.2f} planned=${planned:.2f} cap=${month_cap:.2f}")
            logger.info("cost_plan_fitted cuts=%s planned=$%.2f", list(cut_secs.values()), planned)
            _write_ledger(state)
        # DN2026_0115 (I-3) → DN2026_0116 R6-03: 스토리 상태는 TTS·실측 비용 판정 통과 후, 첫 fal 제출 직전에 기록
        record_story = getattr(cast_ledger, "record_story", None)
        if beats and callable(record_story):
            record_story(run_id, plan_doc.get("villain"), plan_doc.get("story_state_next"),
                         plan_doc.get("script"))
            state["story_recorded"] = True
            _write_ledger(state)
        current = {"cost": 0.0}   # DN2026_0116: 제출 중인 컷의 추정 비용(컷별 길이)

        def on_submit() -> None:  # 제출 직전 런타임 가드 + 원장 선기록 — DN2026_0114
            per_cut = current["cost"]
            if prior + state["total_cost_usd"] + per_cut > run_cap + 1e-9:
                raise generate.GenerationError(
                    f"cost_cap_run_runtime spent=${prior + state['total_cost_usd']:.2f} cap=${run_cap:.2f}")
            # DN2026_0114 (R6-01 #4): 재제출분까지 월 상한에 포함
            if spent + state["total_cost_usd"] + per_cut > month_cap + 1e-9:
                raise generate.GenerationError(
                    f"cost_cap_month_runtime spent=${spent + state['total_cost_usd']:.2f} cap=${month_cap:.2f}")
            cast_ledger.add_cost(run_id, per_cut)
            state["total_cost_usd"] = round(state["total_cost_usd"] + per_cut, 3)   # DN2026_0123: 키프레임 $0.067 합산
            _write_ledger(state)

        keyframes = _make_keyframes(plan, beat_of, canon, axis, plan_doc.get("omen_axis"), state,
                                    prior=prior, spent=spent, planned=planned, run_cap=run_cap,
                                    month_cap=month_cap, start=start, run_id=run_id) if beats else {}
        for idx, cut in enumerate(plan):
            request = generate.build_fal_request(
                _cut_prompt(cut, beat_of.get(int(cut["cut"])), canon, axis, plan_doc.get("omen_axis")),   # DN2026_0115
                _first_frame(cut, refs, keyframes), cut_secs[int(cut["cut"])])   # DN2026_0123 D7
            current["cost"] = round(cut_secs[int(cut["cut"])] * price, 2)
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
