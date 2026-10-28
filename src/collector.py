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
from datetime import timedelta

import yfinance as yf

from src.market_sources import fetch_fallback

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
SESSION_SETTLE_DELAY = timedelta(minutes=75)


def _empty_metric() -> dict:
    """수집 실패 시의 표준 형태. 0.0 이 아니라 None 으로 '데이터 없음'을 명시한다."""
    return {"close": None, "change_pct": None, "sma20": None, "dev_pct": None, "source": None}


def _index_date(index):
    return index.date().isoformat() if hasattr(index, "date") else None


def _last_completed_session(now=None):
    """마지막으로 마감(+정산 여유)까지 끝난 뉴욕증시 세션 날짜. 달력을 못 구하면 None.

    2026-10-28 사고: 장중(09:43 ET) 재시도에서 yfinance 가 진행 중인 당일 봉을 마지막 행으로
    돌려줘, 장중 시세가 '종가'로 쓰일 뻔했고 동결 시세와도 어긋났다.
    선물(GOLD/OIL)·달러인덱스 일봉은 17:00 ET 에 끝나므로 마감 후 여유를 둔다.
    """
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        import exchange_calendars as xcals
        import pandas as pd

        now = now or datetime.now(ZoneInfo("America/New_York"))
        calendar = xcals.get_calendar("XNYS")
        today = pd.Timestamp(now.astimezone(ZoneInfo("America/New_York")).date())
        sessions = calendar.sessions_in_range(today - pd.Timedelta(days=14), today)
        done = [s for s in sessions
                if calendar.session_close(s).to_pydatetime() + SESSION_SETTLE_DELAY <= now]
        return done[-1].date() if done else None
    except Exception:  # 달력 실패가 수집을 막지 않는다. provenance 검증이 최종 방어선이다
        logger.warning("market_calendar_unavailable -- partial session bars not filtered")
        return None


def _build_metric(hist, completed=None) -> dict:
    closes = hist["Close"].dropna()
    if completed is not None and not closes.empty:
        closes = closes[[_index_date(i) is None or _index_date(i) <= completed.isoformat()
                         for i in closes.index]]
    if closes.empty:
        return _empty_metric()

    close = float(closes.iloc[-1])
    prev_close = float(closes.iloc[-2]) if len(closes) > 1 else None
    change_pct = ((close - prev_close) / prev_close * 100) if prev_close else None

    sma20 = None
    dev_pct = None
    if len(closes) >= SMA_WINDOW:
        sma20 = float(closes.iloc[-SMA_WINDOW:].mean())
        if sma20:
            dev_pct = (close - sma20) / sma20 * 100

    return {
        "close": round(close, 2),
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "sma20": round(sma20, 2) if sma20 is not None else None,
        "dev_pct": round(dev_pct, 2) if dev_pct is not None else None,
        "source": "yfinance",
        "close_raw": str(close), "prev_close_raw": str(prev_close) if prev_close is not None else None,
        "observed_date": _index_date(closes.index[-1]),
        "prev_observed_date": _index_date(closes.index[-2]) if len(closes) > 1 else None,
        "observation_kind": "daily_close" if completed is not None else "latest",
    }


def fetch_market_data() -> dict:
    """지표별 {close, change_pct, sma20, dev_pct} 딕셔너리를 반환한다."""
    logger.info("market_collection_started tickers=%s", len(TICKERS))
    data: dict[str, dict] = {}
    completed = _last_completed_session()

    for name, ticker in TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period=HISTORY_PERIOD)
            data[name] = _build_metric(hist, completed) if not hist.empty else _empty_metric()
        except Exception:  # 개별 지표 실패가 전체 수집을 막지 않는다
            logger.exception("market_collection_failed ticker=%s", name)
            data[name] = _empty_metric()

    # 1차(yfinance)에서 빠진 지표만 폴백 체인으로 개별 보충한다.
    # 소스 단위가 아니라 지표 단위이므로, 성공한 지표는 그대로 유지된다.
    for name, metric in data.items():
        if metric.get("close") is not None:
            continue
        recovered = fetch_fallback(name)
        if recovered:
            data[name] = recovered

    from datetime import datetime, timezone
    from src.pipeline_control import fingerprint
    for name, metric in data.items():
        metric.update(source_symbol=metric.get("source_symbol") or TICKERS[name],
                      unit="percent" if name == "TNX" else "USD" if name in ("GOLD", "OIL") else "index",
                      instrument_kind="yield" if name == "TNX" else "future" if name in ("GOLD", "OIL") else "index",
                      collected_at_utc=datetime.now(timezone.utc).isoformat(),
                      observation_timezone="America/New_York", schema_version=1)
        metric["raw_payload_hash"] = fingerprint({k: metric.get(k) for k in ("close_raw", "prev_close_raw", "observed_date", "source")})
    ok = sum(1 for m in data.values() if m["close"] is not None)
    sources = {n: m.get("source") for n, m in data.items()}
    logger.info(
        "market_collection_finished ok=%s total=%s sources=%s data=%s",
        ok, len(TICKERS), sources, data,
    )
    return data
