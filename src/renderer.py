"""세로형(1080x1920) 숏폼 영상 렌더링.

세 가지 모드:
  - 스토리보드 모드: scenes(이미지+자막+내레이션 음성)를 이어붙여 30초 내외 영상 생성.
    각 장면 길이는 내레이션 오디오 길이를 따라가며, 오디오가 없으면 고정 길이를 쓴다.
  - 슬라이드쇼 모드: 이미지 목록만 있을 때 고정 길이로 이어붙인다.
  - 텍스트카드 모드(최종 폴백): 이미지가 하나도 없거나 위 두 모드가 실패하면
    검은 배경 + 텍스트로 대체한다. 파이프라인은 렌더링 실패로 죽지 않는다.
"""

from __future__ import annotations

import json
import logging
import os
import random
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

SEGMENT_DURATION_SEC = 5.0
MIN_SEGMENT_SEC = 3.5
AUDIO_TAIL_PAD_SEC = 0.6

BGM_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg"}

# Ken Burns 효과: 같은 이미지를 여러 비트에 재사용해도 화면이 단조롭지 않도록
# 장면마다 줌 방향을 교대한다 (비용 절감을 위한 이미지 재사용의 보완책).
KEN_BURNS_FPS = 30
KEN_BURNS_MAX_ZOOM = 1.12
KEN_BURNS_SPEED = 0.0012


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", r"\\").replace("'", r"\'").replace(":", r"\:")


def _run_ffmpeg(cmd: list[str], ffmpeg_log: Path, append: bool = False) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    mode = "a" if append else "w"
    with ffmpeg_log.open(mode, encoding="utf-8") as f:
        f.write("$ " + " ".join(cmd) + "\n\nSTDOUT\n" + result.stdout + "\nSTDERR\n" + result.stderr + "\n\n")
    if result.returncode != 0:
        logger.error(
            "ffmpeg_step_failed exit_code=%s stderr_tail=%s",
            result.returncode,
            result.stderr[-1000:].replace("\n", " | "),
        )
        raise subprocess.CalledProcessError(result.returncode, cmd)


