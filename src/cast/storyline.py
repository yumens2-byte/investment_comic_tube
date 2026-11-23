# DN2026_0115
"""CAST-LINE 스토리 레이어 — 빌런·축·연속성(시즌·스트릭·복선) 결정. # DN2026_0115

- 빌런 선정은 src.market_regime.select_villain 단일 출처(편집 금지, import만).
- villain_gate: 캐논 _cast_video_canon.characters 에 등재된 빌런만 출연. 그 외는 Debt Titan
  + 해당 축 attack_form 으로 대체(DN2026_0115 D3). 축(axis)은 원 빌런 기준으로 유지한다.
- DN2026_0120 D1: 화면 빌런은 축별 Notion 캐논 빌런(CANON_VILLAIN). 회전·스트릭은 villain_raw 기준 유지.
- DN2026_0120 D2·D3: 시즌 4막(act) + 직전 회차 요약(recap)을 story_state(jsonb 내부 키)에 저장.
- 외부 호출 없음(순수 함수). 이전 회차 상태(prev)는 호출부가 ledger.fetch_last_story로 넘긴다.
"""
from __future__ import annotations

import copy
import json
import logging
import os

from src.market_regime import (
    VILLAIN_BULL_BRUTE,
    VILLAIN_CHAOS_REAPER,
    VILLAIN_DEBT_TITAN,
    select_villain,
)

from .casting import HEROES, _canon_file

logger = logging.getLogger("cast.storyline")

AXIS = {  # DN2026_0115
    VILLAIN_DEBT_TITAN: "rates",
    VILLAIN_CHAOS_REAPER: "volatility",
    VILLAIN_BULL_BRUTE: "momentum",
}
HERO_FOCUS = {"rates": "EDT", "volatility": "FG-01", "momentum": "LEV-01"}
# DN2026_0120 D1: 축 → Notion 캐논 빌런(02_EDT_CHARACTER_CANON v2.18.1 CHAR_VILLAIN_001·004·005, '노션이 최종')
CANON_VILLAIN = {"rates": "Debt Titan", "volatility": "Volatility Hydra", "momentum": "Algorithm Reaper"}
# DN2026_0120 D2: 시즌 4막 — 주차(1~4) → 막
ACTS = {1: "INTRO", 2: "RISE", 3: "CLASH", 4: "FINALE"}
GATE_FALLBACK = VILLAIN_DEBT_TITAN
SEASON_WEEKS = 4
FALLBACK_ENDINGS = ("QUESTION", "DECLARE", "TEASE", "TWIST")


def load_cast_canon() -> dict:
    """_canon_data.json 의 _cast_video_canon 블록. 없으면 RuntimeError(추측 진행 금지)."""
    path = _canon_file()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    canon = data.get("_cast_video_canon")
    if not isinstance(canon, dict) or not isinstance(canon.get("characters"), dict):
        raise RuntimeError(f"_cast_video_canon.characters 부재: {os.path.basename(path)}")  # noqa: TRY004
    return canon


def allowed_villains(canon: dict | None = None) -> set[str]:
    canon = canon or load_cast_canon()
    return {k for k in canon["characters"] if k not in HEROES}


def gate(raw: str, allowed: set[str]) -> str:
    if raw in allowed:
        return raw
    logger.info("villain_gated raw=%s → %s", raw, GATE_FALLBACK)
    return GATE_FALLBACK


def _ending_type(prev: dict | None) -> str:
    prev_state = {"story_state": (prev or {}).get("story_state") or {}} if prev else None
    try:
        from src.story import select_ending_type
        return select_ending_type(prev_state)
    except Exception as e:  # noqa: BLE001 — story 모듈 import 불가 시 결정적 순환
        logger.warning("ending_type_fallback err=%s", type(e).__name__)
        last = str(((prev_state or {}).get("story_state") or {}).get("ending_type") or "").upper()
        if last in FALLBACK_ENDINGS:
            return FALLBACK_ENDINGS[(FALLBACK_ENDINGS.index(last) + 1) % len(FALLBACK_ENDINGS)]
        return FALLBACK_ENDINGS[0]


