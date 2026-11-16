"""generate 단위 테스트 — 네트워크 없이 계약 검증."""
import pytest

from src.cast.generate import (
    GenerationError, build_cut_prompt, build_fal_request, estimate_cost,
)
from src.cast.refsheet import load_ref_urls, ref_url_for


def test_prompt_is_canon_plus_situation_only():
    p = build_cut_prompt("HERO CANON BLOCK", "ENGAGE")
    assert p.startswith("HERO CANON BLOCK")
    assert "battle stance" in p


def test_unknown_role_rejected():
    with pytest.raises(GenerationError, match="미정의"):
        build_cut_prompt("c", "UNKNOWN_ROLE")


def test_fal_request_contract():
    r = build_fal_request("p", "https://x/y.png", 10)
    assert r["duration"] == 10 and r["resolution"] == "768P"
    assert r["prompt_expansion_mode"] == "disabled"
    assert r["image_url"] == "https://x/y.png"


def test_fal_request_t2v_when_no_ref():
    r = build_fal_request("p", None, 10)
    assert "image_url" not in r


def test_duration_bounds():
    with pytest.raises(GenerationError):
        build_fal_request("p", None, 4)
    with pytest.raises(GenerationError):
        build_fal_request("p", None, 16)


def test_cost_matches_measured_rate():
    assert estimate_cost(10.14) == 0.61   # 부록 E 실측치와 정합
    assert estimate_cost(70) == 4.2       # T3 회차 원가


def test_missing_fal_key_blocks():
    import os
    from src.cast import generate
    saved = {k: os.environ.pop(k) for k in ("FAL_AI_KEY", "FAL_KEY") if k in os.environ}
    try:
        with pytest.raises(GenerationError, match="FAL_AI_KEY"):
            generate.submit_cut({"prompt": "x"})
    finally:
        os.environ.update(saved)


def test_refsheet_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CAST_REFSHEET_FILE", str(tmp_path / "none.json"))
    assert load_ref_urls() == {}


def test_ref_url_first_entry():
    refs = {"FG-01": ["https://a/1", "https://a/2"]}
    assert ref_url_for(refs, "FG-01") == "https://a/1"
    assert ref_url_for(refs, "EDT") is None
