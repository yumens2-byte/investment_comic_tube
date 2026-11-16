"""계층 → 컷별 캐스트 배정.

REF 상태는 canon_data.json 의 ref_status 를 단일 출처로 읽는다.
CONFIRMED 아닌 캐릭터가 배정되면 CastPlanError 로 차단한다 (fail-closed).
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("cast.casting")


class CastPlanError(RuntimeError):
    """REF 미확정 캐스팅 등 계획 단계 차단."""


DEFAULT_CANON_FILE = os.path.join(os.path.dirname(__file__), "_canon_data.json")


def _canon_file() -> str:
    """환경변수를 매 호출마다 읽는다 — 테스트에서 monkeypatch 주입 가능."""
    return os.environ.get("CAST_CANON_FILE", DEFAULT_CANON_FILE)

# T3 7컷 배역 — Notion 상세설계 v1.0 §5 그대로.
T3_PLAN = [
    {"cut": 1, "role": "ESTABLISH_THREAT", "characters": ["villain"]},
    {"cut": 2, "role": "ESCALATE", "characters": ["LEV-01"]},
    {"cut": 3, "role": "ENGAGE", "characters": ["EDT"]},
    {"cut": 4, "role": "CRISIS", "characters": ["villain", "FG-01"]},
    {"cut": 5, "role": "TURN", "characters": ["EDT", "FG-01"]},
    {"cut": 6, "role": "RESOLVE", "characters": ["EDT"]},
    {"cut": 7, "role": "NEXT_OMEN", "characters": ["EDT", "villain"]},
]

# T1 3컷 — EDT 단독 (기존 AFTERMATH 계약 승계).
T1_PLAN = [
    {"cut": 1, "role": "AFTERMATH_CALM", "characters": ["EDT"]},
    {"cut": 2, "role": "REGROUP", "characters": ["EDT"]},
    {"cut": 3, "role": "NEXT_OMEN", "characters": ["EDT", "villain"]},
]

HEROES = {"EDT", "LEV-01", "FG-01"}

# 캐논 데이터 키 매핑 — cast_for_tier의 캐릭터 코드는 캐논 ID, 실제 키는 이름.
CANON_KEYS = {
    "EDT": "EDT",
    "LEV-01": "Leverage Muscle Man",
    "FG-01": "Exposure Futures Girl",
}
REF_CONFIRMED_PREFIX = "CONFIRMED"


# EDT 기존 확정 — RCL-07-B SYSTEM STRIKE 9/9 (canon.py REF_SHEETS 단일 출처).
# 캐논 파일에 EDT ref_status가 없으면 이 상수로 판정한다.
EDT_DEFAULT_STATUS = "CONFIRMED_9_9"


def _canon_ref_status(canon: dict, character: str) -> str:
    key = CANON_KEYS.get(character, character)
    entry = canon.get(key) or {}
    return entry.get("ref_status") or (EDT_DEFAULT_STATUS if character == "EDT" else "")


def _load_canon() -> dict:
    path = _canon_file()
    if not os.path.exists(path):
        raise CastPlanError(f"캐논 파일 부재: {path} — REF 판정 불가, 추측 진행 금지")
    with open(path) as f:
        return json.load(f)


def cast_for_tier(tier: str, villain: str = "Debt Titan") -> list[dict]:
    """tier → 컷별 캐스트 계획. REF 미확정 히어로 포함 시 CastPlanError."""
    canon = _load_canon()
    plan = {"T1": T1_PLAN, "T3": T3_PLAN}.get(tier)
    if plan is None:
        raise CastPlanError(f"미정의 계층 '{tier}' — T1/T3만 지원 (추측 확장 금지)")

    resolved = []
    for cut in plan:
        if "villain" in cut["characters"]:
            cut = dict(cut, characters=[villain if c == "villain" else c for c in cut["characters"]])
        resolved.append(cut)
    plan = resolved
    for cut in plan:
        for ch in cut["characters"]:
            if ch in HEROES:
                status = _canon_ref_status(canon, ch)
                if not status.startswith(REF_CONFIRMED_PREFIX):
                    raise CastPlanError(
                        f"REF 미확정 캐릭터 '{ch}' (status={status or '없음'}) — "
                        f"컷{cut['cut']} 생성 차단(fail-closed)"
                    )
    logger.info("cast_planned tier=%s cuts=%d villain=%s", tier, len(plan), villain)
    return plan
