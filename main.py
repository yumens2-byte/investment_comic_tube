import logging

from src.collector import fetch_market_data
from src.director import generate_connected_script
from src.logging_config import configure_logging
from src.renderer import render_video
from src.publisher import upload_to_youtube


logger = logging.getLogger(__name__)


def main() -> int:
    log_path = configure_logging()
    logger.info("pipeline_started log_file=%s", log_path)
    try:
        market_data = fetch_market_data()
        script = generate_connected_script(market_data)
        video_file = render_video(script)
        video_id = upload_to_youtube(video_file, script)
    except Exception:
        logger.exception("pipeline_failed")
        return 1
    logger.info("pipeline_finished video_id=%s", video_id or "not_uploaded")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
