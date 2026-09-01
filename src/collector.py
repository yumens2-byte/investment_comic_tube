"""미국 시장 지표 수집.

수집 원칙:
  - 종가(close)와 전일대비(change_pct) 외에 20일 이동평균(sma20)과 그 편차(dev_pct)를
    함께 산출한다. 절대 임계값만 쓰면 금리 환경이 고착됐을 때 빌런이 고정되므로
    (Ep.103~110 전부 Debt Titan 고착 사고), 상대값 기반 판정을 가능하게 하기 위함이다.
  - 수집 실패는 0.0 이 아니라 None 으로 남긴다. 0.0 은 "변동 없음"과 구분되지 않아
    하류 로직이 잘못된 판단을 하게 된다.
"""

from __future__ import annotations

import logging

import yfinance as yf

logger = logging.getLogger(__name__)

# 서사 판정 및 내레이션에 사용하는 지표
TICKERS = {
    "TNX": "^TNX",        # 미국 10년물 금리
    "VIX": "^VIX",        # 변동성 지수
    "NASDAQ": "^IXIC",    # 나스닥 종합
    "SPX": "^GSPC",       # S&P 500
    "DXY": "DX-Y.NYB",    # 달러 인덱스
    "GOLD": "GC=F",       # 금 선물
    "OIL": "CL=F",        # WTI 원유 선물
}

SMA_WINDOW = 20
HISTORY_PERIOD = "3mo"


def _empty_metric() -> dict:
    """수집 실패 시의 표준 형태. 0.0 이 아니라 None 으로 '데이터 없음'을 명시한다."""
    return {"close": None, "change_pct": None, "sma20": None, "dev_pct": None}


def _build_metric(hist) -> dict:
    closes = hist["Close"].dropna()
    if closes.empty:
        return _empty_metric()

    close = float(closes.iloc[-1])
    prev_close = float(closes.iloc[-2]) if len(closes) > 1 else close
    change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0.0

    sma20 = None
    dev_pct = None
    if len(closes) >= SMA_WINDOW:
        sma20 = float(closes.iloc[-SMA_WINDOW:].mean())
        if sma20:
            dev_pct = (close - sma20) / sma20 * 100

    return {
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "sma20": round(sma20, 2) if sma20 is not None else None,
        "dev_pct": round(dev_pct, 2) if dev_pct is not None else None,
    }


def fetch_market_data() -> dict:
    """지표별 {close, change_pct, sma20, dev_pct} 딕셔너리를 반환한다."""
    logger.info("market_collection_started tickers=%s", len(TICKERS))
    data: dict[str, dict] = {}

    for name, ticker in TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period=HISTORY_PERIOD)
            data[name] = _build_metric(hist) if not hist.empty else _empty_metric()
        except Exception:  # 개별 지표 실패가 전체 수집을 막지 않는다
            logger.exception("market_collection_failed ticker=%s", name)
            data[name] = _empty_metric()

    ok = sum(1 for m in data.values() if m["close"] is not None)
    logger.info("market_collection_finished ok=%s total=%s data=%s", ok, len(TICKERS), data)
    return data
