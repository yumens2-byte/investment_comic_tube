"""storyline 테스트 — 외부 호출 없음 (DN2026_0115)."""
import copy

import pytest

import src.cast.storyline as S
from src.cast.casting import cast_for_tier

DT, CR, BB = "Debt Titan", "Chaos Reaper", "Bull Brute"


@pytest.fixture
def fake_select(monkeypatch):
    calls = []

    def setup(raw, scores):
        def fake(market_data, prev_state=None):
            calls.append(prev_state)
            return raw, "theme", dict(scores)
        monkeypatch.setattr(S, "select_villain", fake)
        return calls
    return setup


def test_allowed_villains_from_canon():
    assert S.allowed_villains() == {DT}


def test_gate_chaos_reaper_to_debt_titan_keeps_axis(fake_select, caplog):
    fake_select(CR, {DT: 1.0, CR: 9.0, BB: 3.0})
    with caplog.at_level("INFO", logger="cast.storyline"):
        d = S.decide({}, None)
    assert d["villain"] == DT and d["villain_raw"] == CR and d["axis"] == "volatility"
    assert d["hero_focus"] == "FG-01"
    assert "villain_gated raw=Chaos Reaper" in caplog.text


def test_omen_is_second_ranked(fake_select):
    fake_select(CR, {DT: 1.0, CR: 9.0, BB: 3.0})
    d = S.decide({}, None)
    assert d["omen_axis"] == "momentum" and d["omen_villain"] == DT   # Bull Brute 게이트


def test_omen_excludes_rotated_raw(fake_select):
    # 스트릭 회전으로 raw가 2순위가 된 경우: omen은 raw 제외 최고점
    fake_select(BB, {DT: 9.0, CR: 1.0, BB: 3.0})
    d = S.decide({}, None)
    assert d["omen_axis"] == "rates"


@pytest.mark.parametrize("raw,focus", [(DT, "EDT"), (CR, "FG-01"), (BB, "LEV-01")])
def test_hero_focus_mapping(fake_select, raw, focus):
    fake_select(raw, {DT: 0.0, CR: 0.0, BB: 0.0, raw: 5.0})
    assert S.decide({}, None)["hero_focus"] == focus


def test_first_season(fake_select):
    fake_select(DT, {DT: 5.0, CR: 0.0, BB: 0.0})
    d = S.decide({}, None)
    assert (d["season_no"], d["season_week"], d["villain_streak"], d["omen_payoff"]) == (1, 1, 1, None)


def test_season_boundary_and_prev_state_shape(fake_select):
    calls = fake_select(DT, {DT: 5.0, CR: 0.0, BB: 0.0})
    prev = {"run_id": "p", "villain": DT,
            "story_state": {"season_no": 1, "season_week": 4, "villain_streak": 2, "ending_type": "QUESTION"}}
    d = S.decide({}, prev)
    assert (d["season_no"], d["season_week"]) == (2, 1)
    assert d["villain_streak"] == 3
    # R6-05 #1: select_villain에는 raw 이름 + streak만 전달
    assert calls[-1] == {"villain": DT, "story_state": {"villain_streak": 2}}
    assert d["ending_type"] == "DECLARE"   # src.story.select_ending_type 순환


def test_week_increment_and_streak_reset(fake_select):
    fake_select(DT, {DT: 5.0, CR: 0.0, BB: 0.0})
    prev = {"villain": "Other", "story_state": {"season_no": 2, "season_week": 2, "villain_streak": 3}}
    d = S.decide({}, prev)
    assert (d["season_no"], d["season_week"], d["villain_streak"]) == (2, 3, 1)


def test_payoff_hit_and_twist(fake_select):
    fake_select(CR, {DT: 1.0, CR: 9.0, BB: 0.0})
    hit = {"villain": DT, "story_state": {"omen_villain": DT, "omen_axis": "volatility"}}
    twist = {"villain": DT, "story_state": {"omen_villain": DT, "omen_axis": "rates"}}
    assert S.decide({}, hit)["omen_payoff"] == "hit"
    assert S.decide({}, twist)["omen_payoff"] == "twist"


