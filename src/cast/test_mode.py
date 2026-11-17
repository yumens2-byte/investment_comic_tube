"""mode 정규화 테스트 — 3값 외 입력은 dry_run 강등 (DN2026_0114)."""
import logging

import pytest

from src.cast.mode import MODES, resolve_mode


@pytest.mark.parametrize("raw,expected", [
    ("dry_run", "dry_run"), ("shadow", "shadow"), ("live", "live"),
    ("LIVE", "live"), ("Shadow", "shadow"), ("  dry_run  ", "dry_run"), ("Live ", "live"),
])
def test_valid_modes_normalized(raw, expected):
    assert resolve_mode(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "prod", "production", "li ve", "true"])
def test_invalid_demoted_to_dry_run(raw, caplog):
    with caplog.at_level(logging.WARNING, logger="cast.mode"):
        assert resolve_mode(raw) == "dry_run"
    assert "mode_demoted" in caplog.text


def test_modes_tuple():
    assert MODES == ("dry_run", "shadow", "live")
