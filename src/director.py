"""시장 데이터를 EDT Universe 스토리(빌런/테마/내레이션)로 변환한다.

빌런/테마 선택은 임계값 규칙을 유지한다. Gemini는 내레이션 문장을
다듬는 용도로만 사용하며, API 미설정/실패 시 규칙 문장을 그대로 사용한다
(외부 API 장애가 스토리 생성 자체를 막지 않도록 안전 폴백).
"""

from __future__ import annotations

import logging
import os

from src.drive_manager import fetch_latest_episode_state, start_episode

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
    next_ep = prev_state.get("episode", 102) + 1

    tnx = market_data.get("TNX", {}).get("close", 0)
    vix = market_data.get("VIX", {}).get("close", 0)

    if tnx > 4.5:
        villain, theme = "Debt Titan", "긴축의 심화와 방어선 사수"
    elif vix > 25.0:
        villain, theme = "Chaos Reaper", "변동성 폭발과 시장의 광기"
    else:
        villain, theme = "Bull Brute", "유동성 장세와 돌파 매수"

    base_narration = f"오늘 시장 지표 분석 결과, {villain}의 기운이 감지되었다."
    narration, degraded_reason = _polish_narration(base_narration)

    script_data = {
        "episode": next_ep,
        "villain": villain,
        "theme": theme,
        "narration": narration,
        "degraded_reason": degraded_reason,
    }

    logger.info("script_generation_finished villain=%s theme=%s", villain, theme)

    episode_id = start_episode(script_data)
    script_data["episode_id"] = episode_id
    return script_data
