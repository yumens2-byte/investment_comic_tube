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
    assert calls[0]["tones"] == ["빠르고 또렷하게 말하는 긴박한 톤", "빠르고 또렷하게 말하는 절박한 톤",
                                 "빠르고 또렷하게 말하는 차분한 톤"]
    assert len(calls) == 1   # 재합성 대상 없음


def test_tempo_boundaries():
    assert N.tempo_for(5.7, 6) == 1.0          # ≤ budget-0.3 → 1.0
    assert N.tempo_for(1.0, 6) == 1.0
    assert N.tempo_for(5.71, 6) > 1.0
    assert abs(N.tempo_for(6.555, 6) - 1.15) < 1e-3


def test_too_long_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "synthesize_narrations", _fake_tts([]))
    monkeypatch.setattr(N, "_probe", lambda p: 12.0)   # 12.0/9.4 = 1.277 > 1.25
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
    p = N.PACE_PREFIX
    assert N.tone_for("ESTABLISH_THREAT") == p + "긴박한 톤"
    assert N.tone_for("ESCALATE") == p + "점점 고조되는 톤"
    assert N.tone_for("ENGAGE") == p + "단호한 톤"
    assert N.tone_for("CRISIS") == p + "절박한 톤"
    assert N.tone_for("TURN") == p + "반격하듯 힘찬 톤"
    assert N.tone_for("RESOLVE") == p + "차분한 톤"
    assert N.tone_for("NEXT_OMEN") == p + "낮고 불길한 톤"
    assert N.tone_for("AFTERMATH_CALM") == p + "자연스러운 톤" == N.tone_for(None)
    assert N.tone_for("NEXT_OMEN", retry=True) == "아주 빠르고 또렷하게 말하는 낮고 불길한 톤"


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
        N.fit_cuts([9.8] * 7)                            # 7×(9.8/1.25+0.6)=59.1 > 58
    with pytest.raises(N.NarrationError, match=r"beats=\[1\] secs=\[1.00,12.00\]"):
        N.fit_cuts([1.0, 12.0])                          # 12/9.4=1.277 > 1.25, 실패 시 전체 길이 기록


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



def test_fit_cuts_shadow_44139523787():
    """DN2026_0116 shadow 실측(무음 제거 전, 합 64.32초) — 1.25 상한에서 통과, 7번 비트 11.12초 포함."""
    secs = [8.20, 9.20, 8.04, 8.52, 9.04, 9.60, 11.12]
    f = N.fit_cuts(secs)
    assert N._composed_total(secs, [x["tempo"] for x in f]) <= N.TOTAL_MAX_SEC + 0.01
    assert all(x["tempo"] <= N.FIT_MAX_TEMPO for x in f) and all(5 <= x["cut_sec"] <= 10 for x in f)
    for s_, x in zip(secs, f):
        assert x["cut_sec"] >= s_ / x["tempo"] + N.CUT_TAIL_SEC - 1e-6


@pytest.mark.skipif(not HAS_FF, reason="ffmpeg/ffprobe 없음")
def test_trim_silence_cuts_head_and_tail(tmp_path):
    """DN2026_0116 — 앞 1초·뒤 1.5초 무음 + 2초 음성 → 약 2.2초."""
    p = str(tmp_path / "n.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=2:sample_rate=24000",
                    "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono:d=1.5",
                    "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", "-ac", "1", p],
                   check=True, capture_output=True, timeout=60)
    assert abs(N._probe(p) - 4.5) < 0.05
    out = N.trim_silence(p)
    assert out.endswith("_trim.wav") and 2.0 <= N._probe(out) <= 2.4


def test_trim_silence_fail_open(tmp_path):
    missing = str(tmp_path / "none.wav")
    assert N.trim_silence(missing) == missing



def _seq_tts(calls, plan):
    """호출 순서별 길이 계획 plan=[[secs...], [secs...]] 로 wav 생성."""
    def fake(narrations, output_dir="x", tones=None, voice_name=None, model_name=None, **_):
        import os
        os.makedirs(output_dir, exist_ok=True)
        secs = plan[len(calls)]
        calls.append({"n": list(narrations), "dir": output_dir, "tones": tones})
        paths = []
        for i, d in enumerate(secs):
            p = f"{output_dir}/narration_{i}.wav"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                            f"sine=frequency=330:duration={d}:sample_rate=24000", "-ac", "1", p],
                           check=True, capture_output=True, timeout=60)
            paths.append(p)
        return paths, None
    return fake


@pytest.mark.skipif(not HAS_FF, reason="ffmpeg/ffprobe 없음")
def test_slow_beat_retried_fast_and_replaced(tmp_path, monkeypatch):
    """DN2026_0116 D안 — 11.88초 비트만 '아주 빠르게' 재합성, 짧아지면 교체 후 통과."""
    calls = []
    monkeypatch.setattr(tts, "synthesize_narrations", _seq_tts(calls, [[3.0, 11.88, 4.0], [8.5]]))
    out = N.synthesize(_beats(), str(tmp_path))
    assert len(calls) == 2 and calls[1]["n"] == [_beats()[1]["narration"]]
    assert calls[1]["tones"][0].startswith(N.RETRY_PACE_PREFIX) and calls[1]["dir"].endswith("retry")
    assert abs(out[1]["sec"] - 8.5) < 0.05 and "retry" in out[1]["wav"]


@pytest.mark.skipif(not HAS_FF, reason="ffmpeg/ffprobe 없음")
def test_slow_beat_retry_longer_keeps_original_then_blocks(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tts, "synthesize_narrations", _seq_tts(calls, [[3.0, 12.5, 4.0], [12.9]]))
    with pytest.raises(N.NarrationError, match="narration_too_long beats=\\[1\\]"):
        N.synthesize(_beats(), str(tmp_path))
    assert len(calls) == 2



@pytest.mark.skipif(not HAS_FF, reason="ffmpeg/ffprobe 없음")
def test_slow_beat_retry_failure_keeps_original(tmp_path, monkeypatch):
    """R6-06 — 재합성 실패(경로 None·사유 있음)면 원본 유지 → fit_cuts 판정(10.8초는 1.149배로 통과)."""
    calls = []
    first = _seq_tts(calls, [[3.0, 10.8, 4.0]])

    def fake(narrations, output_dir="x", tones=None, **k):
        if calls:
            calls.append({"n": list(narrations)})
            return [None], "tts:quota_exhausted"
        return first(narrations, output_dir=output_dir, tones=tones, **k)
    monkeypatch.setattr(tts, "synthesize_narrations", fake)
    out = N.synthesize(_beats(), str(tmp_path))
    assert len(calls) == 2 and "/retry/" not in out[1]["wav"] and abs(out[1]["sec"] - 10.8) < 0.05
