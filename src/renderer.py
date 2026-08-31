import logging
import os
from pathlib import Path
import subprocess


logger = logging.getLogger(__name__)

def render_video(script_data):
    output_path = "output_short.mp4"
    if os.path.exists(output_path):
        os.remove(output_path)

    logger.info("render_started output=%s", output_path)
    
    ep_num = script_data.get('episode', 101)
    villain = script_data.get('villain', 'Unknown')
    text_content = f"EDT Universe Ep.{ep_num}\nVs. {villain}"
    
    # 8초 분량의 1080x1920 영상 생성 (실제 프로덕션 FFmpeg 명령어)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=8",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-vf", f"drawtext=text='{text_content}':fontcolor=orange:fontsize=64:x=(w-text_w)/2:y=(h-text_h)/2",
        "-c:v", "libx264", "-t", "8", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        output_path
    ]
    
    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_log = log_dir / "ffmpeg.log"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        ffmpeg_log.write_text(
            "$ " + " ".join(cmd) + "\n\nERROR\nffmpeg executable was not found in PATH\n",
            encoding="utf-8",
        )
        logger.exception("render_failed reason=ffmpeg_not_found ffmpeg_log=%s", ffmpeg_log)
        raise
    ffmpeg_log.write_text(
        "$ " + " ".join(cmd) + "\n\nSTDOUT\n" + result.stdout
        + "\nSTDERR\n" + result.stderr,
        encoding="utf-8",
    )
    if result.returncode != 0:
        logger.error(
            "render_failed exit_code=%s ffmpeg_log=%s stderr_tail=%s",
            result.returncode,
            ffmpeg_log,
            result.stderr[-1000:].replace("\n", " | "),
        )
        raise subprocess.CalledProcessError(result.returncode, cmd)
    logger.info(
        "render_finished output=%s bytes=%s ffmpeg_log=%s",
        output_path,
        os.path.getsize(output_path),
        ffmpeg_log,
    )
    return output_path
