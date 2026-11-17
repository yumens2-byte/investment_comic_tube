"""python -m src.cast.__main__plan — CAST plan 단계 진입점 (DN2026_0115).

워크플로 heredoc에 있던 plan 로직을 모듈로 옮겨 테스트 가능하게 한다(R6-05 통합 지적:
heredoc 미테스트 경로에서 run_date_kst 미정의 결함 발생).

흐름: 시장 데이터 → 계층 → 모드 → 직전 회차(shadow/live만, DB 오류 시 실패) → 빌런·축·복선
     → 캐스팅 → TURN 주역 → 대본 → cast_plan.json(I-1) + GITHUB_OUTPUT mode
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from src.cast import VERSION, cast_for_tier, classify_tier
from src.cast.mode import resolve_mode

logger = logging.getLogger("cast.plan")

ARTIFACT_DIR = "artifacts_cast"
PLAN_FILE = os.path.join(ARTIFACT_DIR, "cast_plan.json")


def _kst_today() -> str:
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def build_plan(data: dict, mode: str, run_id: str, run_date_kst: str, tier_force: str | None = None) -> dict:
    """plan 문서(I-1) 생성. 외부 호출: shadow/live의 원장 조회, 대본 Gemini(키 있을 때)."""
    from src.cast import ledger, storyline
    from src.cast.script import build_script

    tier = (tier_force or "auto").strip()
    if tier not in ("T1", "T2", "T3"):
        tier = classify_tier(data)
    prev = (ledger.fetch_last_story(include_generated=(mode == "shadow"), exclude_run_id=run_id)
            if mode in ("shadow", "live") else None)   # DB 오류 시 예외 전파(fail-closed)
    decision = storyline.decide(data, prev, run_date_kst=run_date_kst)
    plan = cast_for_tier(tier, villain=decision["villain"])
    plan = storyline.apply_focus(plan, decision["hero_focus"])
    script = build_script(tier, plan, data, decision, prev, mode)
    story_state_next = storyline.next_state(decision, script)
    logger.info("[CAST] story villain=%s raw=%s axis=%s omen=%s focus=%s season=S%dW%d payoff=%s source=%s",
                decision["villain"], decision["villain_raw"], decision["axis"], decision["omen_villain"],
                decision["hero_focus"], decision["season_no"], decision["season_week"],
                decision.get("omen_payoff"), script["source"])
    return {
        "tier": tier,
        "plan": plan,
        "mode": mode,
        "run_id": run_id,
        "run_date_kst": run_date_kst,
        "villain": decision["villain"],
        "market_snapshot": json.loads(json.dumps(data, default=str)),
        "axis": decision["axis"],
        "villain_scores": decision["villain_scores"],
        "villain_raw": decision["villain_raw"],
        "omen_villain": decision["omen_villain"],
        "omen_axis": decision["omen_axis"],
        "hero_focus": decision["hero_focus"],
        "script": script,
        "story_state_next": story_state_next,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mode = resolve_mode(os.environ.get("CAST_EXECUTION_MODE"))
    logger.info("[CAST] v%s 시작 stage=plan mode=%s", VERSION, mode)
    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a") as f:
            f.write(f"mode={mode}\n")
    from src.collector import fetch_market_data
    data = fetch_market_data()
    doc = build_plan(data, mode, os.environ.get("GITHUB_RUN_ID", "local"), _kst_today(),
                     os.environ.get("CAST_TIER_FORCE"))
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    with open(PLAN_FILE, "w") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in doc.items() if k != "market_snapshot"},
                     ensure_ascii=False, indent=1)[:2000])


if __name__ == "__main__":
    main()
