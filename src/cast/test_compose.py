"""CAST 합성 테스트 — 실제 ffmpeg 로 lavfi 클립 생성 후 compose 검증 (DN2026_0114)."""
import json
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe 없음")

from src.cast.compose import ComposeError, compose


def _clip(path, size="720x1280", sec=6):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=24:duration={sec}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={sec}",
         "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)],
        check=True, capture_output=True, timeout=120)
    return str(path)


def _probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
         "-of", "json", path], check=True, capture_output=True, text=True, timeout=30).stdout
    return json.loads(out)


@pytest.fixture
def no_bgm(tmp_path, monkeypatch):
    empty = tmp_path / "bgm_empty"
    empty.mkdir()
    monkeypatch.setenv("BGM_DIR", str(empty))
    return empty


def test_compose_three_clips(tmp_path, no_bgm):
    cuts = [
        {"cut": 3, "path": _clip(tmp_path / "c3.mp4"), "duration_sec": 6},
        {"cut": 1, "path": _clip(tmp_path / "c1.mp4"), "duration_sec": 6},
        {"cut": 2, "path": _clip(tmp_path / "c2.mp4", size="768x1360"), "duration_sec": None},
    ]
    out = tmp_path / "out" / "cast_short.mp4"
    res = compose(cuts, "Debt Titan", str(out), work_dir=str(tmp_path / "work"))
    info = _probe(str(out))
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert 17.0 <= float(info["format"]["duration"]) <= 19.0
    assert 17.0 <= res["duration_sec"] <= 19.0
    assert (tmp_path / "work" / "thumb.png").exists() and res["thumbnail"].endswith("thumb.png")


def test_compose_too_short_raises(tmp_path, no_bgm):
    cuts = [{"cut": i, "path": _clip(tmp_path / f"s{i}.mp4", sec=5), "duration_sec": 5}
            for i in (1, 2)]
    with pytest.raises(ComposeError, match="길이"):
        compose(cuts, None, str(tmp_path / "short.mp4"), work_dir=str(tmp_path / "w"))


def test_compose_missing_clip_raises(tmp_path, no_bgm):
    with pytest.raises(ComposeError):
        compose([{"cut": 1, "path": str(tmp_path / "nope.mp4")}], None,
                str(tmp_path / "x.mp4"), work_dir=str(tmp_path / "w"))


# ── DN2026_0115 P2: 내레이션 믹스·컷 트림·자막 ─────────────────────────────────
import hashlib

from src import caption_policy


def _font_ok():
    try:
        return hashlib.sha256(caption_policy.FONT_PATH.read_bytes()).hexdigest() == caption_policy.FONT_SHA256
    except OSError:
        return False


def _wav(path, sec):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={sec}",
                    "-ac", "1", "-ar", "24000", str(path)], check=True, capture_output=True, timeout=60)
    return str(path)


def _narr_inputs(tmp_path):
    from src.cast.narration import tempo_for
    cuts = [{"cut": i, "path": _clip(tmp_path / f"n{i}.mp4"), "duration_sec": 6} for i in (1, 2, 3)]
    narration = []
    for i, sec in zip((1, 2, 3), (2.0, 4.0, 6.2)):
        narration.append({"cut": i, "wav": _wav(tmp_path / f"v{i}.wav", sec), "sec": sec,
                          "tempo": tempo_for(sec, 6)})
    return cuts, narration


def _mean_volume(path, start, dur):
    out = subprocess.run(["ffmpeg", "-ss", str(start), "-t", str(dur), "-i", path, "-af", "volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True, timeout=60, check=True).stderr
    line = next(ln for ln in out.splitlines() if "mean_volume" in ln)
    return float(line.split("mean_volume:")[1].split("dB")[0])


def test_compose_with_narration_segments(tmp_path, no_bgm):
    cuts, narration = _narr_inputs(tmp_path)
    out = tmp_path / "o" / "narr.mp4"
    res = compose(cuts, "Debt Titan", str(out), work_dir=str(tmp_path / "w"), narration=narration)
    # target = min(6, max(5, sec/tempo + 0.6)): 2.0→5.0, 4.0→5.0, 6.2(tempo 1.0877)→min(6, 6.3)=6.0
    assert [s["target_sec"] for s in res["segments"]] == [5.0, 5.0, 6.0]
    assert res["segments"][0]["tempo"] == 1.0 and abs(res["segments"][2]["tempo"] - 6.2 / 5.7) < 1e-3
    info = _probe(str(out))
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert 15.0 <= res["duration_sec"] <= 60.0 and abs(res["duration_sec"] - 16.0) < 0.3
    # 내레이션은 컷 시작 오프셋에 배치 — 컷2(5.0~7.0s)는 소리, 컷1 끝(3.0~4.5s)은 무음(BGM 없음)
    assert _mean_volume(str(out), 5.3, 1.5) > _mean_volume(str(out), 2.5, 2.0) + 20


def test_compose_narration_missing_cut_raises(tmp_path, no_bgm):
    cuts, narration = _narr_inputs(tmp_path)
    with pytest.raises(ComposeError, match="내레이션 누락"):
        compose(cuts, None, str(tmp_path / "x.mp4"), work_dir=str(tmp_path / "w"), narration=narration[:2])


def test_compose_captions_count_mismatch(tmp_path, no_bgm):
    cuts, _ = _narr_inputs(tmp_path)
    with pytest.raises(ComposeError, match="자막 수"):
        compose(cuts, None, str(tmp_path / "x.mp4"), work_dir=str(tmp_path / "w"), captions=["a"])


@pytest.mark.skipif(not _font_ok(), reason="자막 폰트(NotoSansCJKkr-Bold.otf, sha 고정) 없음")
def test_compose_with_narration_and_captions(tmp_path, no_bgm):
    cuts, narration = _narr_inputs(tmp_path)
    caps = ["부채 거인이 도시를 짓눌렀다.", "히어로들이 반격을 준비한다.", "원칙을 지킨 쪽이 버텼다."]
    out = tmp_path / "o" / "cap.mp4"
    res = compose(cuts, "Debt Titan", str(out), work_dir=str(tmp_path / "w"),
                  narration=narration, captions=caps)
    assert (tmp_path / "w" / "cap_00-line-0.txt").exists()
    info = _probe(str(out))
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert 15.0 <= res["duration_sec"] <= 60.0
