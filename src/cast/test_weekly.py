"""weekly 테스트 — 네트워크 없음 (DN2026_0123 D4)."""
from datetime import date

import src.cast.__main__plan as P
import src.cast.weekly as W
from src import market_facts

# 11-16(월)~11-20(금), 직전 주 금요일 11-13
SERIES = [(date(2026, 11, 12), 100.0), (date(2026, 11, 13), 100.0), (date(2026, 11, 16), 101.0),
          (date(2026, 11, 17), 99.0), (date(2026, 11, 18), 104.0), (date(2026, 11, 19), 103.0),
          (date(2026, 11, 20), 105.0)]


def test_summarize_week():
    s = W.summarize(SERIES, date(2026, 11, 20))
    assert s["week_change_pct"] == 5.0 and s["week_base_date"] == "2026-11-13"
    assert s["week_sessions"] == 5 and s["week_observed_date"] == "2026-11-20"
    assert s["max_move_date"] == "2026-11-18" and s["max_move_weekday"] == "수요일"
    assert s["max_move_pct"] == round((104 / 99 - 1) * 100, 2)
    assert s["range_pct"] == 6.0   # (105-99)/100


def test_summarize_holiday_short_week_and_no_base():
    short = [p for p in SERIES if p[0] != date(2026, 11, 19)]
    assert W.summarize(short, date(2026, 11, 20))["week_sessions"] == 4
    assert W.summarize(SERIES[2:], date(2026, 11, 20)) is None        # 직전 주 종가 없음
    # 관측일 이후(미마감) 봉은 쓰지 않는다
    assert W.summarize(SERIES + [(date(2026, 11, 23), 200.0)], date(2026, 11, 20))["week_close"] == 105.0


def _snap(close=105.0, observed="2026-11-20"):
    return {k: {"close": close, "change_pct": 1.94, "dev_pct": 1.0, "observed_date": observed}
            for k in W.WEEK_KEYS}


def test_fetch_weekly_guard(monkeypatch):
    calls = []

    def fake(ticker, completed):
        calls.append(ticker)
        return SERIES
    out = W.fetch_weekly(_snap(), fetch=fake)
    assert set(out) == set(W.WEEK_KEYS) and len(calls) == 5
    assert W.fetch_weekly(_snap(close=104.99), fetch=fake) == {}             # 종가 불일치 → 제외
    assert W.fetch_weekly(_snap(close=103.0, observed="2026-11-19"), fetch=lambda t, c: SERIES)["SPX"]["week_close"] == 103.0
    assert W.fetch_weekly(_snap(), fetch=lambda t, c: (_ for _ in ()).throw(OSError("net"))) == {}


def test_weekly_view_only_change_pct():
    snap = _snap()
    weekly = {"VIX": W.summarize(SERIES, date(2026, 11, 20))}
    view = W.weekly_view(snap, weekly)
    assert view["VIX"]["change_pct"] == 5.0 and view["VIX"]["change_basis"] == "week"
    assert view["VIX"]["close"] == snap["VIX"]["close"] and view["VIX"]["dev_pct"] == 1.0
    assert snap["VIX"]["change_pct"] == 1.94 and view["SPX"]["change_pct"] == 1.94   # 원본 불변·미포함 지표 일간


def test_prompt_note_has_no_digits():
    note = W.prompt_note({"VIX": W.summarize(SERIES, date(2026, 11, 20))})
    assert "수요일" in note and "이번 주" in note and not any(ch.isdigit() for ch in note)
    assert W.prompt_note({}) == ""


DATA = {"TNX": {"close": 4.1, "change_pct": 0.5, "dev_pct": 1.0},
        "DXY": {"close": 100.0, "change_pct": 0.1, "dev_pct": 0.2},
        "VIX": {"close": 15.0, "change_pct": -1.0, "dev_pct": -2.0},
        "SPX": {"close": 7400.0, "change_pct": 0.1, "dev_pct": 0.5},
        "NASDAQ": {"close": 24500.0, "change_pct": 0.1, "dev_pct": 0.5}}


