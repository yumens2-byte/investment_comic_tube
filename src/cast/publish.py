"""CAST-LINE render/publish — 기존 renderer·publisher 재용 연결.

설계 원칙 (상세설계 v1.0 §6):
- 렌더는 src.renderer.render_video storyboard 모드 재용 — CAST는 scenes를 만들어 넘긴다.
- 발행은 src.publisher.upload_to_youtube 재용 — 메타데이터에 line='cast' 식별만 추가.
- 중복 게이트는 라인별 분리: has_published_today(line) — 기존 daily 게이트 무변경.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("cast.publish")


class PublishBlocked(RuntimeError):
    """발행 차단 — 모드 게이트·중복 게이트·원장 오류."""


def build_cast_scenes(cut_videos: list[dict]) -> list[dict]:
    """generate 산출 컷들 → render_video storyboard scenes 형식.

    T3 7컷 각각이 하나의 scene. 컷 영상은 이미 fal 생성물이라 renderer는
    각 scene을 clip으로 이어붙이는 역할만 담당한다.
    """
    scenes = []
    for i, cut in enumerate(sorted(cut_videos, key=lambda c: c["cut"])):
        scenes.append({
            "image": cut["path"],
            "duration_sec": cut.get("duration_sec"),
            "beat_no": i + 1,
            "source": "fal_i2v",
        })
    return scenes


def cast_episode_metadata(tier: str, market_snapshot: dict | None) -> dict:
    """CAST 라인 발행 메타데이터 — 기존 publisher 계약 필드 유지 + line 식별."""
    return {
        "line": "cast",
        "tier": tier,
        "villain": (market_snapshot or {}).get("villain", "Debt Titan"),
        "theme": f"CAST-{tier}",
        "privacy": os.getenv("YOUTUBE_DEFAULT_PRIVACY", "private"),
        "market_snapshot": market_snapshot or {},
    }


def has_published_today_line(line: str = "daily") -> bool:
    """라인별 당일 발행 확인 — C4 분리 설계.

    Supabase episodes.line 컬럼(003 마이그레이션) 기준 조회.
    line 컬럼이 아직 없는 구버전 스키마에서는 daily 게이트가 기존 함수를 그대로 쓴다.
    """
    from src.drive_manager import get_client, EpisodeStateUnavailable
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    try:
        client = get_client()
        query = (
            client.table("episodes")
            .select("episode_no, market_as_of")
            .in_("status", ("published",))
            .gte("market_as_of", today)
        )
        try:
            query = query.eq("line", line)
        except Exception:
            if line != "daily":
                # daily가 아닌 라인인데 line 컬럼이 없으면 → cast 라인 발행 이력이 없으므로 미발행 처리
                return False
        result = query.limit(1).execute()
    except EpisodeStateUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("line_published_check_failed line=%s err=%s", line, type(e).__name__)
        raise EpisodeStateUnavailable("라인별 발행 여부 조회 실패") from e
    rows = result.data or []
    return bool(rows)


def publish_cast(video_path: str, tier: str, mode: str, market_snapshot: dict | None = None,
                 youtube_service=None) -> dict | None:
    """발행 진입점 — mode 게이트 → 중복 게이트 → upload_to_youtube 재용.

    mode: dry_run(호출 0) / shadow(발행 스킵) / live(발행).
    """
    if mode == "dry_run":
        logger.info("publish_skipped mode=dry_run")
        return None
    if mode == "shadow":
        logger.info("publish_skipped mode=shadow — 생성물 보존, 발행 없음")
        return None
    if not os.getenv("CAST_EXECUTION_MODE") == "live":
        raise PublishBlocked("CAST_EXECUTION_MODE != live — 워크플로우 게이트와 불일치")
    if has_published_today_line("cast"):
        raise PublishBlocked("cast 라인 오늘 발행 이력 존재 — 중복 차단")
    metadata = cast_episode_metadata(tier, market_snapshot)
    service = youtube_service if youtube_service is not None else "unset"
    from src.publisher import upload_to_youtube, _YOUTUBE_SERVICE_UNSET
    kw = {} if service == "unset" else {"youtube_service": service}
    result = upload_to_youtube(video_path, metadata, **kw)
    logger.info("cast_publish_done tier=%s result=%s", tier, bool(result))
    return result
