"""narration 단위 테스트 — 실 TTS·네트워크 없음 (DN2026_0115)."""
import shutil
import subprocess

import pytest

from src import tts
from src.cast import narration as N

HAS_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _beats(n=3, roles=("ESTABLISH_THREAT", "CRISIS", "RESOLVE")):
    return [{"cut": i + 1, "role": roles[i % len(roles)], "narration": f"문장 {i + 1}",
             "situation_en": "x"} for i in range(n)]


def _fake_tts(calls, secs=None, reason=None, none_at=None):
    def fake(narrations, output_dir="x", tones=None, voice_name=None, model_name=None, **_):
        calls.append({"n": list(narrations), "dir": output_dir, "tones": tones,
                      "voice": voice_name, "model": model_name})
        paths = []
        for i, _line in enumerate(narrations):
            if none_at == i:
                paths.append(None)
                continue
            p = f"{output_dir}/narration_{i}.wav"
            if secs is not None and HAS_FF:
                subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                                f"sine=frequency=330:duration={secs[i]}", "-ac", "1", "-ar", "24000", p],
                               check=True, capture_output=True, timeout=60)
            paths.append(p)
        return paths, reason
    return fake


@pytest.mark.skipif(not HAS_FF, reason="ffmpeg/ffprobe 없음")
def test_synthesize_real_wav(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tts, "synthesize_narrations", _fake_tts(calls, secs=[2.0, 5.7, 6.2]))
    monkeypatch.delenv("CAST_TTS_MODEL", raising=False)
    monkeypatch.delenv("TTS_VOICE", raising=False)
    out = N.synthesize(_beats(), str(tmp_path), budget_sec=6)
    assert [o["cut"] for o in out] == [1, 2, 3]
    assert out[0]["tempo"] == 1.0 and abs(out[0]["sec"] - 2.0) < 0.05
    assert abs(out[2]["tempo"] - 6.2 / 5.7) < 0.01
    assert calls[0]["model"] == "gemini-3.8-flash-tts" and calls[0]["voice"] == "Charon"
    assert calls[0]["tones"] == ["긴박한 톤", "절박한 톤", "차분한 톤"]


def test_tempo_boundaries():
    assert N.tempo_for(5.7, 6) == 1.0          # ≤ budget-0.3 → 1.0
    assert N.tempo_for(1.0, 6) == 1.0
    assert N.tempo_for(5.71, 6) > 1.0
    assert abs(N.tempo_for(6.555, 6) - 1.15) < 1e-3


def test_too_long_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "synthesize_narrations", _fake_tts([]))
    monkeypatch.setattr(N, "_probe", lambda p: 6.6)   # 6.6/5.7 = 1.158 > 1.15
    with pytest.raises(N.NarrationError, match="narration_too_long cut=1"):
        N.synthesize(_beats(1), str(tmp_path), budget_sec=6)


def test_env_model_voice(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tts, "synthesize_narrations", _fake_tts(calls))
    monkeypatch.setattr(N, "_probe", lambda p: 3.0)
    monkeypatch.setenv("CAST_TTS_MODEL", "m-x")
    monkeypatch.setenv("TTS_VOICE", "Kore")
    out = N.synthesize(_beats(2), str(tmp_path), budget_sec=6)
    assert calls[0]["model"] == "m-x" and calls[0]["voice"] == "Kore"
    assert all(o["tempo"] == 1.0 for o in out)


def test_reason_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "synthesize_narrations", _fake_tts([], reason="tts:no_api_key"))
    monkeypatch.setattr(N, "_probe", lambda p: 3.0)
    with pytest.raises(N.NarrationError, match="tts:no_api_key"):
        N.synthesize(_beats(), str(tmp_path), budget_sec=6)


def test_none_path_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "synthesize_narrations", _fake_tts([], none_at=1))
    monkeypatch.setattr(N, "_probe", lambda p: 3.0)
    with pytest.raises(N.NarrationError, match="ok=2/3"):
        N.synthesize(_beats(), str(tmp_path), budget_sec=6)


def test_tone_mapping():
    assert N.tone_for("ESTABLISH_THREAT") == "긴박한 톤"
    assert N.tone_for("ESCALATE") == "점점 고조되는 톤"
    assert N.tone_for("ENGAGE") == "단호한 톤"
    assert N.tone_for("CRISIS") == "절박한 톤"
    assert N.tone_for("TURN") == "반격하듯 힘찬 톤"
    assert N.tone_for("RESOLVE") == "차분한 톤"
    assert N.tone_for("NEXT_OMEN") == "낮고 불길한 톤"
    assert N.tone_for("AFTERMATH_CALM") is None and N.tone_for(None) is None


def test_plan_only_counts_chars():
    p = N.plan_only(_beats(2))
    assert p[0] == {"cut": 1, "role": "ESTABLISH_THREAT", "text": "문장 1", "chars": 4}
