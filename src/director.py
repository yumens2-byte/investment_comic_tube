"""시장 데이터를 EDT Universe 스토리(빌런/테마/내레이션)로 변환한다.

빌런/테마 선택은 임계값 규칙을 유지한다. Gemini는 내레이션 문장을
다듬는 용도로만 사용하며, API 미설정/실패 시 규칙 문장을 그대로 사용한다
(외부 API 장애가 스토리 생성 자체를 막지 않도록 안전 폴백).
"""

from __future__ import annotations

import logging
import os

from src.drive_manager import (
    fetch_latest_episode_state,
    fetch_recent_cliffhangers,
    start_episode,
)
from src.market_regime import select_villain
from src.quota import is_quota_exhausted
from src.story import build_storyboard

logger = logging.getLogger(__name__)

NARRATION_MODEL = "gemini-3.6-flash"


def _polish_narration(base_sentence: str) -> tuple[str, str | None]:
    """내레이션을 다듬어 (문장, 실패사유) 를 반환한다. 사유가 None 이면 정상."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return base_sentence, "narration:no_api_key"

    try:
        from google import genai
    except ImportError:
        logger.warning("narration_polish_skipped reason=google_genai_not_installed")
        return base_sentence, "narration:google_genai_not_installed"

    prompt = (
        "다음 한국어 문장을 같은 의미를 유지하면서 더 임팩트 있고 자연스럽게 "
        "한 문장으로 다듬어줘. 부연 설명 없이 다듬어진 문장만 출력해:\n"
        f"{base_sentence}"
    )
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=NARRATION_MODEL, contents=prompt)
        polished = (response.text or "").strip()
    except Exception as e:  # noqa: BLE001 - 외부 API 실패는 규칙 문장으로 폴백
        if is_quota_exhausted(e):
            logger.warning("narration_polish_aborted reason=quota_exhausted")
            return base_sentence, "narration:quota_exhausted"
        reason = f"narration:{type(e).__name__}"
        logger.warning("narration_polish_failed reason=%s: %s", type(e).__name__, e)
        return base_sentence, reason

    if not polished:
        logger.warning("narration_polish_empty -- falling back to rule sentence")
        return base_sentence, "narration:empty_response"

    return polished, None


def generate_connected_script(market_data: dict) -> dict:
    logger.info("script_generation_started")
    prev_state = fetch_latest_episode_state()
    prev_state["recent_cliffhangers"] = fetch_recent_cliffhangers(limit=3)
    next_ep = prev_state.get("episode", 0) + 1

    # 고정 임계값 대신 복합 스코어로 판정한다 (빌런 고착 방지)
    villain, theme, scores = select_villain(market_data, prev_state)

    base_narration = f"오늘 시장 지표 분석 결과, {villain}의 기운이 감지되었다."
    narration, degraded_reason = _polish_narration(base_narration)

    storyboard, story_state, story_degraded = build_storyboard(
        market_data, villain, theme, prev_state
    )
    degraded_reasons = [r for r in (degraded_reason, story_degraded) if r]

    # 시장 수치를 그대로 보관해 다음 회차가 전일 대비 서사를 만들 수 있게 한다
    market_snapshot = dict(market_data)
    market_snapshot["_villain_scores"] = scores

    script_data = {
        "episode": next_ep,
        "villain": villain,
        "theme": theme,
        "narration": narration,
        "storyboard": storyboard,
        "market_snapshot": market_snapshot,
        "story_state": story_state,
        "degraded_reason": ";".join(degraded_reasons) if degraded_reasons else None,
    }

    logger.info(
        "script_generation_finished episode=%s villain=%s theme=%s streak=%s",
        next_ep, villain, theme, story_state["villain_streak"],
    )

    episode_id = start_episode(script_data)
    script_data["episode_id"] = episode_id
    return script_data
