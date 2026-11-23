# DN2026_0123
"""CAST 전용 주간 집계 — 이번 주(월~금) 누적 등락·주중 최대 변동일·주간 폭. # DN2026_0123 D4

공용 모듈(src.collector·src.market_regime·src.market_facts)은 수정하지 않는다.
일간 라인은 스냅샷 전체로 지문을 만들기 때문에(market_facts.validate_provenance) 필드 하나만
더해도 지문이 바뀐다. 그래서 CAST 안에서 지표별 1개월 일봉을 따로 받아 계산한다.

- 기준 주: 일간 스냅샷의 관측일(observed_date) 이 속한 ISO 주. 마감 전 봉은
  collector._last_completed_session(읽기 전용 재사용)으로 거른다.
- 정합 가드: 주간 마지막 종가·관측일이 일간 스냅샷과 다르면 그 지표는 주간 값을 쓰지 않는다.
- 실패는 빈 결과(일간 값으로 진행). 비용 0(yfinance).
"""
from __future__ import annotations

import copy
import logging
from datetime import date

logger = logging.getLogger("cast.weekly")

WEEK_KEYS = ("TNX", "VIX", "NASDAQ", "SPX", "DXY")
HISTORY_PERIOD = "1mo"
WEEKDAY_KR = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")
MIN_WEEK_SESSIONS = 2   # R6-13: 월요일 1세션만으로는 '이번 주'라 하지 않는다


def _iso_week(d: date) -> tuple[int, int]:
    return d.isocalendar()[:2]


def summarize(series: list[tuple[date, float]], anchor: date) -> dict | None:
    """(날짜, 종가) 오름차순 목록 → anchor 가 속한 주의 요약. 직전 주 종가가 없으면 None."""
    week = _iso_week(anchor)
    series = [(d, float(c)) for d, c in series if d <= anchor and c]
    in_week = [(d, c) for d, c in series if _iso_week(d) == week]
    before = [(d, c) for d, c in series if d < (in_week[0][0] if in_week else anchor)]
    if not in_week or not before:
        return None
    base_date, base = before[-1]
    moves, prev = [], base
    for d, c in in_week:
        moves.append((d, (c / prev - 1) * 100))
        prev = c
    max_date, max_pct = max(moves, key=lambda m: abs(m[1]))
    closes = [base] + [c for _, c in in_week]
    last_date, last = in_week[-1]
    return {
        "week_change_pct": round((last / base - 1) * 100, 2),
        "week_close": round(last, 2),
        "week_base_close": round(base, 2),
        "week_base_date": base_date.isoformat(),
        "week_observed_date": last_date.isoformat(),
        "week_sessions": len(in_week),
        "max_move_date": max_date.isoformat(),
        "max_move_weekday": WEEKDAY_KR[max_date.weekday()],
        "max_move_pct": round(max_pct, 2),
        "range_pct": round((max(closes) - min(closes)) / base * 100, 2),
    }


def _history(ticker: str, completed: date | None) -> list[tuple[date, float]]:
    import yfinance as yf
    hist = yf.Ticker(ticker).history(period=HISTORY_PERIOD)
    closes = hist["Close"].dropna() if not hist.empty else []
    out = []
    for idx, value in (closes.items() if hasattr(closes, "items") else []):
        d = idx.date() if hasattr(idx, "date") else None
        if d is None or (completed is not None and d > completed):
            continue
        out.append((d, float(value)))
    return out


