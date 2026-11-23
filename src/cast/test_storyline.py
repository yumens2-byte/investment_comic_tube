"""storyline 테스트 — 외부 호출 없음 (DN2026_0115)."""
import copy

import pytest

import src.cast.storyline as S
from src.cast.casting import cast_for_tier

DT, CR, BB = "Debt Titan", "Chaos Reaper", "Bull Brute"
VH, AR = "Volatility Hydra", "Algorithm Reaper"   # DN2026_0122 Notion 캐논 빌런


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
    assert S.allowed_villains() == {DT, VH, AR}   # DN2026_0122


@pytest.mark.parametrize("raw,axis,villain", [(DT, "rates", DT), (CR, "volatility", VH), (BB, "momentum", AR)])
def test_axis_maps_to_notion_canon_villain(fake_select, raw, axis, villain):
    fake_select(raw, {DT: 0.0, CR: 0.0, BB: 0.0, raw: 5.0})
    d = S.decide({}, None)
    assert (d["villain"], d["villain_raw"], d["axis"]) == (villain, raw, axis)


def test_gate_falls_back_to_debt_titan_when_canon_missing(fake_select, caplog):
    fake_select(CR, {DT: 1.0, CR: 9.0, BB: 3.0})
    canon = copy.deepcopy(S.load_cast_canon())
    canon["characters"].pop(VH)
    with caplog.at_level("INFO", logger="cast.storyline"):
        d = S.decide({}, None, canon=canon)
    assert d["villain"] == DT and d["villain_raw"] == CR and d["axis"] == "volatility"
    assert d["hero_focus"] == "FG-01"
    assert "villain_gated raw=Volatility Hydra" in caplog.text


def test_omen_is_second_ranked(fake_select):
    fake_select(CR, {DT: 1.0, CR: 9.0, BB: 3.0})
    d = S.decide({}, None)
    assert d["omen_axis"] == "momentum" and d["omen_villain"] == AR


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
                       "omen_villain", "omen_axis", "ending_type", "hero_focus", "unresolved", "basis",
                       "act", "recap", "lines"}   # DN2026_0122
    assert st["unresolved"] == "끝" and st["villain"] == VH and st["villain_raw"] == CR
    assert st["act"] == "INTRO" and st["lines"] == [["a", "끝"]] and st["recap"]["villain"] == VH



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
        assert d["villain"] == (AR if d["villain_raw"] == BB else S.CANON_VILLAIN[d["axis"]])   # DN2026_0122
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



def test_same_week_rerun_reproduces_original_decision():
    """R6-06 D2 — 같은 주 재실행은 원 회차의 basis로 다시 계산: 빌런·스트릭·payoff·주차 동일."""
    data = {"TNX": {"close": 4.0, "change_pct": 0.0, "dev_pct": 0.0}, "DXY": {"close": 100, "change_pct": 0.0, "dev_pct": 0.0},
            "VIX": {"close": 35.0, "change_pct": 20.0, "dev_pct": 30.0},
            "SPX": {"close": 7000, "change_pct": -3.0, "dev_pct": -4.0},
            "NASDAQ": {"close": 25000, "change_pct": -3.5, "dev_pct": -5.0}}
    prev, dates = None, ["2026-11-07", "2026-11-14", "2026-11-21"]
    for day in dates:                                 # 변동성 3주 연속 → 스트릭 3
        d = S.decide(data, prev, run_date_kst=day)
        prev = {"run_id": day, "run_date_kst": day, "villain": d["villain"],
                "story_state": S.next_state(d, {"beats": [{"narration": "x"}]})}
    original = d
    assert original["villain_streak"] == 3
    rerun = S.decide(data, prev, run_date_kst="2026-11-21")   # 같은 주 재실행
    for k in ("villain_raw", "villain_streak", "season_week", "omen_payoff", "ending_type", "omen_axis"):
        assert rerun[k] == original[k], k



