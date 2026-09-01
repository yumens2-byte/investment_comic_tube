"""시장 지표 다중 소스 폴백 체인.

설계 원칙:
  - **지표 단위 폴백**이지 소스 단위가 아니다. yfinance 가 VIX 만 실패하면
    VIX 만 다음 소스에서 가져오고, 성공한 지표는 그대로 둔다.
  - 각 값에 어느 소스에서 왔는지(`source`)를 기록해 사후 추적을 가능하게 한다.
  - 폴백 소스는 API 키가 없으면 조용히 건너뛴다(그 소스만 스킵, 체인은 계속).
  - 전 체인이 실패한 지표는 None 으로 남고, validation 이 발행을 중단시킨다.

실측 커버리지 (2026-09-01 마스터 계정 직접 호출 검증):
  FMP 무료   : ^VIX / ^GSPC / ^IXIC 가능, ^TNX / DX-Y.NYB 는 ACCESS DENIED
  AlphaVantage 무료 : TREASURY_YIELD 가능, 인덱스 데이터는 미제공
  FRED       : 전 지표 커버 가능하나 T+1 지연
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15

# 지표별 소스 심볼 매핑. 소스마다 심볼 체계가 다르므로 한 곳에 모은다.
FMP_SYMBOLS = {"VIX": "^VIX", "SPX": "^GSPC", "NASDAQ": "^IXIC"}
FRED_SERIES = {
    "TNX": "DGS10",
    "VIX": "VIXCLS",
    "SPX": "SP500",
    "NASDAQ": "NASDAQCOM",
    "DXY": "DTWEXBGS",
}
STOOQ_SYMBOLS = {"SPX": "^spx", "NASDAQ": "^ndq", "VIX": "^vix", "DXY": "^dxy"}

# 지표별 폴백 우선순위. yfinance(1차)는 collector 가 담당하고 여기부터 2차다.
FALLBACK_ORDER = {
    "TNX": ["alphavantage", "fred"],
    "VIX": ["fmp", "fred", "stooq"],
    "SPX": ["fmp", "fred", "stooq"],
    "NASDAQ": ["fmp", "fred", "stooq"],
    "DXY": ["fred", "stooq"],
    "GOLD": ["stooq"],
    "OIL": ["stooq"],
}


def _metric(close: float, prev_close: float | None, source: str) -> dict:
    change_pct = 0.0
    if prev_close:
        change_pct = (close - prev_close) / prev_close * 100
    return {
        "close": round(float(close), 2),
        "change_pct": round(float(change_pct), 2),
        "sma20": None,
        "dev_pct": None,
        "source": source,
    }


def _fetch_fmp(indicator: str) -> dict | None:
    api_key = os.environ.get("FMP_API_KEY")
    symbol = FMP_SYMBOLS.get(indicator)
    if not api_key or not symbol:
        return None

    url = "https://financialmodelingprep.com/stable/quote"
    resp = requests.get(
        url, params={"symbol": symbol, "apikey": api_key}, timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list) or not rows:
        return None

    row = rows[0]
    close = row.get("price")
    if close is None:
        return None
    return _metric(close, row.get("previousClose"), "fmp")


def _fetch_alphavantage(indicator: str) -> dict | None:
    api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not api_key or indicator != "TNX":
        return None

    resp = requests.get(
        "https://www.alphavantage.co/query",
        params={
            "function": "TREASURY_YIELD",
            "interval": "daily",
            "maturity": "10year",
            "apikey": api_key,
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    points = [p for p in (resp.json().get("data") or []) if p.get("value") not in (None, ".")]
    if not points:
        return None

    close = float(points[0]["value"])
    prev = float(points[1]["value"]) if len(points) > 1 else None
    return _metric(close, prev, "alphavantage")


def _fetch_fred(indicator: str) -> dict | None:
    api_key = os.environ.get("FRED_API_KEY")
    series = FRED_SERIES.get(indicator)
    if not api_key or not series:
        return None

    resp = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": series,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 5,
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    points = [
        p for p in (resp.json().get("observations") or []) if p.get("value") not in (None, ".")
    ]
    if not points:
        return None

    close = float(points[0]["value"])
    prev = float(points[1]["value"]) if len(points) > 1 else None
    return _metric(close, prev, "fred")


def _fetch_stooq(indicator: str) -> dict | None:
    """Stooq 는 인증이 필요 없는 CSV 소스라 최종 폴백으로 둔다."""
    symbol = STOOQ_SYMBOLS.get(indicator)
    if not symbol:
        return None

    resp = requests.get(
        "https://stooq.com/q/d/l/",
        params={"s": symbol, "i": "d"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    lines = [ln for ln in resp.text.strip().splitlines() if ln]
    if len(lines) < 2:
        return None

    header = lines[0].split(",")
    try:
        close_idx = header.index("Close")
    except ValueError:
        return None

    rows = [ln.split(",") for ln in lines[1:]]
    valid = [r for r in rows if len(r) > close_idx and r[close_idx] not in ("", "N/A")]
    if not valid:
        return None

    close = float(valid[-1][close_idx])
    prev = float(valid[-2][close_idx]) if len(valid) > 1 else None
    return _metric(close, prev, "stooq")


FETCHERS = {
    "fmp": _fetch_fmp,
    "alphavantage": _fetch_alphavantage,
    "fred": _fetch_fred,
    "stooq": _fetch_stooq,
}


def fetch_fallback(indicator: str) -> dict | None:
    """지표 하나를 폴백 체인에서 순서대로 시도한다. 전부 실패하면 None."""
    for source in FALLBACK_ORDER.get(indicator, []):
        fetcher = FETCHERS.get(source)
        if not fetcher:
            continue
        try:
            result = fetcher(indicator)
        except Exception as e:  # noqa: BLE001 - 한 소스 실패가 체인을 끊지 않는다
            logger.warning(
                "fallback_source_failed indicator=%s source=%s reason=%s: %s",
                indicator, source, type(e).__name__, e,
            )
            continue

        if result:
            logger.info("fallback_source_hit indicator=%s source=%s close=%s",
                        indicator, source, result["close"])
            return result
        logger.info("fallback_source_miss indicator=%s source=%s", indicator, source)

    logger.warning("fallback_exhausted indicator=%s", indicator)
    return None