def test_real_select_villain_streak_rotation():
    """실제 select_villain: 같은 빌런 3연속이면 2순위로 회전 — prev_state 형태가 맞는지."""
    data = {"TNX": {"close": 5.0, "change_pct": 3.0}, "VIX": {"close": 30.0, "change_pct": 20.0},
            "SPX": {"close": 1, "change_pct": -1.0}, "NASDAQ": {"close": 1, "change_pct": -1.0}}
    base = S.decide(data, None)
    prev = {"villain": base["villain_raw"], "story_state": {"villain_streak": 3}}
    rotated = S.decide(data, prev)
    assert rotated["villain_raw"] != base["villain_raw"]


def test_apply_focus_immutable_and_order():
    plan = cast_for_tier("T3")
    orig = copy.deepcopy(plan)
    new = S.apply_focus(plan, "LEV-01")
    assert plan == orig
    turn = next(c for c in new if c["role"] == "TURN")
    assert turn["characters"] == ["LEV-01", "EDT"]
    assert S.apply_focus(plan, "EDT")[4]["characters"] == ["EDT"]
    assert [c for c in new if c["role"] != "TURN"] == [c for c in orig if c["role"] != "TURN"]


def test_apply_focus_no_turn_in_t2():
    plan = cast_for_tier("T2")
    assert S.apply_focus(plan, "FG-01") == plan


def test_next_state_fields(fake_select):
    fake_select(CR, {DT: 1.0, CR: 9.0, BB: 3.0})
    d = S.decide({}, None)
    st = S.next_state(d, {"ending_type": "QUESTION", "beats": [{"narration": "a"}, {"narration": "끝"}]})
    assert set(st) == {"villain", "villain_raw", "axis", "villain_streak", "season_no", "season_week",
                       "omen_villain", "omen_axis", "ending_type", "hero_focus", "unresolved"}
    assert st["unresolved"] == "끝" and st["villain"] == DT and st["villain_raw"] == CR



def test_gated_prev_rotation_uses_raw():
    """R6-05 #1·#2 — 원장 villain은 게이트 후 이름(Debt Titan)이어도 회전·스트릭은 raw 기준.
    실제 select_villain으로 모멘텀이 계속 1위인 주를 연쇄 시뮬레이션한다."""
    data = {"TNX": {"close": 4.0, "change_pct": 0.0, "dev_pct": 0.0}, "DXY": {"close": 100, "change_pct": 0.0, "dev_pct": 0.0},
            "VIX": {"close": 14.0, "change_pct": -1.0, "dev_pct": -5.0},
            "SPX": {"close": 7000, "change_pct": 2.5, "dev_pct": 4.0},
            "NASDAQ": {"close": 25000, "change_pct": 3.0, "dev_pct": 5.0}}
    prev, raws, streaks = None, [], []
    for week in range(6):
        d = S.decide(data, prev)
        raws.append(d["villain_raw"]); streaks.append(d["villain_streak"])
        assert d["villain"] == "Debt Titan"          # 캐논 게이트
        prev = {"run_id": str(week), "villain": d["villain"],
                "story_state": {**S.next_state(d, {"beats": [{"narration": "x"}]})}}
    assert raws[:3] == [BB, BB, BB]
    assert raws[3] != BB                              # 3연속 후 강제 회전(raw 기준)
    assert max(streaks) <= 3                          # 스트릭 무한 증가 없음


def test_same_iso_week_rerun_does_not_advance():
    """R6-05 #15 — 같은 ISO 주 재실행은 주차·스트릭을 올리지 않는다."""
    data = {"TNX": {"close": 5.0, "change_pct": 3.0, "dev_pct": 6.0}}
    d1 = S.decide(data, None, run_date_kst="2026-11-21")
    prev = {"run_id": "a", "run_date_kst": "2026-11-21", "villain": d1["villain"],
            "story_state": S.next_state(d1, {"beats": [{"narration": "x"}]})}
    d2 = S.decide(data, prev, run_date_kst="2026-11-21")
    assert (d2["season_week"], d2["villain_streak"]) == (d1["season_week"], d1["villain_streak"])
    d3 = S.decide(data, prev, run_date_kst="2026-11-28")
    assert d3["season_week"] == d1["season_week"] + 1
