"""market tier 판정 — 시장 데이터 → T1/T2/T3 계층.

판정 근거는 반드시 로그에 기록한다 (규칙: 추측 판정 금지).
기준값은 환경변수로 외부 주입 가능 — 코드에 감추지 않는다.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("cast.tier")

# 임계치 (환경변수로 조정 가능 — 캐논 규칙 §8: 기준 변경은 근거 기록 후에만)
T2_MOVE_PCT = float(os.environ.get("CAST_T2_MOVE_PCT", "1.0"))   # 지수 등락 ±% 이상
T3_MOVE_PCT = float(os.environ.get("CAST_T3_MOVE_PCT", "2.5"))   # 대형 이동 ±%
T3_VIX = float(os.environ.get("CAST_T3_VIX", "30.0"))            # 공포지수 급등

# T3 강제 발동 이벤트 — 운영 원장에서 갱신 (주간 검토 안건)
T3_EVENTS = {"FOMC", "CPI", "NFP", "GDP"}

MARKET_KEYS = ("SPX", "NASDAQ", "TNX", "VIX", "DXY", "GOLD", "OIL")


def _max_abs_move(market_data: dict) -> tuple[str, float]:
    """지수류 중 최대 등락률(절대값)과 해당 지표명을 반환한다."""
    best_name, best_val = "", 0.0
    for key in ("SPX", "NASDAQ"):
        m = market_data.get(key) or {}
        v = abs(m.get("change_pct") or 0.0)
        if v > best_val:
            best_name, best_val = key, v
    return best_name, best_val


def classify_tier(market_data: dict, event: str | None = None) -> str:
    """market_data(collector.fetch_market_data 출력) → 'T1' | 'T2' | 'T3'.

    event: 예약 이벤트 이름(FOMC 등) 또는 None. T3_EVENTS 포함 시 즉시 T3.
    """
    name, move = _max_abs_move(market_data)
    vix = (market_data.get("VIX") or {}).get("close") or 0.0

    if event and event.upper() in T3_EVENTS:
        tier = "T3"
        reason = f"event={event}"
    elif move >= T3_MOVE_PCT or vix >= T3_VIX:
        tier = "T3"
        reason = f"move={name} {move:.2f}% vix={vix:.1f}"
    elif move >= T2_MOVE_PCT:
        tier = "T2"
        reason = f"move={name} {move:.2f}%"
    else:
        tier = "T1"
        reason = f"move={name} {move:.2f}% vix={vix:.1f}"

    logger.info("tier_decided tier=%s %s", tier, reason)
    return tier
