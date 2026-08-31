"""8~N초 세로형(1080x1920) 영상 렌더링.

두 가지 모드:
  - 슬라이드쇼 모드: image_paths가 주어지면 각 이미지를 자막과 함께 이어붙인다
    (Gemini 이미지 생성 결과 사용, 안3 설계).
  - 텍스트카드 모드(기본 폴백): image_paths가 없거나 슬라이드쇼 렌더링이
    실패하면 기존 검은 배경 + 텍스트 방식으로 자동 대체한다. 파이프라인은
    영상 생성 실패로 절대 죽지 않는다.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

SEGMENT_DURATION_SEC = 4


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


def _render_slideshow(script_data: dict, image_paths: list[str], output_path: str, ffmpeg_log: Path) -> None:
    ep_num = script_data.get("episode", 101)
    villain = script_data.get("villain", "Unknown")
    narration = script_data.get("narration", "")

    captions = [f"EDT Universe Ep.{ep_num}\nVs. {villain}"]
    if narration:
        captions.append(narration)
    while len(captions) < len(image_paths):
        captions.append(captions[-1])

    tmp_dir = Path(output_path).parent / "_segments"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    segment_paths = []

    for idx, image_path in enumerate(image_paths):
        caption = captions[idx] if idx < len(captions) else ""
        segment_path = tmp_dir / f"segment_{idx}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-t", str(SEGMENT_DURATION_SEC), "-i", image_path,
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-vf", (
                "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"drawtext=text='{_escape_drawtext(caption)}':fontcolor=white:fontsize=54:"
                "box=1:boxcolor=black@0.5:boxborderw=20:x=(w-text_w)/2:y=h-th-160"
            ),
            "-c:v", "libx264", "-t", str(SEGMENT_DURATION_SEC), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(segment_path),
        ]
        _run_ffmpeg(cmd, ffmpeg_log, append=(idx > 0))
        segment_paths.append(segment_path)

    filelist = tmp_dir / "filelist.txt"
    filelist.write_text("\n".join(f"file '{p.resolve()}'" for p in segment_paths), encoding="utf-8")

    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(filelist),
        "-c", "copy",
        output_path,
    ]
    _run_ffmpeg(concat_cmd, ffmpeg_log, append=True)


def render_video(script_data: dict, image_paths: list[str] | None = None) -> str:
    output_path = "output_short.mp4"
    if os.path.exists(output_path):
        os.remove(output_path)

    mode = "slideshow" if image_paths else "text_card"
    logger.info("render_started output=%s mode=%s", output_path, mode)

    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_log = log_dir / "ffmpeg.log"

    if image_paths:
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

    logger.info(
        "render_finished output=%s bytes=%s ffmpeg_log=%s",
        output_path,
        os.path.getsize(output_path),
        ffmpeg_log,
    )
    return output_path
