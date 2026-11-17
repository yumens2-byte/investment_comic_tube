"""CAST 합성 테스트 — 실제 ffmpeg 로 lavfi 클립 생성 후 compose 검증 (DN2026_0114)."""
import json
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe 없음")

from src.cast.compose import ComposeError, compose  # noqa: E402


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