def _same_iso_week(a: str | None, b: str | None) -> bool:
    from datetime import date
    try:
        return date.fromisoformat(str(a)[:10]).isocalendar()[:2] == date.fromisoformat(str(b)[:10]).isocalendar()[:2]
    except (TypeError, ValueError):
        return False


def decide(market_data: dict, prev: dict | None, canon: dict | None = None,
           run_date_kst: str | None = None) -> dict:
    """이번 회차 스토리 결정. prev = ledger.fetch_last_story() 결과 또는 None."""
    canon = canon or load_cast_canon()
    allowed = allowed_villains(canon)
    prev_ss = dict((prev or {}).get("story_state") or {})
    # R6-06 D2: 같은 ISO 주 재실행은 그 회차가 썼던 '직전 주 기준(basis)'으로 다시 계산한다.
    # (같은 주 회차를 직전 주로 취급하면 스트릭 회전·payoff가 원 회차와 달라진다)
    if prev and run_date_kst and _same_iso_week(prev.get("run_date_kst"), run_date_kst):
        basis = prev_ss.get("basis")
        logger.info("story_same_week_rerun basis=%s", "yes" if basis else "none")
        if basis:
            prev = {"run_id": prev.get("run_id"), "villain": basis.get("villain_raw"),
                    "run_date_kst": None, "story_state": dict(basis)}
            prev_ss = dict(basis)
        else:
            prev, prev_ss = None, {}
    # DN2026_0115 (R6-05 #1): 회전·스트릭은 게이트 전 이름(villain_raw) 기준이어야 한다.
    # 원장 villain 컬럼은 게이트 후 이름(현재 항상 Debt Titan)이라 그대로 넘기면 회전이 깨진다.
    prev_raw = prev_ss.get("villain_raw") or (prev or {}).get("villain") or prev_ss.get("villain")
    prev_state = ({"villain": prev_raw, "story_state": {"villain_streak": prev_ss.get("villain_streak")}}
                  if prev else None)

    villain_raw, _theme, scores = select_villain(market_data, prev_state)
    axis = AXIS[villain_raw]
    villain = gate(CANON_VILLAIN[axis], allowed)   # DN2026_0120 D1

    # 2순위 축: villain_raw 를 제외한 최고 점수(스트릭 회전으로 raw가 2순위가 된 경우도 중복 없음)
    others = sorted((k for k in scores if k != villain_raw), key=lambda k: scores[k], reverse=True)
    omen_raw = others[0] if others else villain_raw
    omen_axis = AXIS[omen_raw]
    omen_villain = gate(CANON_VILLAIN[omen_axis], allowed)   # DN2026_0120 D1

    if prev:
        week = int(prev_ss.get("season_week") or 0) + 1
        season = int(prev_ss.get("season_no") or 1)
        if week > SEASON_WEEKS:
            season, week = season + 1, 1
        prev_streak = prev_ss.get("villain_streak")
        streak = ((int(prev_streak) + 1 if isinstance(prev_streak, int) else 2)
                  if prev_raw == villain_raw else 1)   # raw 기준(R6-05 #1)
    else:
        season, week, streak = 1, 1, 1

    payoff = None
    if prev and prev_ss.get("omen_villain"):
        # omen_villain 은 게이트 후 이름(Debt Titan 대체 가능)이라 축 일치를 우선 판정한다.
        prev_axis = prev_ss.get("omen_axis")
        hit = (axis == prev_axis) if prev_axis else (villain_raw == prev_ss.get("omen_villain"))
        payoff = "hit" if hit else "twist"

    return {
        "villain": villain,
        "villain_raw": villain_raw,
        "villain_scores": scores,
        "axis": axis,
        "omen_villain": omen_villain,
        "omen_axis": omen_axis,
        "hero_focus": HERO_FOCUS[axis],
        "season_no": season,
        "season_week": week,
        "act": ACTS.get(week, "INTRO"),   # DN2026_0120 D2
        "villain_streak": streak,
        # DN2026_0120 D2: 시즌 마지막 회(FINALE)는 다음 시즌 예고(TEASE) 고정, 그 외 회전
        "ending_type": "TEASE" if ACTS.get(week) == "FINALE" else _ending_type(prev),
        "omen_payoff": payoff,
        "prev_unresolved": prev_ss.get("unresolved"),
        "prev_recap": prev_ss.get("recap"),   # DN2026_0120 D3
        "prev_lines": list(prev_ss.get("lines") or []),   # DN2026_0120 D5: 직전 2회 대사(금지 목록)
        # R6-06 D2: 이번 회차 계산에 쓴 직전 주 상태 — 같은 주 재실행 시 동일 계산 재현용
        "basis": ({k: prev_ss.get(k) for k in ("villain_raw", "villain", "villain_streak", "season_no",
                                               "season_week", "omen_villain", "omen_axis", "ending_type",
                                               "unresolved", "recap", "lines")}
                  | {"villain_raw": prev_raw}) if prev else None,
    }


