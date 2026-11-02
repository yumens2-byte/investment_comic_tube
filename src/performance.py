"""DN2026_0092 M1 성과 측정 — 발행 영상 YouTube 통계 수집.

순수 로직만 둔다(클라이언트는 호출자가 주입). 재시도·DLQ 없음(STD-PUB-03):
응답에 없는 영상은 결과에서 빠지고 호출자가 경고로만 보고한다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

VERSION = "1.0.0"  # DN2026_0092

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
VALID_MODES = ("dry_run", "shadow", "live")
BATCH_SIZE = 50  # videos.list id 파라미터 상한
TABLE = "video_stats"
ON_CONFLICT = "video_id,collected_date"


def resolve_mode(raw) -> str:
    """dry_run/shadow/live 3값. 그 외·빈값·None 은 dry_run 강등 (STD-MODE)."""
    mode = (raw or "").strip().lower() if isinstance(raw, str) else ""
    if mode in VALID_MODES:
        return mode
    logger.warning("run_mode_invalid value=%r fallback=dry_run", raw)
    return "dry_run"


def kst_today() -> str:
    """KST(Asia/Seoul) 기준 오늘 날짜 'YYYY-MM-DD'."""
    return datetime.now(KST).date().isoformat()


def list_published_videos(client) -> list[dict]:
    """public.episodes 에서 youtube_video_id 가 있는 회차를 episode_no 순으로 반환."""
    response = (
        client.table("episodes")
        .select("episode_no,youtube_video_id")
        .not_.is_("youtube_video_id", "null")
        .order("episode_no")
        .execute()
    )
    videos: list[dict] = []
    seen: set[str] = set()
    for row in response.data or []:
        video_id = (row.get("youtube_video_id") or "").strip()
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        videos.append({"episode_no": row.get("episode_no"), "youtube_video_id": video_id})
    return videos


def _to_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_stats(youtube, video_ids) -> dict:
    """videos().list 50개 단위 배치 조회. 응답에 없는 id 는 결과에서 빠진다."""
    ids = list(video_ids)
    stats: dict[str, dict] = {}
    for start in range(0, len(ids), BATCH_SIZE):
        batch = ids[start:start + BATCH_SIZE]
        response = youtube.videos().list(
            part="statistics,status", id=",".join(batch), maxResults=BATCH_SIZE
        ).execute()
        for item in response.get("items", []) or []:
            vid = item.get("id")
            if not vid:
                continue
            statistics = item.get("statistics") or {}
            status = item.get("status") or {}
            stats[vid] = {
                "views": _to_int(statistics.get("viewCount")),
                "likes": _to_int(statistics.get("likeCount")),
                "comments": _to_int(statistics.get("commentCount")),
                "privacy_status": status.get("privacyStatus"),
            }
    return stats


def build_rows(videos, stats, collected_date, run_mode) -> list[dict]:
    """조회된 영상만 video_stats 행으로 만든다 (collected_at 은 DB default)."""
    rows: list[dict] = []
    for video in videos:
        vid = video["youtube_video_id"]
        stat = stats.get(vid)
        if stat is None:
            continue
        rows.append({
            "video_id": vid,
            "episode_no": video.get("episode_no"),
            "collected_date": collected_date,
            "views": stat.get("views"),
            "likes": stat.get("likes"),
            "comments": stat.get("comments"),
            "privacy_status": stat.get("privacy_status"),
            "run_mode": run_mode,
        })
    return rows


def save_rows(client, rows):
    """video_stats upsert (멱등 키: video_id, collected_date)."""
    if not rows:
        return None
    return client.table(TABLE).upsert(rows, on_conflict=ON_CONFLICT).execute()


def load_previous_views(client, collected_date) -> tuple[str | None, dict]:
    """collected_date 직전 수집일의 {video_id: views}. 읽기 전용(shadow 비교용)."""
    response = (
        client.table(TABLE)
        .select("collected_date")
        .lt("collected_date", collected_date)
        .order("collected_date", desc=True)
        .limit(1)
        .execute()
    )
    data = response.data or []
    if not data:
        return None, {}
    previous = data[0]["collected_date"]
    rows = (
        client.table(TABLE)
        .select("video_id,views")
        .eq("collected_date", previous)
        .execute()
    ).data or []
    return previous, {r["video_id"]: r.get("views") for r in rows}