def _probe_duration(path: str) -> float | None:
    """미디어 파일 길이(초)를 반환한다. 실패 시 None."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-print_format", "json",
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return None
        return float(json.loads(result.stdout)["format"]["duration"])
    except (ValueError, KeyError, json.JSONDecodeError, FileNotFoundError):
        return None


def _find_bgm() -> str | None:
    """BGM 디렉터리에서 배경음 파일을 하나 무작위로 고른다.

    파일이 없으면 None -- 이 경우 내레이션만 남는다.
    저작권 리스크를 피하기 위해 코드가 음원을 자동으로 받아오지 않는다.
    무작위 선택은 X 안티봇 원칙(동일 패턴 반복 금지)에 따른 것이다.
    """
    bgm_dir = Path(os.getenv("BGM_DIR", "assets/bgm"))
    if not bgm_dir.is_dir():
        return None
    candidates = sorted(p for p in bgm_dir.iterdir() if p.suffix.lower() in BGM_EXTENSIONS)
    if not candidates:
        return None
    return str(random.choice(candidates))


def _apply_bgm(video_path: str, bgm_path: str, ffmpeg_log: Path) -> None:
    """기존 오디오(내레이션) 위에 BGM을 낮은 볼륨으로 깐다.

    내레이션 트랙이 무음이면 결과적으로 BGM만 들린다.
    실패 시 원본을 그대로 두어 영상 자체는 보존한다.
    """
    mixed_path = f"{video_path}.bgm.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-i", bgm_path,
        "-filter_complex",
        "[1:a]volume=0.18[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[aout]",
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        mixed_path,
    ]
    try:
        _run_ffmpeg(cmd, ffmpeg_log, append=True)
    except Exception as e:  # noqa: BLE001 - BGM 합성 실패는 원본 유지로 폴백
        logger.warning("bgm_mix_failed reason=%s: %s -- keeping original audio", type(e).__name__, e)
        if os.path.exists(mixed_path):
            os.remove(mixed_path)
        return

    os.replace(mixed_path, video_path)
    logger.info("bgm_applied source=%s", bgm_path)


def _render_text_card(script_data: dict, output_path: str, ffmpeg_log: Path) -> None:
    ep_num = script_data.get("episode", 101)
    villain = script_data.get("villain", "Unknown")
    text_content = f"EDT Universe Ep.{ep_num}\nVs. {villain}"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=8",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-vf", (
            f"drawtext=text='{_escape_drawtext(text_content)}':fontcolor=orange:fontsize=64:"
            "x=(w-text_w)/2:y=(h-text_h)/2"
        ),
        "-c:v", "libx264", "-t", "8", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        output_path,
    ]
    try:
        _run_ffmpeg(cmd, ffmpeg_log)
    except FileNotFoundError:
        ffmpeg_log.write_text(
            "$ " + " ".join(cmd) + "\n\nERROR\nffmpeg executable was not found in PATH\n",
            encoding="utf-8",
        )
        logger.exception("render_failed reason=ffmpeg_not_found ffmpeg_log=%s", ffmpeg_log)
        raise


def _ken_burns_filter(duration: float, zoom_in: bool) -> str:
    """장면 길이에 맞춘 zoompan 필터식을 만든다."""
    frames = max(1, int(duration * KEN_BURNS_FPS))
    if zoom_in:
        z = f"min(1+{KEN_BURNS_SPEED}*on,{KEN_BURNS_MAX_ZOOM})"
    else:
        z = f"max({KEN_BURNS_MAX_ZOOM}-{KEN_BURNS_SPEED}*on,1.0)"
    return (
        f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s=1080x1920:fps={KEN_BURNS_FPS}"
    )


def _render_segment(
    image_path: str,
    caption: str,
    audio_path: str | None,
    duration: float,
    segment_path: Path,
    ffmpeg_log: Path,
    append: bool,
    zoom_in: bool = True,
) -> None:
    """이미지 1장 + (있으면) 내레이션으로 장면 하나를 렌더링한다.

    자막(drawtext)은 사용하지 않는다. 한국어 폰트/개행 처리에서 깨짐이 발생했고,
    내레이션 음성이 같은 내용을 전달하므로 화면에는 이미지만 남긴다.
    caption 인자는 호출부 호환을 위해 남겨두되 렌더링에는 쓰지 않는다.
    """
    video_filter = (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"{_ken_burns_filter(duration, zoom_in)}"
    )

    cmd = ["ffmpeg", "-y", "-loop", "1", "-t", f"{duration:.3f}", "-i", image_path]
    if audio_path:
        cmd += ["-i", audio_path]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]

    cmd += [
        "-vf", video_filter,
        "-c:v", "libx264", "-t", f"{duration:.3f}", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        "-af", f"apad=whole_dur={duration:.3f}",
        str(segment_path),
    ]
    _run_ffmpeg(cmd, ffmpeg_log, append=append)


def _concat_segments(segment_paths: list[Path], output_path: str, tmp_dir: Path, ffmpeg_log: Path) -> None:
    filelist = tmp_dir / "filelist.txt"
    filelist.write_text("\n".join(f"file '{p.resolve()}'" for p in segment_paths), encoding="utf-8")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(filelist),
        "-c", "copy",
        output_path,
    ]
    _run_ffmpeg(cmd, ffmpeg_log, append=True)


def _render_storyboard(scenes: list[dict], output_path: str, ffmpeg_log: Path) -> None:
    """스토리보드 장면 목록을 이어붙여 영상을 만든다.

    scenes 항목: {"image", "caption", "audio"}
    """
    tmp_dir = Path(output_path).parent / "_segments"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    segment_paths: list[Path] = []

    for idx, scene in enumerate(scenes):
        image_path = scene.get("image")
        if not image_path:
            continue

        audio_path = scene.get("audio")
        duration = SEGMENT_DURATION_SEC
        if audio_path:
            probed = _probe_duration(audio_path)
            if probed:
                duration = max(MIN_SEGMENT_SEC, probed + AUDIO_TAIL_PAD_SEC)

        segment_path = tmp_dir / f"segment_{idx}.mp4"
        _render_segment(
            image_path=image_path,
            caption=scene.get("caption", ""),
            audio_path=audio_path,
            duration=duration,
            segment_path=segment_path,
            ffmpeg_log=ffmpeg_log,
            append=bool(segment_paths),
            zoom_in=(idx % 2 == 0),
        )
        segment_paths.append(segment_path)

    if not segment_paths:
        raise ValueError("no renderable scenes")

    _concat_segments(segment_paths, output_path, tmp_dir, ffmpeg_log)


def _render_slideshow(script_data: dict, image_paths: list[str], output_path: str, ffmpeg_log: Path) -> None:
    """이미지만 있을 때 고정 길이 슬라이드쇼로 렌더링한다."""
    ep_num = script_data.get("episode", 101)
    villain = script_data.get("villain", "Unknown")
    narration = script_data.get("narration", "")

    captions = [f"EDT Universe Ep.{ep_num}\nVs. {villain}"]
    if narration:
        captions.append(narration)
    while len(captions) < len(image_paths):
        captions.append(captions[-1])

    scenes = [
        {"image": path, "caption": captions[idx], "audio": None}
        for idx, path in enumerate(image_paths)
    ]
    _render_storyboard(scenes, output_path, ffmpeg_log)


def render_video(
    script_data: dict,
    image_paths: list[str] | None = None,
    scenes: list[dict] | None = None,
) -> str:
    output_path = "output_short.mp4"
    if os.path.exists(output_path):
        os.remove(output_path)

    if scenes and any(s.get("image") for s in scenes):
        mode = "storyboard"
    elif image_paths:
        mode = "slideshow"
    else:
        mode = "text_card"
    logger.info("render_started output=%s mode=%s", output_path, mode)

    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_log = log_dir / "ffmpeg.log"

    if mode == "storyboard":
        try:
            _render_storyboard(scenes, output_path, ffmpeg_log)
        except Exception as e:  # noqa: BLE001 - 스토리보드 실패는 텍스트카드로 폴백
            logger.warning(
                "storyboard_render_failed reason=%s: %s -- falling back to text_card",
                type(e).__name__, e,
            )
            _render_text_card(script_data, output_path, ffmpeg_log)
    elif mode == "slideshow":
        try:
            _render_slideshow(script_data, image_paths, output_path, ffmpeg_log)
        except Exception as e:  # noqa: BLE001 - 슬라이드쇼 실패는 텍스트카드로 폴백
            logger.warning(
                "slideshow_render_failed reason=%s: %s -- falling back to text_card",
                type(e).__name__, e,
            )
            _render_text_card(script_data, output_path, ffmpeg_log)
    else:
        _render_text_card(script_data, output_path, ffmpeg_log)

    bgm_path = _find_bgm()
    if bgm_path:
        _apply_bgm(output_path, bgm_path, ffmpeg_log)
    else:
        logger.info("bgm_skipped reason=no_bgm_file")

    duration = _probe_duration(output_path)
    logger.info(
        "render_finished output=%s bytes=%s duration=%s ffmpeg_log=%s",
        output_path,
        os.path.getsize(output_path),
        f"{duration:.2f}" if duration else "unknown",
        ffmpeg_log,
    )
    return output_path
