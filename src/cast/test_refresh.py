"""refresh_refs 테스트 — 네트워크 모킹으로 계약 검증."""
import json

import pytest

import src.cast.refresh_refs as rr


def _fake_run(monkeypatch, urls):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            stdout = json.dumps({"data": {"readable_url": urls[len(calls) - 1]}})
        return R()
    monkeypatch.setattr(rr.subprocess, "run", fake_run)
    return calls


def test_refresh_writes_all_three(tmp_path, monkeypatch):
    urls = [f"https://t/{i}" for i in range(3)]
    _fake_run(monkeypatch, urls)
    p = tmp_path / "refs.json"
    p.write_text(json.dumps({"EDT": [], "LEV-01": [], "FG-01": []}))
    out = rr.refresh(str(p))
    assert len(out) == 3
    doc = json.loads(p.read_text())
    assert doc["FG-01"] == ["https://t/2"]
    assert rr.all_refs_fresh(str(p)) is True


def test_refresh_partial_failure_aborts(tmp_path, monkeypatch):
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        if len(calls) == 2:
            raise RuntimeError("boom")
        class R:
            stdout = json.dumps({"data": {"readable_url": "https://t/x"}})
        return R()
    monkeypatch.setattr(rr.subprocess, "run", fake_run)
    p = tmp_path / "refs.json"
    p.write_text(json.dumps({"EDT": [], "LEV-01": [], "FG-01": []}))
    with pytest.raises(RuntimeError):
        rr.refresh(str(p))


def test_missing_url_aborts(tmp_path, monkeypatch):
    def fake_run(cmd, **kw):
        class R:
            stdout = "{}"   # URL 없음
        return R()
    monkeypatch.setattr(rr.subprocess, "run", fake_run)
    p = tmp_path / "refs.json"
    p.write_text(json.dumps({"EDT": [], "LEV-01": [], "FG-01": []}))
    with pytest.raises(RuntimeError, match="발급 실패"):
        rr.refresh(str(p))


def test_all_refs_fresh_false_when_empty(tmp_path):
    p = tmp_path / "refs.json"
    p.write_text(json.dumps({"EDT": [], "LEV-01": [], "FG-01": []}))
    assert rr.all_refs_fresh(str(p)) is False