def test_same_week_rerun_first_episode_and_next_week_and_no_nesting():
    """R6-07 #4 — 첫 회차(basis None) 재실행 동일, 재실행 다음 주 정상 증가, basis 중첩 없음."""
    data = {"TNX": {"close": 5.0, "change_pct": 3.0, "dev_pct": 6.0}}
    d1 = S.decide(data, None, run_date_kst="2026-11-21")
    st1 = S.next_state(d1, {"beats": [{"narration": "x"}]})
    assert st1["basis"] is None
    prev = {"run_id": "a", "run_date_kst": "2026-11-21", "villain": d1["villain"], "story_state": st1}
    again = S.decide(data, prev, run_date_kst="2026-11-21")
    assert (again["season_week"], again["villain_streak"], again["omen_payoff"]) == (1, 1, None)
    st_again = S.next_state(again, {"beats": [{"narration": "x"}]})
    nxt = S.decide(data, {"run_id": "b", "run_date_kst": "2026-11-21", "villain": again["villain"],
                          "story_state": st_again}, run_date_kst="2026-11-28")
    assert nxt["season_week"] == 2
    st2 = S.next_state(nxt, {"beats": [{"narration": "y"}]})
    assert "basis" not in (st2["basis"] or {})             # 중첩 누적 없음


# ---------- DN2026_0122 D2·D3·D5 ----------

@pytest.mark.parametrize("week,act", [(1, "INTRO"), (2, "RISE"), (3, "CLASH"), (4, "FINALE")])
def test_season_acts(fake_select, week, act):
    fake_select(DT, {DT: 5.0, CR: 0.0, BB: 0.0})
    prev = None if week == 1 else {"villain": DT, "story_state": {"season_no": 1, "season_week": week - 1,
                                                                   "ending_type": "QUESTION"}}
    d = S.decide({}, prev)
    assert d["season_week"] == week and d["act"] == act
    if act == "FINALE":
        assert d["ending_type"] == "TEASE"


def test_recap_and_lines_carry_two_episodes(fake_select):
    fake_select(DT, {DT: 5.0, CR: 0.0, BB: 0.0})
    d1 = S.decide({}, None)
    st1 = S.next_state(d1, {"beats": [{"role": "TURN", "narration": "t1"}, {"role": "RESOLVE", "narration": "r1"}]})
    d2 = S.decide({}, {"villain": DT, "story_state": st1})
    assert d2["prev_recap"]["turn"] == "t1" and d2["prev_recap"]["lesson"] == "r1"
    assert d2["prev_lines"] == [["t1", "r1"]]
    st2 = S.next_state(d2, {"beats": [{"role": "TURN", "narration": "t2"}, {"role": "RESOLVE", "narration": "r2"}]})
    assert st2["lines"] == [["t2", "r2"], ["t1", "r1"]]          # 이번 회 + 직전 회(2회분)
    d3 = S.decide({}, {"villain": DT, "story_state": st2})
    st3 = S.next_state(d3, {"beats": [{"role": "TURN", "narration": "t3"}]})
    assert st3["lines"] == [["t3"], ["t2", "r2"]]                 # 3회 전은 빠진다


def test_lines_two_episodes_regardless_of_beat_count(fake_select):
    """R6-12 (b): T3(7) → T1(3) → T3(7) 에서도 정확히 2회분."""
    fake_select(DT, {DT: 5.0, CR: 0.0, BB: 0.0})
    prev = None
    for n, tag in ((7, "a"), (3, "b"), (7, "c")):
        d = S.decide({}, prev)
        st = S.next_state(d, {"beats": [{"role": "X", "narration": f"{tag}{i}"} for i in range(n)]})
        prev = {"villain": DT, "story_state": st}
    assert [len(e) for e in st["lines"]] == [7, 3] and st["lines"][1][0] == "b0"


def test_legacy_flat_lines_treated_as_one_episode(fake_select):
    fake_select(DT, {DT: 5.0, CR: 0.0, BB: 0.0})
    d = S.decide({}, {"villain": DT, "story_state": {"season_week": 1, "lines": ["x", "y"]}})
    st = S.next_state(d, {"beats": [{"role": "X", "narration": "z"}]})
    assert st["lines"] == [["z"], ["x", "y"]]


def test_apply_omen_and_script_cast():
    plan = cast_for_tier("T3", villain=VH)
    orig = copy.deepcopy(plan)
    new = S.apply_omen(plan, VH, AR)
    assert plan == orig
    assert new[-1]["characters"] == ["EDT", AR] and new[3]["characters"] == [VH, "FG-01"]
    cast = S.apply_script_cast(new, {"beats": [{"cut": 2, "characters": ["LEV-01", "EDT"]}]})
    assert cast[1]["characters"] == ["LEV-01", "EDT"] and cast[2] == new[2]
