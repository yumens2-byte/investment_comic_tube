"""refresh_refs raw-URL 전환 테스트 — gsk 없는 러너 환경 재현."""
import json
import os

import pytest

from src.cast import refresh_refs


@pytest.fixture
def refsheet(tmp_path):
    p = tmp_path / "refs.json"
    p.write_text("{}", encoding="utf-8")
    return str(p)


def test_refresh_uses_raw_url(refsheet, monkeypatch):
    monkeypatch.setattr(refresh_refs, "_raw_url_ok", lambda u: True)
    r = refresh_refs.refresh(refsheet)
    assert sorted(r) == ["EDT", "FG-01", "LEV-01"]
    assert all(u.startswith(refresh_refs.RAW_BASE) for u in r.values())
    doc = json.load(open(refsheet))
    assert doc["EDT"] == [refresh_refs.RAW_REFS["EDT"]]


def test_refresh_fails_closed_without_gsk(refsheet, monkeypatch):
    """raw 검증 실패 + gsk 부재(러너) → RuntimeError로 전체 중단."""
    monkeypatch.setattr(refresh_refs, "_raw_url_ok", lambda u: False)
    monkeypatch.setattr(refresh_refs, "_readable_url", None)  # gsk 호출 전제 제거
    with pytest.raises(Exception):
        refresh_refs.refresh(refsheet)