def fetch_weekly(snapshot: dict, fetch=None) -> dict:
    """지표별 주간 요약. 정합 가드를 통과한 지표만 담는다. 실패는 빈 dict.

    fetch(ticker, completed) 는 테스트 주입용(기본 yfinance)."""
    try:
        from src.collector import TICKERS, _last_completed_session
    except Exception as e:  # noqa: BLE001 — 공용 수집기 import 실패는 일간 값으로 진행
        logger.warning("weekly_unavailable err=%s", type(e).__name__)
        return {}
    fetch = fetch or _history
    try:
        completed = _last_completed_session()
    except Exception:  # noqa: BLE001
        completed = None
    out: dict[str, dict] = {}
    for key in WEEK_KEYS:
        metric = snapshot.get(key) or {}
        observed, close = metric.get("observed_date"), metric.get("close")
        if not observed or close is None:
            continue
        try:
            anchor = date.fromisoformat(str(observed)[:10])
            summary = summarize(fetch(TICKERS[key], completed), anchor)
        except Exception as e:  # noqa: BLE001 — 지표 단위 실패는 그 지표만 제외
            logger.warning("weekly_metric_failed key=%s err=%s", key, type(e).__name__)
            continue
        if summary is None or summary["week_sessions"] < MIN_WEEK_SESSIONS:
            logger.info("weekly_metric_skipped key=%s reason=%s", key, "no_base" if summary is None else "short_week")
            continue
        if summary["week_observed_date"] != str(observed)[:10] or summary["week_close"] != round(float(close), 2):
            logger.warning("weekly_metric_mismatch key=%s", key)   # 정합 가드 — 값은 로그하지 않는다
            continue
        out[key] = summary
    logger.info("weekly_collected keys=%s", sorted(out))
    return out


def weekly_view(snapshot: dict, weekly: dict) -> dict:
    """등락률(change_pct)만 주간 누적으로 바꾼 스냅샷 사본 — 수치 토큰·자막 보호·대체 훅용.

    주간으로 바뀐 지표에만 change_basis="week" 를 단다(R6-13 치명: 지표 단위 판정).
    close·dev_pct 등 나머지는 일간 값 그대로다. 원본은 바꾸지 않는다."""
    view = copy.deepcopy(snapshot or {})
    for key, summary in (weekly or {}).items():
        if isinstance(view.get(key), dict) and summary.get("week_change_pct") is not None:
            view[key]["change_pct"] = summary["week_change_pct"]
            view[key]["change_basis"] = "week"
    return view


def scoring_view(snapshot: dict, weekly: dict) -> dict:
    """빌런 점수용 사본 — 주간 누적을 √세션 수로 나눠 일간 등락 척도에 맞춘다.

    R6-13 경고: market_regime 가중치(×2.0·×2.5)는 일간 change_pct 기준이라 주간 누적을 그대로 넣으면
    모멘텀·변동성 축으로 쏠린다. 무작위 보행 가정의 표준 척도 변환(√n)으로 정규화한다."""
    import math
    view = copy.deepcopy(snapshot or {})
    for key, summary in (weekly or {}).items():
        n = summary.get("week_sessions") or 1
        if isinstance(view.get(key), dict) and summary.get("week_change_pct") is not None:
            view[key]["change_pct"] = round(summary["week_change_pct"] / math.sqrt(max(1, n)), 2)
    return view


def week_keys(view: dict) -> set[str]:
    return {k for k, m in (view or {}).items() if isinstance(m, dict) and m.get("change_basis") == "week"}


def prompt_note(weekly: dict) -> str:
    """대본 프롬프트용 주간 사실(숫자 없음): 지표별 주중 최대 변동 요일·방향."""
    if not weekly:
        return ""
    from src.korean_text import particle_for
    from src.market_facts import LABELS
    parts, labels = [], []
    for key in WEEK_KEYS:
        s = weekly.get(key) or {}
        if s.get("week_change_pct") is None:
            continue
        labels.append(LABELS[key])
        if s.get("max_move_pct") is None or not s.get("max_move_weekday"):
            continue
        direction = "상승" if s["max_move_pct"] > 0 else "하락" if s["max_move_pct"] < 0 else "보합"
        parts.append(f"{LABELS[key]}{particle_for(LABELS[key], '는')} {s['max_move_weekday']}에 가장 크게 {direction}")
    if not labels:
        return ""
    # R6-13 치명: 주간 값은 지표 단위 — 주간 지표 토큰을 쓸 때만 '이번 주'
    note = (f"\n[주간 사실] 다음 지표의 토큰 값은 이번 주 누적 등락이다: {', '.join(labels)}. "
            "이 지표 토큰을 쓰면 첫 비트를 '이번 주'로 시작하고, 다른 지표 토큰은 '이번 주'를 붙이지 않는다.")
    return note + (f" 주중 흐름: {', '.join(parts)}." if parts else "")
