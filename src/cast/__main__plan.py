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


def build_plan(data: dict, mode: str, run_id: str, run_date_kst: str, tier_force: str | None = None,
               weekly: dict | None = None) -> dict:
    """plan 문서(I-1) 생성. 외부 호출: shadow/live의 원장 조회, 대본 Gemini(키 있을 때).
    DN2026_0123 D4: weekly(주간 요약) 가 있으면 빌런 점수·수치 토큰을 주간 누적 등락으로 판정한다."""
    from src.cast import ledger, storyline
    from src.cast import weekly as cast_weekly
    from src.cast.script import build_script

    tier = (tier_force or "auto").strip()
    if tier not in ("T1", "T2", "T3"):
        tier = classify_tier(data)
    prev = (ledger.fetch_last_story(include_generated=(mode == "shadow"), exclude_run_id=run_id)
            if mode in ("shadow", "live") else None)   # DB 오류 시 예외 전파(fail-closed)
    weekly = weekly or {}
    view = cast_weekly.weekly_view(data, weekly)   # DN2026_0123 D4: 등락률만 주간 누적(토큰·자막·훅)
    scoring = cast_weekly.scoring_view(data, weekly)   # R6-13: 점수는 √세션 정규화
    decision = storyline.decide(scoring, prev, run_date_kst=run_date_kst)
    if weekly:
        from src.market_regime import select_villain
        daily_raw, _t, daily_scores = select_villain(data, None)
        logger.info("[CAST] villain_scores_compare daily=%s(%s) weekly=%s(%s)", daily_raw, daily_scores,
                    decision["villain_raw"], decision["villain_scores"])
    decision["fact_basis"] = "week" if cast_weekly.week_keys(view) else "day"
    decision["weekly_note"] = cast_weekly.prompt_note(weekly)
    plan = cast_for_tier(tier, villain=decision["villain"])
    plan = storyline.apply_focus(plan, decision["hero_focus"])
    plan = storyline.apply_omen(plan, decision["villain"], decision["omen_villain"])   # DN2026_0122 D1
    script = build_script(tier, plan, view, decision, prev, mode)
    plan = storyline.apply_script_cast(plan, script)   # DN2026_0122 D6
    story_state_next = storyline.next_state(decision, script)
    logger.info("[CAST] story villain=%s raw=%s axis=%s omen=%s focus=%s season=S%dW%d act=%s basis=%s payoff=%s source=%s",
                decision["villain"], decision["villain_raw"], decision["axis"], decision["omen_villain"],
                decision["hero_focus"], decision["season_no"], decision["season_week"],
                decision.get("act"), decision["fact_basis"], decision.get("omen_payoff"), script["source"])
    return {
        "tier": tier,
        "plan": plan,
        "mode": mode,
        "run_id": run_id,
        "run_date_kst": run_date_kst,
        "villain": decision["villain"],
        "market_snapshot": json.loads(json.dumps(data, default=str)),
        # DN2026_0123 D4: 주간 요약·수치 토큰 대입 기준(자막 보호·훅에 사용). 원장 market_snapshot 은 일간 그대로
        "weekly": weekly,
        "fact_snapshot": json.loads(json.dumps(view, default=str)),
        "fact_basis": decision["fact_basis"],
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
    from src.cast.weekly import fetch_weekly
    weekly = fetch_weekly(data)   # DN2026_0123 D4 — 실패 시 빈 dict(일간 값으로 진행)
    doc = build_plan(data, mode, os.environ.get("GITHUB_RUN_ID", "local"), _kst_today(),
                     os.environ.get("CAST_TIER_FORCE"), weekly=weekly)
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    with open(PLAN_FILE, "w") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in doc.items() if k != "market_snapshot"},
                     ensure_ascii=False, indent=1)[:2000])


if __name__ == "__main__":
    main()
