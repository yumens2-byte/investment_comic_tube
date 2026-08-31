"""API 한도 소진(quota / spend cap) 감지 공통 모듈.

2026-08-31 사고: Gemini 프로젝트의 월 지출 상한 초과로 429 RESOURCE_EXHAUSTED가
발생했는데, 이미지 6회 + TTS 6회가 전부 개별 호출되어 실패가 확정된 뒤에도
무의미한 호출을 12회 더 날렸다. 한도 소진은 재시도해도 절대 회복되지 않으므로
첫 감지 즉시 남은 호출을 중단한다(fail-fast).
"""

from __future__ import annotations

# 한도 소진을 나타내는 신호. 소문자로 비교한다.
QUOTA_MARKERS = (
    "resource_exhausted",
    "spending cap",
    "spend cap",
    "quota exceeded",
    "insufficient_quota",
)


def is_quota_exhausted(error: Exception) -> bool:
    """예외가 '재시도해도 소용없는 한도 소진'인지 판정한다.

    429는 일시적 rate limit 일 수도 있으므로 429 단독으로는 판정하지 않고,
    한도 소진을 명시하는 문구가 함께 있을 때만 True 를 반환한다.
    """
    text = str(error).lower()
    return any(marker in text for marker in QUOTA_MARKERS)
