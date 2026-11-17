"""CAST-LINE 계획 모듈 테스트 — fail-closed 게이트 검증이 핵심."""
import json
import os

import pytest

from src.cast.casting import CastPlanError, cast_for_tier
from src.cast.tier import classify_tier


@pytest.fixture()
def canon_file(tmp_path, monkeypatch):
    canon = {
        "EDT": {},
        "Leverage Muscle Man": {"ref_status": "CANDIDATE"},
        "Exposure Futures Girl": {"ref_status": "CONFIRMED_9_9"},
    }
    p = tmp_path / "canon.json"
    p.write_text(json.dumps(canon, ensure_ascii=False))
    monkeypatch.setenv("CAST_CANON_FILE", str(p))
    return p


# ── tier ──
def test_t1_quiet_market():
    data = {"SPX": {"change_pct": 0.3}, "NASDAQ": {"change_pct": 0.2}, "VIX": {"close": 15}}
    assert classify_tier(data) == "T1"


def test_t2_eventful_market():
    data = {"SPX": {"change_pct": -1.4}, "NASDAQ": {"change_pct": 1.0}, "VIX": {"close": 20}}
    assert classify_tier(data) == "T2"


def test_t3_big_move():
    data = {"SPX": {"change_pct": 2.8}, "NASDAQ": {"change_pct": 1.0}, "VIX": {"close": 20}}
    assert classify_tier(data) == "T3"


def test_t3_vix_spike():
    data = {"SPX": {"change_pct": 0.5}, "NASDAQ": {"change_pct": 0.4}, "VIX": {"close": 31}}
    assert classify_tier(data) == "T3"


def test_t3_scheduled_event():
    data = {"SPX": {"change_pct": 0.1}, "NASDAQ": {"change_pct": 0.1}, "VIX": {"close": 14}}
    assert classify_tier(data, event="FOMC") == "T3"


# ── casting (fail-closed) ──
def test_t3_blocked_when_lev_ref_unconfirmed(canon_file):
    with pytest.raises(CastPlanError, match="LEV-01"):
        cast_for_tier("T3")


def test_t3_passes_when_all_refs_confirmed(canon_file):
    canon = json.loads(canon_file.read_text())
    canon["Leverage Muscle Man"]["ref_status"] = "CONFIRMED_6_6_MASTER"
    canon_file.write_text(json.dumps(canon, ensure_ascii=False))
    plan = cast_for_tier("T3", villain="Oil Shock Titan")
    assert len(plan) == 7
    assert plan[0]["characters"] == ["Oil Shock Titan"]


def test_t1_always_runs(canon_file):
    plan = cast_for_tier("T1")
    assert len(plan) == 3
    assert all(c == ["EDT"] or "villain" in c or "EDT" in c for c in [p["characters"] for p in plan])


def test_t2_runs_with_fg01_confirmed(canon_file):
    """T2 5컷 — FG-01 확정 상태면 LEV-01 승인 없이도 동작한다."""
    plan = cast_for_tier("T2", villain="Debt Titan")
    assert len(plan) == 5
    chars = [c for p in plan for c in p["characters"]]
    assert "LEV-01" not in chars            # 미확정 캐릭터 미배정
    assert "FG-01" in chars                 # 확정 히어로 증원


def test_unknown_tier_rejected(canon_file):
    with pytest.raises(CastPlanError, match="T1/T2/T3"):
        cast_for_tier("T4")


def test_missing_canon_file_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("CAST_CANON_FILE", str(tmp_path / "none.json"))
    with pytest.raises(CastPlanError, match="캐논 파일 부재"):
        cast_for_tier("T3")


def test_villain_only_cut_has_no_hero_after_filter():
    """부록 U 회귀 테스트 — 빌런 단독 컷은 히어로 REF 불필요(캐논 텍스트 기반)."""
    from src.cast.casting import HEROES, cast_for_tier

    # T2·T3의 1컷은 빌런 단독 — HEROES 필터 후 히어로 0이어야 한다 (T1 1컷은 EDT 단독이 정상)
    for tier in ("T2", "T3"):
        plan = cast_for_tier(tier)
        first = [c for c in plan[0]["characters"] if c in HEROES]
        assert first == [], f"{tier} 1컷(빌런 단독)에서 히어로 오인: {first}"
    assert [c for c in cast_for_tier("T1")[0]["characters"] if c in HEROES] == ["EDT"]

