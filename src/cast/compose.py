"""CAST-LINE 전용 합성 — fal i2v 컷 mp4 N개 → 1080x1920 Shorts (DN2026_0114).

src.renderer.render_video 는 정지 이미지 입력 전제(caption 필수, `-loop 1`)라
mp4 컷을 넘기면 실패한다. CAST는 i2v 모션을 유지한 채 정규화·연결·BGM 믹스만 한다.
renderer 는 _find_bgm 만 읽기 전용으로 재사용한다(무수정).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

try:
    from src.cast import VERSION
except ImportError:
    VERSION = "1.1.0"

logger = logging.getLogger("cast.compose")

# DN2026_0114 합성 규격
TARGET_W, TARGET_H, TARGET_FPS = 1080, 1920, 30
MIN_TOTAL_SEC, MAX_TOTAL_SEC = 15.0, 60.0
FADE_OUT_SEC = 1.5
THUMB_AT_SEC = 1.5
CLIP_TIMEOUT_SEC = 180
MUX_TIMEOUT_SEC = 300
PROBE_TIMEOUT_SEC = 30
VIDEO_FILTER = (
    f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
    f"crop={TARGET_W}:{TARGET_H},fps={TARGET_FPS},setsar=1,format=yuv420p"
)


class ComposeError(RuntimeError):
    """합성 실패·규격 위반 — 발행하지 않는다."""


def _run(cmd: list[str], timeout: int, what: str) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ComposeError(f"{what} timeout {timeout}s") from e
    except FileNotFoundError as e:
        raise ComposeError(f"{what} 실행 불가: {e.filename}") from e
    if proc.returncode != 0:
        raise ComposeError(f"{what} 실패 rc={proc.returncode}: {(proc.stderr or '')[-300:]}")
    return proc


def _probe(path: str) -> dict:
    proc = _run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height", "-of", "json", path],
        PROBE_TIMEOUT_SEC, f"ffprobe {os.path.basename(path)}",
    )
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        raise ComposeError(f"ffprobe 출력 해석 실패: {path}") from e


def _duration(info: dict) -> float:
    try:
        return float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        return 0.0


def compose(cuts: list[dict], villain: str | None, out_path: str,
            work_dir: str = "artifacts_cast/compose") -> dict:
    """컷 mp4 들을 합성해 Shorts 한 편을 만든다.  # DN2026_0114

    반환: {"path": out_path, "duration_sec": float, "thumbnail": str}
    위반(해상도·오디오·15~60초)·ffmpeg 실패 시 ComposeError.
    """
    if not cuts:
        raise ComposeError("컷 없음")
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    ordered = sorted(cuts, key=lambda c: int(c["cut"]))
    logger.info("[CAST] compose v%s 시작 cuts=%d out=%s", VERSION, len(ordered), out_path)

    # 1) 클립 정규화 — 정지 이미지 옵션·zoompan 없이 모션 유지
    segments: list[Path] = []
    for i, cut in enumerate(ordered):
        src = cut.get("path")
        if not src or not os.path.exists(src):
            raise ComposeError(f"컷 파일 없음 cut={cut.get('cut')}")
        dur = cut.get("duration_sec")
        if not isinstance(dur, (int, float)) or dur <= 0:
            dur = _duration(_probe(src))
        if dur <= 0:
            raise ComposeError(f"컷 길이 확인 불가 cut={cut.get('cut')}")
        seg = work / f"seg_{i:02d}.mp4"
        _run(["ffmpeg", "-y", "-i", src, "-t", f"{float(dur):.3f}", "-vf", VIDEO_FILTER,
              "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(seg)],
             CLIP_TIMEOUT_SEC, f"normalize cut={cut.get('cut')}")
        segments.append(seg)
        logger.info("[CAST] compose segment cut=%s dur=%.2f", cut.get("cut"), float(dur))

    # 2) concat demuxer — 무음 영상
    list_file = work / "concat.txt"
    list_file.write_text(
        "".join(f"file '{seg.resolve().as_posix()}'\n" for seg in segments), encoding="utf-8")
    silent = work / "silent.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
          "-c", "copy", str(silent)], MUX_TIMEOUT_SEC, "concat")
    total = _duration(_probe(str(silent)))
    if total <= 0:
        raise ComposeError("연결 영상 길이 확인 불가")

    # 3) BGM 믹스 — renderer._find_bgm 읽기 전용 재사용, 없으면 무음 트랙
    from src.renderer import _find_bgm
    bgm = _find_bgm(villain)
    if bgm:
        audio_in = ["-stream_loop", "-1", "-i", bgm]
    else:
        audio_in = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    fade_st = max(0.0, total - FADE_OUT_SEC)
    afilter = f"afade=t=out:st={fade_st:.3f}:d={FADE_OUT_SEC},loudnorm=I=-14:TP=-1.5:LRA=11"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-i", str(silent), *audio_in,
          "-map", "0:v:0", "-map", "1:a:0", "-af", afilter, "-shortest",
          "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
          "-movflags", "+faststart", out_path], MUX_TIMEOUT_SEC, "mux")
    logger.info("[CAST] compose mux bgm=%s", os.path.basename(bgm) if bgm else "anullsrc")

    # 4) 썸네일 — 첫 컷 1.5초(짧으면 중간) 프레임
    first_dur = _duration(_probe(str(segments[0])))
    at = THUMB_AT_SEC if first_dur > THUMB_AT_SEC else max(0.0, first_dur / 2)
    thumb = work / "thumb.png"
    _run(["ffmpeg", "-y", "-ss", f"{at:.3f}", "-i", str(segments[0]),
          "-frames:v", "1", str(thumb)], CLIP_TIMEOUT_SEC, "thumbnail")
    if not thumb.exists():
        raise ComposeError("썸네일 생성 실패")

    # 5) 규격 검증
    info = _probe(out_path)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = _duration(info)
    if not video or (video.get("width"), video.get("height")) != (TARGET_W, TARGET_H):
        raise ComposeError(f"해상도 위반: {video and (video.get('width'), video.get('height'))}")
    if not has_audio:
        raise ComposeError("오디오 스트림 없음")
    if not (MIN_TOTAL_SEC <= duration <= MAX_TOTAL_SEC):
        raise ComposeError(f"길이 위반 {duration:.2f}s (허용 {MIN_TOTAL_SEC}~{MAX_TOTAL_SEC}s)")
    logger.info("[CAST] compose done path=%s duration=%.2f thumb=%s", out_path, duration, thumb)
    return {"path": out_path, "duration_sec": round(duration, 3), "thumbnail": str(thumb)}
