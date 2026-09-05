import logging

from src.collector import fetch_market_data
from src.director import generate_connected_script
from src.drive_manager import record_step_finish, record_step_start, update_episode
from src.image_generator import generate_scene_images
from src.logging_config import configure_logging
from src.publisher import upload_to_youtube
from src.renderer import render_video
from src.story import BEAT_IMAGE_SLOT, SLOT_SCENES
from src.tts import synthesize_narrations
from src.validation import (
    ValidationError,
    validate_market_data,
    validate_not_published_today,
    validate_render_environment,
    validate_storyboard,
)

logger = logging.getLogger(__name__)


def _pick_image(image_paths: list, slot: int):
    """비트의 이미지 슬롯에 대응하는 이미지를 고른다.

    비용 절감을 위해 이미지 수(기본 3장)가 비트 수(6개)보다 적으므로
    슬롯 매핑으로 재사용한다. 해당 슬롯 이미지가 실패했으면
    생성에 성공한 다른 이미지로 대체해 비트가 통째로 사라지지 않게 한다.
    """
    if not image_paths:
        return None
    if slot < len(image_paths) and image_paths[slot]:
        return image_paths[slot]
    available = [p for p in image_paths if p]
    if not available:
        return None
    return available[slot % len(available)]


def _build_scenes(storyboard, image_paths, audio_paths) -> list[dict]:
    """스토리보드 + 이미지 + 음성을 렌더러가 받는 장면 목록으로 합친다.

    이미지는 슬롯 매핑으로 재사용되며, 쓸 이미지가 하나도 없는 비트는 건너뛴다.
    """
    scenes = []
    for idx, beat in enumerate(storyboard):
        slot = BEAT_IMAGE_SLOT[idx] if idx < len(BEAT_IMAGE_SLOT) else 0
        image = _pick_image(image_paths, slot)
        if not image:
            continue
        scenes.append(
            {
                "image": image,
                "caption": beat.get("narration", ""),
                "audio": audio_paths[idx] if idx < len(audio_paths) else None,
                # 훅 비트는 renderer 가 전용 연출(펀치인/흔들림/중앙자막/SFX)을 적용한다
                "is_hook": bool(beat.get("is_hook")),
                "sfx": beat.get("sfx"),
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
    current_step = None
    try:
        # 렌더링 환경(한글 폰트) 먼저 확인한다. 두부 자막 영상이 발행되면
        # YouTube에 영구히 남으므로 유료 API를 쓰기 전에 중단하는 것이 싸다.
        validate_render_environment()
        # 같은 날 두 번 돌면 같은 시세로 같은 이야기가 나간다 (Ep.1/Ep.2 사례)
        validate_not_published_today()

        market_data = fetch_market_data()

        # 필수 지표가 없으면 여기서 중단한다. start_episode() 이전이므로
        # DB에 고아 회차 row가 남지 않고, YouTube에도 아무것도 올라가지 않는다.
        validate_market_data(market_data)

        # script 생성 시 episode row가 status=script_ready 로 선기록된다
        script_data = generate_connected_script(market_data)
        episode_id = script_data.get("episode_id")
        if script_data.get("degraded_reason"):
            degraded.append(script_data["degraded_reason"])

        storyboard = script_data.get("storyboard") or []
        validate_storyboard(storyboard)
        narrations = [beat.get("narration", "") for beat in storyboard]

        step = current_step = record_step_start(episode_id, "image")
        # 비용 통제: 비트(6개)마다가 아니라 슬롯(기본 3개)만큼만 생성한다
        image_paths, image_degraded = generate_scene_images(
            script_data, scenes=SLOT_SCENES
        )
        if image_degraded:
            degraded.append(image_degraded)
        record_step_finish(
            step,
            "success" if any(image_paths) else "skipped",
            error_code=image_degraded,
        )
        current_step = None

        step = current_step = record_step_start(episode_id, "tts")
        tones = [beat.get("tts_tone") for beat in storyboard]
        audio_paths, tts_degraded = synthesize_narrations(narrations, tones=tones)
        if tts_degraded:
            degraded.append(tts_degraded)
        record_step_finish(
            step,
            "success" if any(audio_paths) else "skipped",
            error_code=tts_degraded,
        )
        current_step = None

        step = current_step = record_step_start(episode_id, "render")
        scenes = _build_scenes(storyboard, image_paths, audio_paths)
        video_file = render_video(script_data, scenes=scenes or None)
        update_episode(episode_id, status="rendered", video_path=video_file)
        record_step_finish(step, "success")
        current_step = None

        step = current_step = record_step_start(episode_id, "upload")
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
        current_step = None
    except ValidationError as e:
        # 검증 실패는 버그가 아니라 '발행하지 않기로 한 정상 판단'이다.
        # traceback 대신 사유만 남기고, 부분 생성물이 있으면 실패로 기록한다.
        logger.error("pipeline_aborted_validation reason=%s", e)
        if current_step:
            record_step_finish(current_step, "failed", error_code=str(e)[:200])
        if episode_id:
            try:
                update_episode(episode_id, status="aborted_validation", degraded_reason=str(e))
            except Exception:
                logger.exception("episode_abort_record_failed")
        return 1
    except Exception as e:
        logger.exception("pipeline_failed")
        # 실패한 step 을 running 으로 방치하면 관측이 어긋난다 (Ep.1 upload 고아 사례)
        if current_step:
            record_step_finish(current_step, "failed", error_code=f"{type(e).__name__}"[:200])
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
