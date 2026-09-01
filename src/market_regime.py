"""시장 데이터 → 빌런/테마 판정 (복합 스코어 방식).

기존 문제:
  절대 임계값(TNX>4.5, VIX>25) 방식은 금리 환경이 고착되면 빌런이 고정된다.
  실제로 Ep.103~110 여덟 편이 전부 Debt Titan 으로 나와 매 회차가 같은 영상이 됐다.

개선:
  세 축(금리압력 / 변동성 / 모멘텀)을 각각 점수화하고 최고점 축이 빌런을 결정한다.
  점수는 20일 이동평균 대비 편차(dev_pct)를 우선 사용하므로, 절대 수준이 높게
  유지되더라도 '지금 이 축이 상대적으로 튀는가'로 판정된다.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

VILLAIN_DEBT_TITAN = "Debt Titan"
VILLAIN_CHAOS_REAPER = "Chaos Reaper"
VILLAIN_BULL_BRUTE = "Bull Brute"

THEMES = {
    VILLAIN_DEBT_TITAN: "긴축의 심화와 방어선 사수",
    VILLAIN_CHAOS_REAPER: "변동성 폭발과 시장의 광기",
    VILLAIN_BULL_BRUTE: "유동성 장세와 돌파 매수",
}


def _get(market_data: dict, key: str, field: str) -> float | None:
    metric = market_data.get(key) or {}
    value = metric.get(field)
    return float(value) if isinstance(value, (int, float)) else None


def _rate_pressure_score(market_data: dict) -> float:
    """금리 압력. 10년물이 이평 위로 튈수록, 달러가 강할수록 높다."""
    score = 0.0
    tnx_dev = _get(market_data, "TNX", "dev_pct")
    if tnx_dev is not None:
        score += tnx_dev * 2.0
    else:
        # 이평 미확보 시에만 절대 수준으로 보조 판정
        tnx_close = _get(market_data, "TNX", "close")
        if tnx_close is not None and tnx_close > 4.5:
            score += (tnx_close - 4.5) * 10.0

    dxy_dev = _get(market_data, "DXY", "dev_pct")
    if dxy_dev is not None:
        score += dxy_dev * 1.0
    return score


def _volatility_score(market_data: dict) -> float:
    """변동성. VIX 절대 수준과 이평 대비 급등을 함께 본다."""
    score = 0.0
    vix_close = _get(market_data, "VIX", "close")
    if vix_close is not None:
        # VIX 20 을 중립으로 두고 초과분을 가중
        score += (vix_close - 20.0) * 1.5

    vix_dev = _get(market_data, "VIX", "dev_pct")
    if vix_dev is not None:
        score += vix_dev * 0.8

    # 지수 급락은 변동성 축으로 흡수한다
    for key in ("NASDAQ", "SPX"):
        change = _get(market_data, key, "change_pct")
        if change is not None and change < 0:
            score += abs(change) * 2.0
    return score


def _momentum_score(market_data: dict) -> float:
    """상승 모멘텀. 지수 상승과 이평 상회 정도."""
    score = 0.0
    for key in ("NASDAQ", "SPX"):
        change = _get(market_data, key, "change_pct")
        if change is not None and change > 0:
            score += change * 2.5
        dev = _get(market_data, key, "dev_pct")
        if dev is not None and dev > 0:
            score += dev * 1.0
    return score


def select_villain(market_data: dict) -> tuple[str, str, dict]:
    """빌런, 테마, 축별 점수를 반환한다."""
    scores = {
        VILLAIN_DEBT_TITAN: round(_rate_pressure_score(market_data), 2),
        VILLAIN_CHAOS_REAPER: round(_volatility_score(market_data), 2),
        VILLAIN_BULL_BRUTE: round(_momentum_score(market_data), 2),
    }
    villain = max(scores, key=lambda k: scores[k])
    logger.info("villain_scored villain=%s scores=%s", villain, scores)
    return villain, THEMES[villain], scores
