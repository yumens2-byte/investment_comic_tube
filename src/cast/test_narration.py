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
    out = N.synthesize(_beats(), str(tmp_path))
    assert [o["cut"] for o in out] == [1, 2, 3]
    assert out[0]["tempo"] == 1.0 and abs(out[0]["sec"] - 2.0) < 0.05
    # DN2026_0116: 컷 길이 = max(5, ceil(sec+0.6)) → 2.0→5, 5.7→7, 6.2→7 (합 19 ≥ 16)
    assert [o["cut_sec"] for o in out] == [5, 7, 7] and all(o["tempo"] == 1.0 for o in out)
    assert calls[0]["model"] == "gemini-3.8-flash-tts" and calls[0]["voice"] == "Charon"
    assert calls[0]["tones"] == ["긴박한 톤", "절박한 톤", "차분한 톤"]


def test_tempo_boundaries():
    assert N.tempo_for(5.7, 6) == 1.0          # ≤ budget-0.3 → 1.0
    assert N.tempo_for(1.0, 6) == 1.0
    assert N.tempo_for(5.71, 6) > 1.0
    assert abs(N.tempo_for(6.555, 6) - 1.15) < 1e-3


def test_too_long_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "synthesize_narrations", _fake_tts([]))
    monkeypatch.setattr(N, "_probe", lambda p: 10.5)   # 10.5/9.4 = 1.117 > 1.10
    with pytest.raises(N.NarrationError, match="narration_too_long beats=\\[0\\]"):
        N.synthesize(_beats(1), str(tmp_path))


def test_env_model_voice(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tts, "synthesize_narrations", _fake_tts(calls))
    monkeypatch.setattr(N, "_probe", lambda p: 3.0)
    monkeypatch.setenv("CAST_TTS_MODEL", "m-x")
    monkeypatch.setenv("TTS_VOICE", "Kore")
    out = N.synthesize(_beats(2), str(tmp_path))
    assert calls[0]["model"] == "m-x" and calls[0]["voice"] == "Kore"
    assert all(o["tempo"] == 1.0 for o in out)


def test_reason_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "synthesize_narrations", _fake_tts([], reason="tts:no_api_key"))
    monkeypatch.setattr(N, "_probe", lambda p: 3.0)
    with pytest.raises(N.NarrationError, match="tts:no_api_key"):
        N.synthesize(_beats(), str(tmp_path))


def test_none_path_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "synthesize_narrations", _fake_tts([], none_at=1))
    monkeypatch.setattr(N, "_probe", lambda p: 3.0)
    with pytest.raises(N.NarrationError, match="ok=2/3"):
        N.synthesize(_beats(), str(tmp_path))


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



def test_fit_cuts_rules():
    """DN2026_0116 — 실측 길이 → 컷 길이(5~10, tempo ≤1.10), 합계 16 미만 보정, 합성 58 초과 차단."""
    f = N.fit_cuts([2.0, 2.0, 2.0])                    # 5+5+5=15 < 16 → 한 컷 +1
    assert sum(x["cut_sec"] for x in f) == 16
    f = N.fit_cuts([4.0, 7.2, 9.6])
    assert [x["cut_sec"] for x in f] == [5, 8, 10] and f[2]["tempo"] == round(9.6 / 9.4, 4)
    with pytest.raises(N.NarrationError, match="narration_total_too_long"):
        N.fit_cuts([8.9] * 7)                            # 7×(8.9/1.1+0.6)=60.8 > 58
    with pytest.raises(N.NarrationError, match=r"secs=\[1.00,11.00\]"):
        N.fit_cuts([1.0, 11.0])                          # 실패 시 전체 길이 기록


def test_fit_cuts_shadow_measured_case():
    """shadow run 44133047259 실측 7비트(합 55.98초) — R4-03 재현에서 61초 차단되던 케이스가 통과."""
    secs = [9.68, 7.4, 7.1, 7.4, 8.0, 8.4, 8.0]
    f = N.fit_cuts(secs)
    est = N._composed_total(secs, [x["tempo"] for x in f])
    assert est <= N.TOTAL_MAX_SEC and all(1.0 <= x["tempo"] <= N.FIT_MAX_TEMPO for x in f)
    assert all(5 <= x["cut_sec"] <= 10 for x in f)
    for s_, x in zip(secs, f):                          # fal 클립(요청 초) ≥ compose 목표 길이
        assert x["cut_sec"] >= s_ / x["tempo"] + N.CUT_TAIL_SEC - 1e-6


def test_fit_cuts_max_script_caps_fit():
    """대본 상한(30자+24자×6, 초당 3.1자) 최악값도 합성 58초 이내."""
    secs = [30 / 3.1] + [24 / 3.1] * 6
    f = N.fit_cuts(secs)
    assert N._composed_total(secs, [x["tempo"] for x in f]) <= N.TOTAL_MAX_SEC
