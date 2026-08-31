import logging

from src.collector import fetch_market_data
from src.director import generate_connected_script
from src.drive_manager import record_step_finish, record_step_start, update_episode
from src.image_generator import generate_scene_images
from src.logging_config import configure_logging
from src.publisher import upload_to_youtube
from src.renderer import render_video

logger = logging.getLogger(__name__)


def main() -> int:
    log_path = configure_logging()
    logger.info("pipeline_started log_file=%s", log_path)

    episode_id = None
    video_id = None
    try:
        market_data = fetch_market_data()

        # script 생성 시 episode row가 status=script_ready 로 선기록된다
        # (src.director.generate_connected_script -> src.drive_manager.start_episode)
        script_data = generate_connected_script(market_data)
        episode_id = script_data.get("episode_id")

        step = record_step_start(episode_id, "image")
        image_paths = generate_scene_images(script_data)
        record_step_finish(step, "success" if image_paths else "skipped")

        step = record_step_start(episode_id, "render")
        video_file = render_video(script_data, image_paths=image_paths or None)
        update_episode(episode_id, status="rendered", video_path=video_file)
        record_step_finish(step, "success")

        step = record_step_start(episode_id, "upload")
        video_id = upload_to_youtube(video_file, script_data)
        update_episode(
            episode_id,
            status="published" if video_id else "rendered_no_upload",
            youtube_video_id=video_id,
        )
        record_step_finish(step, "success" if video_id else "skipped")
    except Exception:
        logger.exception("pipeline_failed")
        if episode_id:
            try:
                update_episode(episode_id, status="failed")
            except Exception:
                logger.exception("episode_failure_record_failed")
        return 1

    logger.info("pipeline_finished video_id=%s", video_id or "not_uploaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
