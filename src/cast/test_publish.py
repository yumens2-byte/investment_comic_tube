"""render/publish 연결 테스트 — 모드 게이트·중복 게이트 검증."""
import json
import os

import pytest

from src.cast.publish import (
    PublishBlocked, build_cast_scenes, cast_episode_metadata, publish_cast,
)


def test_build_cast_scenes_orders_by_cut():
    cuts = [{"cut": 2, "path": "b.mp4", "duration_sec": 10.1},
            {"cut": 1, "path": "a.mp4", "duration_sec": 10.0}]
    scenes = build_cast_scenes(cuts)
    assert [s["image"] for s in scenes] == ["a.mp4", "b.mp4"]
    assert all(s["source"] == "fal_i2v" for s in scenes)


def test_metadata_carries_line_cast():
    m = cast_episode_metadata("T3", {"villain": "Oil Shock Titan"})
    assert m["line"] == "cast" and m["tier"] == "T3"
    assert m["villain"] == "Oil Shock Titan"


def test_dry_run_publishes_nothing():
    assert publish_cast("x.mp4", "T3", "dry_run") is None


def test_shadow_publishes_nothing():
    assert publish_cast("x.mp4", "T3", "shadow") is None


def test_live_requires_env_gate(monkeypatch):
    monkeypatch.delenv("CAST_EXECUTION_MODE", raising=False)
    with pytest.raises(PublishBlocked, match="live"):
        publish_cast("x.mp4", "T3", "live")


def test_live_duplicate_line_blocked(monkeypatch):
    monkeypatch.setenv("CAST_EXECUTION_MODE", "live")

    class FakeQuery:
        def in_(self, *a): return self
        def gte(self, *a): return self
        def limit(self, n): return self
        def execute(self):
            class R: data = [{"episode_no": 1, "market_as_of": "2099-01-01"}]
            return R()

    class FakeTable:
        def __init__(self, name): pass
        def select(self, *a): return FakeQuery()
        def eq(self, *a): return FakeQuery()

    class FakeClient:
        def table(self, name): return FakeTable(name)

    import src.cast.publish as pub
    orig = pub.__dict__.get("has_published_today_line")
    monkeypatch.setattr(pub, "has_published_today_line", lambda line="daily": True)
    with pytest.raises(PublishBlocked, match="중복"):
        publish_cast("x.mp4", "T3", "live")
