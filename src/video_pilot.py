"""Render and validate a vertical storyboard preview without publishing it."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Callable

from src.episode import Episode


logger = logging.getLogger(__name__)
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class VideoArtifact:
    path: Path
    sha256: str
    duration_seconds: float
    width: int
    height: int
    video_codec: str
    audio_codec: str


class VideoValidationError(RuntimeError):
    """Raised when FFmpeg output does not satisfy the Shorts pilot contract."""


def _escape_filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _font_option() -> str:
    configured = os.getenv("VIDEO_FONT_FILE")
    candidates = [
        configured,
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return f"fontfile='{_escape_filter_path(Path(candidate))}':"
    return ""


def build_video_filter(episode: Episode, caption_files: list[Path]) -> str:
    filters = []
    start = 0.0
    font = _font_option()
    for sequence, caption_file in zip(episode.sequences, caption_files, strict=True):
        end = start + sequence.duration_seconds
        filters.append(
            "drawtext="
            f"{font}textfile='{_escape_filter_path(caption_file)}':reload=0:"
            "fontcolor=white:fontsize=64:line_spacing=18:"
            "box=1:boxcolor=black@0.55:boxborderw=28:"
            "x=(w-text_w)/2:y=h*0.72:"
            f"enable='between(t,{start:g},{end:g})'"
        )
        start = end
    return ",".join(filters)


def _run(command: list[str], runner: CommandRunner) -> subprocess.CompletedProcess[str]:
    logger.debug("media_command executable=%s", command[0])
    try:
        result = runner(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise VideoValidationError(f"{command[0]} executable was not found in PATH") from exc
    if result.returncode:
        raise VideoValidationError(
            f"{command[0]} failed with exit code {result.returncode}: {result.stderr[-1000:]}"
        )
    return result


def render_storyboard_preview(
    episode: Episode,
    artifact_dir: str | Path = "artifacts",
    runner: CommandRunner = subprocess.run,
) -> VideoArtifact:
    """Render colored storyboard cards, then verify the resulting media with ffprobe."""
    target = Path(artifact_dir) / episode.episode_id
    target.mkdir(parents=True, exist_ok=True)
    output = target / "preview.mp4"
    if output.exists():
        output.unlink()

    caption_files = []
    for sequence in episode.sequences:
        path = target / f"{sequence.sequence_id}_caption.txt"
        path.write_text(sequence.caption, encoding="utf-8")
        caption_files.append(path)

    total = episode.total_duration_seconds
    video_filter = build_video_filter(episode, caption_files)
    ffmpeg = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
        "-f", "lavfi", "-i", f"color=c=0x111827:s=1080x1920:r=25:d={total:g}",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-vf", video_filter,
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-t", f"{total:g}", "-shortest", str(output),
    ]
    logger.info(
        "storyboard_render_started episode_id=%s sequences=%s duration_seconds=%s",
        episode.episode_id, len(episode.sequences), total,
    )
    render_result = _run(ffmpeg, runner)
    (target / "ffmpeg-preview.log").write_text(render_result.stderr, encoding="utf-8")

    probe = _run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height",
        "-of", "json", str(output),
    ], runner)
    try:
        media = json.loads(probe.stdout)
        video = next(stream for stream in media["streams"] if stream["codec_type"] == "video")
        audio = next(stream for stream in media["streams"] if stream["codec_type"] == "audio")
        duration = float(media["format"]["duration"])
        width, height = int(video["width"]), int(video["height"])
    except (KeyError, TypeError, ValueError, StopIteration, json.JSONDecodeError) as exc:
        raise VideoValidationError("ffprobe returned incomplete media metadata") from exc
    if (width, height) != (1080, 1920):
        raise VideoValidationError(f"expected 1080x1920, got {width}x{height}")
    if abs(duration - total) > 0.5:
        raise VideoValidationError(f"expected {total}s duration, got {duration}s")
    if not output.is_file():
        raise VideoValidationError("ffmpeg reported success but preview.mp4 was not created")

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    artifact = VideoArtifact(
        path=output,
        sha256=digest,
        duration_seconds=duration,
        width=width,
        height=height,
        video_codec=str(video["codec_name"]),
        audio_codec=str(audio["codec_name"]),
    )
    logger.info(
        "storyboard_render_finished episode_id=%s video=%s sha256=%s",
        episode.episode_id, output, digest,
    )
    return artifact
