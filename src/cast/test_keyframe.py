"""keyframe 테스트 — 가짜 genai·fal, 네트워크 없음 (DN2026_0123 D7)."""
import io
import json

import pytest
from PIL import Image

import src.cast.keyframe as K
from src.cast.storyline import load_cast_canon


def _png(w=768, h=1376):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class _Part:
    def __init__(self, data):
        self.inline_data = type("B", (), {"data": data})()


class FakeClient:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.models = self

    def generate_content(self, model, contents, config):
        assert model == K.KEYFRAME_MODEL
        assert config.image_config.aspect_ratio == "9:16" and config.image_config.image_size == "1K"
        self.calls.append(contents)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return type("R", (), {"parts": [_Part(r)] if r else []})()


CUT_HERO = {"cut": 3, "role": "ENGAGE", "characters": ["EDT", "FG-01"]}
CUT_VILLAIN = {"cut": 1, "role": "ESTABLISH_THREAT", "characters": ["Volatility Hydra"]}


@pytest.fixture
def upload(monkeypatch):
    got = []
    monkeypatch.setattr(K, "_upload", lambda path, key: got.append((path, key)) or "https://cdn.example/k.png")
    return got


def test_prompt_canon_blocks_and_no_text_rule():
    p = K.build_prompt(CUT_VILLAIN["characters"], "The villain looms.", load_cast_canon(), "volatility")
    assert p.startswith(K.IMAGE_HEAD) and "Volatility Hydra, a monster of crackling lightning" in p
    assert "multiplying its heads" in p and "No text, no letters, no numbers" in p
    assert "Single action, single camera move" not in p
    with pytest.raises(K.KeyframeError):
        K.build_prompt(["EDT"], " ", load_cast_canon(), None)


def test_ref_paths_heroes_only_and_exist():
    paths = K.ref_paths(["EDT", "Volatility Hydra", "FG-01"])
    assert [p.rsplit("/", 1)[-1] for p in paths] == ["ref_edt_rcl07b.png", "ref_fg01_v3.png"]
    assert K.ref_paths(["Debt Titan"]) == []


def test_make_keyframe_ok(tmp_path, upload):
    c = FakeClient([_png()])
    attempts = []
    r = K.make_keyframe(CUT_HERO, "EDT and Exposure Futures Girl stand.", load_cast_canon(), "rates",
                        str(tmp_path), client=c, fal_key="k", on_attempt=lambda: attempts.append(1))
    assert r["url"] == "https://cdn.example/k.png" and r["size"] == [768, 1376] and r["attempts"] == 1
    assert len(c.calls[0]) == 3 and attempts == [1]          # REF 2장 + 프롬프트
    assert upload[0][1] == "k" and (tmp_path / "keyframe_03.png").exists()


def test_retry_once_then_fail(tmp_path, upload):
    c = FakeClient([RuntimeError("503"), _png(1376, 768)])   # 1회 예외, 2회 가로 이미지
    attempts = []
    with pytest.raises(K.KeyframeError, match="keyframe_failed"):
        K.make_keyframe(CUT_VILLAIN, "The villain looms.", load_cast_canon(), "volatility", str(tmp_path),
                        client=c, fal_key="k", on_attempt=lambda: attempts.append(1))
    assert attempts == [1, 1] and upload == []


def test_retry_succeeds_second(tmp_path, upload):
    c = FakeClient([b"", _png()])
    r = K.make_keyframe(CUT_VILLAIN, "The villain looms.", load_cast_canon(), "volatility", str(tmp_path),
                        client=c, fal_key="k")
    assert r["attempts"] == 2


def test_upload_failure_not_regenerated(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "_upload", lambda p, k: (_ for _ in ()).throw(OSError("net")))
    c = FakeClient([_png(), _png()])
    with pytest.raises(K.KeyframeError, match="upload_failed"):
        K.make_keyframe(CUT_HERO, "EDT stands.", load_cast_canon(), None, str(tmp_path), client=c, fal_key="k")
    assert len(c.calls) == 1


def test_cost_guard_in_on_attempt_stops_before_call(tmp_path, upload):
    c = FakeClient([_png()])

    def guard():
        raise K.KeyframeError("cost_cap")
    with pytest.raises(K.KeyframeError, match="cost_cap"):
        K.make_keyframe(CUT_HERO, "EDT stands.", load_cast_canon(), None, str(tmp_path), client=c,
                        fal_key="k", on_attempt=guard)
    assert c.calls == []


@pytest.mark.parametrize("value,expected", [(None, True), ("", True), ("true", True), ("false", False),
                                            ("FALSE", False)])
def test_enabled_switch(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("CAST_KEYFRAME_ENABLED", raising=False)
    else:
        monkeypatch.setenv("CAST_KEYFRAME_ENABLED", value)
    assert K.enabled() is expected


def test_fal_client_upload_signature():
    """설치된 fal-client(1.0.3)에 lifecycle 인자·StorageSettings(expires_in) 가 실재하는지."""
    import inspect

    import fal_client
    assert "lifecycle" in inspect.signature(fal_client.SyncClient.upload_file).parameters
    assert fal_client.StorageSettings(expires_in=K.UPLOAD_EXPIRES_IN)
    assert json.dumps(K.REF_FILES)
