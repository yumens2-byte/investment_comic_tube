"""refresh_refs 테스트 — raw-URL 전환 후 계약 검증 (네트워크·gsk 무의존)."""
import json

import pytest

import src.cast.refresh_refs as rr


def test_refresh_uses_raw_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "_raw_url_ok", lambda u: True)
    p = tmp_path / "refs.json"
    p.write_text(json.dumps({"EDT": [], "LEV-01": [], "FG-01": []}))
    out = rr.refresh(str(p))
    assert len(out) == 3
    doc = json.loads(p.read_text())
    assert doc["FG-01"] == [rr.RAW_REFS["FG-01"]]


def test_refresh_falls_back_to_gsk(tmp_path, monkeypatch):
    """raw 검증 실패 → gsk 폴백 (로컬 환경 경로)."""
    monkeypatch.setattr(rr, "_raw_url_ok", lambda u: False)
    monkeypatch.setattr(rr, "_readable_url", lambda dp: "https://t/fallback")
    p = tmp_path / "refs.json"
    p.write_text(json.dumps({"EDT": [], "LEV-01": [], "FG-01": []}))
    out = rr.refresh(str(p))
    assert all(u == "https://t/fallback" for u in out.values())


def test_refresh_partial_failure_aborts(tmp_path, monkeypatch):
    calls = []
    def fake_raw(url):
        calls.append(url)
        return len(calls) != 2   # 두 번째 캐릭터에서 raw 실패
    def fake_gsk(dp):
        raise RuntimeError("boom")
    monkeypatch.setattr(rr, "_raw_url_ok", fake_raw)
    monkeypatch.setattr(rr, "_readable_url", fake_gsk)
    p = tmp_path / "refs.json"
    p.write_text(json.dumps({"EDT": [], "LEV-01": [], "FG-01": []}))
    with pytest.raises(RuntimeError):
        rr.refresh(str(p))


def test_all_refs_fresh_false_when_empty(tmp_path):
    p = tmp_path / "refs.json"
    p.write_text(json.dumps({"EDT": [], "LEV-01": [], "FG-01": []}))
    assert rr.all_refs_fresh(str(p)) is False


def test_all_refs_fresh_true_when_filled(tmp_path):
    p = tmp_path / "refs.json"
    p.write_text(json.dumps({c: [u] for c, u in rr.RAW_REFS.items()}))
    assert rr.all_refs_fresh(str(p)) is True