def apply_focus(plan: list[dict], hero_focus: str) -> list[dict]:
    """TURN 비트 출연을 [hero_focus, EDT](중복 제거)로 바꾼 새 plan. 원본 불변. T3만 TURN 존재."""
    new = copy.deepcopy(plan)
    for cut in new:
        if cut.get("role") == "TURN":
            cut["characters"] = list(dict.fromkeys([hero_focus, "EDT"]))
    return new


def apply_omen(plan: list[dict], villain: str, omen_villain: str) -> list[dict]:
    """DN2026_0120 D1: NEXT_OMEN 컷의 빌런을 다음 회 예고 빌런으로 바꾼 새 plan. 원본 불변."""
    new = copy.deepcopy(plan)
    for cut in new:
        if cut.get("role") == "NEXT_OMEN":
            cut["characters"] = list(dict.fromkeys(omen_villain if c == villain else c
                                                   for c in cut["characters"]))
    return new


def apply_script_cast(plan: list[dict], script: dict) -> list[dict]:
    """DN2026_0120 D6: 대본이 고른 비트별 화면 캐릭터(script.resolve_characters 검증 완료)를 plan에 반영."""
    by_cut = {int(b["cut"]): b.get("characters") for b in (script or {}).get("beats") or []}
    new = copy.deepcopy(plan)
    for cut in new:
        chars = by_cut.get(int(cut["cut"]))
        if chars:
            cut["characters"] = list(chars)
    return new


def next_state(decision: dict, script: dict) -> dict:
    beats = (script or {}).get("beats") or []
    return {
        "villain": decision["villain"],
        "villain_raw": decision["villain_raw"],
        "axis": decision["axis"],
        "villain_streak": decision["villain_streak"],
        "season_no": decision["season_no"],
        "season_week": decision["season_week"],
        "omen_villain": decision["omen_villain"],
        "omen_axis": decision["omen_axis"],
        "ending_type": (script or {}).get("ending_type") or decision["ending_type"],
        "hero_focus": decision["hero_focus"],
        "unresolved": beats[-1]["narration"] if beats else None,
        "basis": decision.get("basis"),   # R6-06 D2
        "act": decision.get("act"),   # DN2026_0120 D2
        "recap": build_recap(decision, beats),   # DN2026_0120 D3
        # DN2026_0120 D5: 이번 회 + 직전 회 대사(최대 2회분)를 다음 회 금지 목록으로 넘긴다
        "lines": [b["narration"] for b in beats] + list(decision.get("prev_lines") or [])[:len(beats) or 0],
    }


def build_recap(decision: dict, beats: list[dict]) -> dict:
    """DN2026_0120 D3: 다음 회 프롬프트·템플릿에 넘길 직전 회차 요약(숫자 없는 사실만)."""
    by_role = {b.get("role"): b.get("narration") for b in beats}
    return {
        "season_no": decision.get("season_no"),
        "season_week": decision.get("season_week"),
        "act": decision.get("act"),
        "villain": decision.get("villain"),
        "axis": decision.get("axis"),
        "hero_focus": decision.get("hero_focus"),
        "turn": by_role.get("TURN") or by_role.get("REGROUP"),
        "lesson": by_role.get("RESOLVE"),
        "omen": by_role.get("NEXT_OMEN"),
    }
