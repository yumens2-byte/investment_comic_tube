"""src.cast.__main__plan 테스트 — 워크플로 heredoc 이관분 (DN2026_0115, R6-05 통합 지적)."""
import json

import pytest

import src.cast.__main__plan as P
import src.cast.ledger as L

DATA = {"TNX": {"close": 4.1, "change_pct": 0.5, "dev_pct": 1.0},
        "DXY": {"close": 100.0, "change_pct": 0.1, "dev_pct": 0.2},
        "VIX": {"close": 22.9, "change_pct": 18.7, "dev_pct": 16.0},
        "SPX": {"close": 7400.0, "change_pct": -1.9, "dev_pct": -2.4},
        "NASDAQ": {"close": 24500.0, "change_pct": -2.49, "dev_pct": -4.2},
        "GOLD": {"close": 2400.0, "change_pct": 0.3}, "OIL": {"close": 70.0, "change_pct": -0.5}}


def test_dry_run_plan_no_db_and_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(L, "fetch_last_story", lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB")))
    doc = P.build_plan(DATA, "dry_run", "r1", "2026-11-21", "T3")
    for k in ("tier", "plan", "mode", "run_id", "run_date_kst", "villain", "market_snapshot", "axis",
              "villain_scores", "villain_raw", "omen_villain", "omen_axis", "hero_focus", "script",
              "story_state_next"):
        assert k in doc
    assert doc["tier"] == "T3" and len(doc["script"]["beats"]) == 7 == len(doc["plan"])
    assert doc["script"]["source"] == "template" and doc["run_date_kst"] == "2026-11-21"


def test_shadow_plan_reads_prev_excluding_current_run(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    seen = {}

    def fake(include_generated, exclude_run_id=None):
        seen.update(include_generated=include_generated, exclude=exclude_run_id)
        return None
    monkeypatch.setattr(L, "fetch_last_story", fake)
    P.build_plan(DATA, "shadow", "r9", "2026-11-21", None)
    assert seen == {"include_generated": True, "exclude": "r9"}


def test_prev_db_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(L, "fetch_last_story",
                        lambda *a, **k: (_ for _ in ()).throw(L.CastLedgerUnavailable("down")))
    with pytest.raises(L.CastLedgerUnavailable):
        P.build_plan(DATA, "live", "r1", "2026-11-21", None)


def test_main_writes_plan_and_output(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "gh_out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("CAST_EXECUTION_MODE", " Bogus ")
    monkeypatch.setenv("CAST_TIER_FORCE", "T2")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    import sys
    import types
    fake = types.ModuleType("src.collector")   # 실 collector는 yfinance 의존 — 네트워크·의존성 없이 대체
    fake.fetch_market_data = lambda: DATA
    monkeypatch.setitem(sys.modules, "src.collector", fake)
    P.main()
    assert out.read_text() == "mode=dry_run\n"
    doc = json.loads((tmp_path / "artifacts_cast" / "cast_plan.json").read_text())
    assert doc["mode"] == "dry_run" and doc["tier"] == "T2" and len(doc["script"]["beats"]) == 5
