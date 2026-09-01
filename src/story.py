"""30초 숏폼용 6비트 스토리보드 생성.

구조(각 비트 약 5초):
  1 HOOK   - 시장 상황 제시
  2 THREAT - 빌런 등장
  3 IMPACT - 시장 타격
  4 HERO   - 호랑이 히어로 등장
  5 CLASH  - 대결
  6 LESSON - 투자 교훈 마무리

Gemini가 각 비트의 한국어 내레이션을 JSON으로 생성한다.
API 미설정/실패/형식 불량 시 규칙 기반 템플릿으로 폴백하므로
스토리보드 생성이 파이프라인을 막지 않는다.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

STORY_MODEL = "gemini-3.6-flash"

# (beat_id, 영어 장면 지시문) -- 이미지 프롬프트에 들어간다
BEAT_SCENES = [
    (
        "HOOK",
        (
            "Wide establishing shot of a tense financial city skyline at dawn, "
            "storm clouds gathering, no characters yet."
        ),
    ),
    (
        "THREAT",
        (
            "The villain appears in the distance above the city, menacing and huge, "
            "EDT the tiger hero is not visible yet."
        ),
    ),
    (
        "IMPACT",
        (
            "The villain strikes the city: buildings cracking, shockwave, debris, "
            "chaos in the streets."
        ),
    ),
    (
        "HERO",
        (
            "EDT the tiger hero lands heroically in the foreground with his chainsaw raised, "
            "crouched on rubble, looking up at the villain, determined."
        ),
    ),
    (
        "CLASH",
        (
            "EDT the tiger hero swings his roaring chainsaw at the villain head-on in the center of the frame, "
            "energy bursting between them."
        ),
    ),
    (
        "LESSON",
        (
            "EDT the tiger hero stands alone on high ground at sunrise, chainsaw resting at his side, calm, "
            "the city recovering behind him."
        ),
    ),
]

BEAT_COUNT = len(BEAT_SCENES)

# 비용 통제: 이미지 N장을 6비트에 재사용하기 위한 슬롯 매핑.
# 슬롯 0 = 위협/시장(빌런 중심), 슬롯 1 = 히어로 등장, 슬롯 2 = 대결/마무리.
# 이미지 장수가 슬롯 수보다 적으면 renderer 가 남는 슬롯을 순환 대입한다.
BEAT_IMAGE_SLOT = [0, 0, 0, 1, 2, 2]

# 이미지 슬롯별 생성 프롬프트용 장면 지시문 (BEAT_SCENES 와 별개)
SLOT_SCENES = [
    (
        "The villain looms huge over a cracking financial city skyline, shockwave and "
        "debris, storm clouds, EDT the tiger hero is not visible."
    ),
    (
        "EDT the tiger hero lands heroically in the foreground with his chainsaw raised, "
        "crouched on rubble, looking up at the villain, determined."
    ),
    (
        "EDT the tiger hero swings his roaring chainsaw at the villain head-on in the center of the frame, "
        "energy bursting between them, sunrise breaking through."
    ),
]


def _fallback_narrations(villain: str, theme: str, market_data: dict) -> list[str]:
    """규칙 기반 내레이션. Gemini 실패 시 사용한다."""
    tnx = market_data.get("TNX", {}).get("close", 0)
    vix = market_data.get("VIX", {}).get("close", 0)
    return [
        f"오늘 시장, 10년물 금리 {tnx}에 변동성 지수 {vix}.",
        f"그때 {villain}이 모습을 드러냈다.",
        "지수는 흔들리고, 투자자들의 계좌가 비명을 질렀다.",
        "하지만 우리에겐 EDT가 있다.",
        f"{theme}. 정면으로 부딪힌다.",
        "패닉은 짧고 원칙은 길다. 오늘도 살아남자.",
    ]


def _parse_narrations(raw: str) -> list[str] | None:
    """모델 응답에서 내레이션 배열을 뽑아낸다. 형식이 어긋나면 None."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text
        text = text.removeprefix("json").strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return None

    if isinstance(data, dict):
        data = data.get("narrations")
    if not isinstance(data, list) or len(data) != BEAT_COUNT:
        return None

    lines = [str(item).strip() for item in data]
    if not all(lines):
        return None
    return lines


def _generate_narrations(villain: str, theme: str, market_data: dict) -> tuple[list[str], str | None]:
    """Gemini로 내레이션 6줄을 생성한다. 실패 시 (폴백 문장, 사유)."""
    fallback = _fallback_narrations(villain, theme, market_data)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return fallback, "story:no_api_key"

    try:
        from google import genai
    except ImportError:
        return fallback, "story:google_genai_not_installed"

    tnx = market_data.get("TNX", {}).get("close", 0)
    vix = market_data.get("VIX", {}).get("close", 0)
    nasdaq = market_data.get("NASDAQ", {}).get("change_pct", 0)

    prompt = (
        "너는 한국어 주식투자 숏폼 영상의 내레이션 작가다. "
        f"오늘 시장 데이터: 미국10년물금리 {tnx}, VIX {vix}, 나스닥 등락률 {nasdaq}%. "
        f"빌런은 '{villain}', 주제는 '{theme}'. 히어로는 체인소를 무기로 쓰는 호랑이 캐릭터 'EDT'다.\n"
        "아래 6개 장면 순서에 맞춰 각각 한 문장씩 한국어 내레이션을 써라.\n"
        "1) 시장 상황 제시 2) 빌런 등장 3) 시장 타격 4) EDT 등장 "
        "5) 대결 6) 투자 교훈 마무리\n"
        "각 문장은 소리내어 읽었을 때 4초 이내여야 하며 25자~45자 사이로 쓴다. "
        "과장된 투자 권유나 수익 보장 표현은 절대 쓰지 마라.\n"
        '반드시 다음 JSON 배열 형식으로만 출력해라. 설명이나 마크다운 없이: '
        '["문장1","문장2","문장3","문장4","문장5","문장6"]'
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=STORY_MODEL, contents=prompt)
        parsed = _parse_narrations(response.text or "")
    except Exception as e:  # noqa: BLE001 - 외부 API 실패는 규칙 문장으로 폴백
        logger.warning("story_generation_failed reason=%s: %s", type(e).__name__, e)
        return fallback, f"story:{type(e).__name__}"

    if parsed is None:
        logger.warning("story_generation_malformed -- falling back to rule template")
        return fallback, "story:malformed_response"

    return parsed, None


def build_storyboard(market_data: dict, villain: str, theme: str) -> tuple[list[dict], str | None]:
    """6비트 스토리보드와 폴백 사유를 반환한다.

    각 비트: {"beat", "scene", "narration"}
    """
    logger.info("storyboard_started beats=%s", BEAT_COUNT)
    narrations, degraded = _generate_narrations(villain, theme, market_data)

    storyboard = [
        {"beat": beat, "scene": scene, "narration": narration}
        for (beat, scene), narration in zip(BEAT_SCENES, narrations, strict=True)
    ]
    logger.info("storyboard_finished beats=%s degraded=%s", len(storyboard), degraded)
    return storyboard, degraded
