"""python -m src.cast.__main__generate — T3 generate 단계 진입점.

입력: artifacts_cast/cast_plan.json (plan job 산출)
동작: 컷별 fal 요청 조립(캐논+상황값, REF 주입) → 제출·폴링·저장 → 비용 원장 기록
모드: CAST_EXECUTION_MODE=dry_run이면 요청 조립·비용 산정만(호출 0회).
"""
from __future__ import annotations

import json
import logging
import os

from src.cast import generate
from src.cast.casting import HEROES
from src.cast.refsheet import load_ref_urls, ref_url_for

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("cast.generate_main")

CANON_PROMPT = (
    "Comic-book cinematic shot, consistent character design: "
    "the hero as specified by the confirmed reference sheet, single action, single camera move, "
    "no text, no numbers, no letters anywhere in frame."
)


def main() -> None:
    plan_file = os.environ.get("CAST_PLAN_FILE", "artifacts_cast/cast_plan.json")
    with open(plan_file) as f:
        plan_doc = json.load(f)
    tier, plan = plan_doc["tier"], plan_doc["plan"]
    mode = os.environ.get("CAST_EXECUTION_MODE", "dry_run")

    refs = load_ref_urls()
    results, total_cost = [], 0.0
    for cut in plan:
        role = cut["role"]
        prompt = generate.build_cut_prompt(CANON_PROMPT, role)
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
        request = generate.build_fal_request(prompt, image_url)
        request["_cut_no"] = cut["cut"]

        if mode == "dry_run":
            results.append({"cut": cut["cut"], "mode": "dry_run", "request_keys": sorted(request)})
            continue
        out = f"artifacts_cast/cut_{cut['cut']:02d}.mp4"
        r = generate.generate_cut(request, out)
        total_cost += r["cost_usd"]
        results.append(r)
        logger.info("cut_done cut=%d cost=$%.2f", cut["cut"], r["cost_usd"])

    ledger = {"tier": tier, "mode": mode, "results": results, "total_cost_usd": round(total_cost, 2)}
    with open("artifacts_cast/generate_ledger.json", "w") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=1)
    logger.info("generate_finished tier=%s mode=%s total=$%.2f", tier, mode, total_cost)


if __name__ == "__main__":
    main()