def test_plan_uses_weekly_for_villain_and_token(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    daily = P.build_plan(DATA, "dry_run", "r1", "2026-11-21", "T3")
    assert daily["fact_basis"] == "day" and not daily["script"]["beats"][0]["narration"].startswith("이번 주")
    # 주간으로는 나스닥·S&P 가 크게 올랐다 → 과열 축
    weekly = {k: {"week_change_pct": 4.5, "week_sessions": 1} for k in ("SPX", "NASDAQ")}
    doc = P.build_plan(DATA, "dry_run", "r1", "2026-11-21", "T3", weekly=weekly)
    assert doc["fact_basis"] == "week" and doc["axis"] == "momentum" and doc["villain"] == "Algorithm Reaper"
    first = doc["script"]["beats"][0]["narration"]
    assert first.startswith("이번 주 나스닥 4.50% 상승") and len(first) <= 34, first
    assert doc["market_snapshot"]["NASDAQ"]["change_pct"] == 0.1            # 원장용 일간 스냅샷 불변
    assert doc["fact_snapshot"]["NASDAQ"]["change_pct"] == 4.5


def _font_ok() -> bool:
    try:
        from src.caption_layout import font_identity
        font_identity()
        return True
    except Exception:  # noqa: BLE001 — 폰트 없는 CI test job 은 배치 검사만 건너뛴다
        return False


def test_longest_weekly_first_beats_fit_and_layout():
    """A안: 최장 조합(금리 라벨·게이트 형태·긴 빌런명)이 34자 이하이고, 폰트가 있으면 자막 배치가 된다."""
    import src.cast.script as C
    from src.caption_layout import build_layout
    font = _font_ok()
    from src.cast.casting import cast_for_tier
    snap = {k: {"close": 1.0, "change_pct": -12.34, "change_basis": "week"} for k in market_facts.KEYS}
    villains = {"rates": ["Debt Titan"], "volatility": ["Volatility Hydra", "Debt Titan"],
                "momentum": ["Algorithm Reaper", "Debt Titan"]}
    for tier in ("T1", "T3"):
        cut = cast_for_tier(tier)[0]
        for axis, names in villains.items():
            for v in names:
                d = {"villain": v, "axis": axis, "act": "INTRO", "season_no": 1,
                     "season_week": 1}
                line = market_facts.resolve(C.template_line(cut["role"], 0, d, snap, cut), snap)
                assert line.startswith("이번 주 ") and len(line) <= C.MAX_RESOLVED_WEEK_CHARS, line
                if font:
                    assert build_layout(line, [C.villain_kr(v)])


# ---------- R6-13 ----------

def test_mixed_basis_no_week_prefix_on_daily_token():
    """치명: 주간 지표가 TNX 뿐이면 나스닥(일간) 토큰에 '이번 주'를 붙이지 않는다."""
    import src.cast.script as C
    d = {k: {"close": 100.0, "change_pct": -0.5} for k in W.WEEK_KEYS}
    v = W.weekly_view(d, {"TNX": {"week_change_pct": 1.2}})
    dec = {"villain": "Algorithm Reaper", "axis": "momentum", "act": "INTRO", "omen_payoff": None,
           "prev_lines": [], "season_no": 1, "season_week": 1}
    line = market_facts.resolve(C.template_line("ESTABLISH_THREAT", 0, dec, v, {"characters": []}), v)
    assert line == "나스닥 0.50% 하락, 알고리즘리퍼 등장"
    rates = market_facts.resolve(C.template_line("ESTABLISH_THREAT", 0, dict(dec, villain="Debt Titan",
                                                                             axis="rates"), v, {"characters": []}), v)
    assert rates.startswith("이번 주 미 10년물 금리 1.20% 상승")
    assert "나스닥" not in W.prompt_note({"TNX": {"week_change_pct": 1.2}})


def test_check_beat_week_prefix_rules():
    import src.cast.script as C
    v = W.weekly_view({"VIX": {"close": 20.0, "change_pct": 1.0}, "SPX": {"close": 1.0, "change_pct": -1.0}},
                      {"VIX": {"week_change_pct": 12.0}})
    sit = "The villain looms."
    ok = {"narration": "이번 주 {{FACT:VIX.change_pct}}, 공포가 깨어났다", "situation_en": sit}
    assert C.check_beat(ok, v)[0] is None
    assert C.check_beat({"narration": "{{FACT:VIX.change_pct}}, 공포가 깨어났다", "situation_en": sit},
                        v)[0] == "week_prefix_missing"
    assert C.check_beat({"narration": "이번 주 {{FACT:SPX.change_pct}}, 공포가 깨어났다", "situation_en": sit},
                        v)[0] == "week_prefix_on_daily"
    long_daily = {"narration": "{{FACT:SPX.change_pct}}, 볼래틸리티히드라가 깨어났다", "situation_en": sit}
    assert C.check_beat(long_daily, v)[0].startswith("narration_too_long")     # 일간은 30자 유지
    assert C.check_beat({"narration": "이번 주 시장이 흔들렸다", "situation_en": sit}, v, allow_token=False)[0] is None


def test_scoring_view_sqrt_normalized_and_short_week_skipped():
    v = W.scoring_view({"SPX": {"close": 1.0, "change_pct": 0.1}}, {"SPX": {"week_change_pct": 4.0, "week_sessions": 4}})
    assert v["SPX"]["change_pct"] == 2.0 and "change_basis" not in v["SPX"]
    monday = [p for p in SERIES if p[0] <= date(2026, 11, 16)]
    snap = {k: {"close": 101.0, "observed_date": "2026-11-16"} for k in W.WEEK_KEYS}
    assert W.fetch_weekly(snap, fetch=lambda t, c: monday) == {}      # 1세션 주는 제외


def test_prompt_note_particle():
    note = W.prompt_note({"NASDAQ": W.summarize(SERIES, date(2026, 11, 20))})
    assert "나스닥은" in note and "나스닥는" not in note
