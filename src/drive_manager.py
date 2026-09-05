"""episodes / step_runs 영속화 계층 (Supabase 기반).

DB 선기록 원칙 (2026-08-31 규약 적용):
  에피소드 상태(episodes)는 진행 단계마다 즉시 기록한다. 기록 실패는 상위로
  전파하여 파이프라인을 중단시킨다 -- 기록 없는 발행(고아 콘텐츠)을 막기 위함.
  step_runs는 보조 관측 데이터이므로 기록 실패가 파이프라인을 막지 않는다
  (best-effort, 실패 시 경고 로그만).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from src.db_client import get_client

logger = logging.getLogger(__name__)

# 에피소드가 하나도 없을 때의 기준값. next_ep = 이 값 + 1 이므로 0 이면 1화부터 시작한다.
DEFAULT_STATE = {
    "episode": 0,
    "villain": None,
    "theme": None,
    "story_state": None,
    "market_snapshot": None,
}


PUBLISHED_STATUSES = ["published", "published_degraded"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_latest_episode_state() -> dict:
    """가장 최근 에피소드 상태를 조회한다. 실패/데이터 없음 시 안전 기본값 반환."""
    logger.info("episode_state_fetch_started backend=supabase")
    try:
        client = get_client()
        # 실패한 회차는 시청자가 본 적이 없으므로 번호도 서사도 이어받지 않는다.
        # 발행된 회차만 기준으로 삼아야 번호 gap(YouTube 가 Ep.2 부터 시작)이 생기지 않는다.
        result = (
            client.table("episodes")
            .select("episode_no, status, villain, story_state, market_snapshot")
            .in_("status", PUBLISHED_STATUSES)
            .order("episode_no", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001 - 조회 실패는 안전 기본값으로 폴백
        logger.warning("episode_state_fetch_failed reason=%s: %s -- using default", type(e).__name__, e)
        return dict(DEFAULT_STATE)

    rows = result.data or []
    if not rows:
        logger.info("episode_state_fetch_finished rows=0 -- using default")
        return dict(DEFAULT_STATE)

    latest = rows[0]
    state = dict(DEFAULT_STATE)
    state["episode"] = latest.get("episode_no", DEFAULT_STATE["episode"])
    state["villain"] = latest.get("villain")
    state["story_state"] = latest.get("story_state")
    state["market_snapshot"] = latest.get("market_snapshot")
    logger.info(
        "episode_state_fetch_finished episode=%s status=%s prev_villain=%s has_story_state=%s",
        state["episode"],
        latest.get("status"),
        state["villain"],
        state["story_state"] is not None,
    )
    return state


def start_episode(script_data: dict) -> str:
    """신규 에피소드 row를 status=script_ready 로 선기록하고 id를 반환한다."""
    episode_no = script_data.get("episode")
    episode_id = f"ep-{episode_no:04d}-{uuid.uuid4().hex[:8]}"
    now = _now_iso()

    logger.info("episode_start_started episode_no=%s id=%s", episode_no, episode_id)
    client = get_client()
    client.table("episodes").insert(
        {
            "id": episode_id,
            "episode_no": episode_no,
            "revision": 1,
            "status": "script_ready",
            "market_as_of": now,
            "villain": script_data.get("villain"),
            "market_snapshot": script_data.get("market_snapshot"),
            "story_state": script_data.get("story_state"),
            "created_at": now,
            "updated_at": now,
        }
    ).execute()
    logger.info("episode_start_finished id=%s", episode_id)
    return episode_id


def update_episode(episode_id: str, **fields) -> None:
    """에피소드 row를 갱신한다 (status/script_path/video_path/youtube_video_id 등)."""
    if not fields:
        return
    payload = dict(fields)
    payload["updated_at"] = _now_iso()

    logger.info("episode_update_started id=%s fields=%s", episode_id, list(fields.keys()))
    client = get_client()
    client.table("episodes").update(payload).eq("id", episode_id).execute()
    logger.info("episode_update_finished id=%s status=%s", episode_id, fields.get("status"))


def record_step_start(episode_id: str, step: str, input_hash: str | None = None) -> str | None:
    """step_runs에 시작 기록을 남긴다 (best-effort, 실패해도 파이프라인 유지)."""
    step_run_id = str(uuid.uuid4())
    try:
        client = get_client()
        client.table("step_runs").insert(
            {
                "id": step_run_id,
                "episode_id": episode_id,
                "step": step,
                "attempt": 1,
                "input_hash": input_hash or "",
                "status": "running",
                "started_at": _now_iso(),
            }
        ).execute()
    except Exception as e:  # noqa: BLE001 - 관측 데이터 실패는 파이프라인을 막지 않음
        logger.warning("step_run_start_failed step=%s reason=%s: %s", step, type(e).__name__, e)
        return None
    return step_run_id


def record_step_finish(step_run_id: str | None, status: str, error_code: str | None = None) -> None:
    """step_runs에 종료 기록을 남긴다 (best-effort)."""
    if not step_run_id:
        return
    try:
        client = get_client()
        client.table("step_runs").update(
            {
                "status": status,
                "error_code": error_code,
                "finished_at": _now_iso(),
            }
        ).eq("id", step_run_id).execute()
    except Exception as e:  # noqa: BLE001
        logger.warning("step_run_finish_failed id=%s reason=%s: %s", step_run_id, type(e).__name__, e)


def has_published_today() -> bool:
    """오늘(UTC) 이미 발행된 회차가 있는지 확인한다.

    같은 날 재실행하면 동일 시세를 받아 사실상 같은 이야기가 두 번 생성된다
    (Ep.1/Ep.2 가 31분 간격으로 완전히 같은 시장 데이터를 가진 사례).
    조회 실패 시 False 를 돌려 파이프라인을 막지 않는다 -- 중복 방지는
    보조 안전장치이지 발행의 전제조건은 아니다.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        client = get_client()
        result = (
            client.table("episodes")
            .select("episode_no, market_as_of")
            .in_("status", PUBLISHED_STATUSES)
            .gte("market_as_of", today)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("published_today_check_failed reason=%s: %s", type(e).__name__, e)
        return False

    rows = result.data or []
    if rows:
        logger.warning("already_published_today episode_no=%s", rows[0].get("episode_no"))
        return True
    return False


def fetch_recent_cliffhangers(limit: int = 3) -> list[str]:
    """최근 발행 회차의 마지막 문장(클리프행어)을 가져온다.

    매회 '과연 EDT 는 방어선을 지켜낼까요?' 로 끝나는 반복을 끊기 위해
    프롬프트에 '이것들과 다르게 써라' 로 주입한다.
    """
    try:
        client = get_client()
        result = (
            client.table("episodes")
            .select("story_state")
            .in_("status", PUBLISHED_STATUSES)
            .order("episode_no", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("recent_cliffhangers_fetch_failed reason=%s: %s", type(e).__name__, e)
        return []

    lines = []
    for row in result.data or []:
        state = row.get("story_state") or {}
        text = state.get("unresolved")
        if text:
            lines.append(str(text))
    return lines
