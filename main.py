import logging

from src.collector import fetch_market_data
from src.director import generate_connected_script
from src.drive_manager import record_step_finish, record_step_start, update_episode
from src.image_generator import generate_scene_images
from src.logging_config import configure_logging
from src.publisher import upload_to_youtube
from src.renderer import render_video
from src.tts import synthesize_narrations

logger = logging.getLogger(__name__)


def _build_scenes(storyboard, image_paths, audio_paths) -> list[dict]:
    """스토리보드 + 이미지 + 음성을 렌더러가 받는 장면 목록으로 합친다.

    이미지가 없는 비트는 렌더링할 수 없으므로 건너뛴다.
    """
    scenes = []
    for idx, beat in enumerate(storyboard):
        image = image_paths[idx] if idx < len(image_paths) else None
        if not image:
            continue
        scenes.append(
            {
                "image": image,
                "caption": beat.get("narration", ""),
                "audio": audio_paths[idx] if idx < len(audio_paths) else None,
            }
        )
    return scenes


def main() -> int:
    log_path = configure_logging()
    logger.info("pipeline_started log_file=%s", log_path)

    episode_id = None
    video_id = None
    final_status = "failed"
    degraded: list[str] = []
    try:
        market_data = fetch_market_data()

        # script 생성 시 episode row가 status=script_ready 로 선기록된다
        script_data = generate_connected_script(market_data)
        episode_id = script_data.get("episode_id")
        if script_data.get("degraded_reason"):
            degraded.append(script_data["degraded_reason"])

        storyboard = script_data.get("storyboard") or []
        narrations = [beat.get("narration", "") for beat in storyboard]

        step = record_step_start(episode_id, "image")
        image_paths, image_degraded = generate_scene_images(
            script_data, scenes=[beat.get("scene") for beat in storyboard]
        )
        if image_degraded:
            degraded.append(image_degraded)
        record_step_finish(
            step,
            "success" if any(image_paths) else "skipped",
            error_code=image_degraded,
        )

        step = record_step_start(episode_id, "tts")
        audio_paths, tts_degraded = synthesize_narrations(narrations)
        if tts_degraded:
            degraded.append(tts_degraded)
        record_step_finish(
            step,
            "success" if any(audio_paths) else "skipped",
            error_code=tts_degraded,
        )

        step = record_step_start(episode_id, "render")
        scenes = _build_scenes(storyboard, image_paths, audio_paths)
        video_file = render_video(script_data, scenes=scenes or None)
        update_episode(episode_id, status="rendered", video_path=video_file)
        record_step_finish(step, "success")

        step = record_step_start(episode_id, "upload")
        video_id = upload_to_youtube(video_file, script_data)
        if video_id:
            final_status = "published_degraded" if degraded else "published"
        else:
            final_status = "rendered_no_upload"
        update_episode(
            episode_id,
            status=final_status,
            youtube_video_id=video_id,
            degraded_reason=";".join(degraded) if degraded else None,
        )
        record_step_finish(step, "success" if video_id else "skipped")
    except Exception:
        logger.exception("pipeline_failed")
        if episode_id:
            try:
                update_episode(
                    episode_id,
                    status="failed",
                    degraded_reason=";".join(degraded) if degraded else None,
                )
            except Exception:
                logger.exception("episode_failure_record_failed")
        return 1

    if degraded:
        logger.warning("pipeline_degraded reasons=%s", ";".join(degraded))
    logger.info(
        "pipeline_finished video_id=%s status=%s", video_id or "not_uploaded", final_status
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
