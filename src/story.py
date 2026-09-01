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

from src.hooks import (
    HOOK_MAX_CHARS,
    HOOK_MIN_CHARS,
    HOOK_SPECS,
    fallback_hook_line,
    is_valid_hook_line,
    select_hook_type,
)
from src.quota import is_quota_exhausted

logger = logging.getLogger(__name__)

STORY_MODEL = "gemini-3.6-flash"

# (beat_id, 영어 장면 지시문) -- 이미지 프롬프트에 들어간다
BEAT_SCENES = [
    (
        "HOOK",
        (
            "EXTREME CLOSE-UP of EDT the tiger hero's face filling the entire frame, "
            "snarling with bared fangs, fierce amber eyes locked on the viewer, "
            "the toothed blade of his roaring chainsaw crossing diagonally in front of him, "
            "harsh dramatic rim light, sparks and embers flying, shallow depth of field. "
            "Place his head in the UPPER TWO THIRDS; keep the BOTTOM THIRD uncluttered."
        ),
    ),
    (
        "THREAT",
        (
            "The villain looms enormous over a cracking financial city skyline, "
            "storm clouds and lightning, EDT the tiger hero is not visible yet."
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
            "energy bursting between them, sunrise breaking through."
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
# 슬롯 0 = 훅 클로즈업, 슬롯 1 = 위협/시장(빌런), 슬롯 2 = 히어로 등장, 슬롯 3 = 대결/마무리.
# 이미지 장수가 슬롯 수보다 적으면 renderer 가 남는 슬롯을 순환 대입한다.
BEAT_IMAGE_SLOT = [0, 1, 1, 2, 3, 3]

# 이미지 슬롯별 생성 프롬프트용 장면 지시문 (BEAT_SCENES 와 별개)
SLOT_SCENES = [
    # 슬롯 0 -- 훅 전용. 0~3초를 책임지는 가장 중요한 컷이라 단독 슬롯을 쓴다.
    (
        "EXTREME CLOSE-UP of EDT the tiger hero's face filling the entire frame, "
        "snarling with bared fangs, fierce amber eyes locked on the viewer, "
        "the toothed blade of his roaring chainsaw crossing diagonally in front of him, "
        "harsh dramatic rim light, sparks and embers flying, shallow depth of field. "
        "Place his head and the chainsaw blade in the UPPER TWO THIRDS of the frame. Keep the BOTTOM THIRD visually simple and uncluttered -- a large caption will be overlaid there, so nothing important may sit in the bottom third."
    ),
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


def _fallback_narrations(
    villain: str, theme: str, market_data: dict, prev_state: dict | None = None,
    hook_type: str = "A",
) -> list[str]:
    """규칙 기반 내레이션. Gemini 실패 시 사용한다."""
    tnx = market_data.get("TNX", {}).get("close")
    vix = market_data.get("VIX", {}).get("close")
    tnx_txt = tnx if tnx is not None else "확인불가"
    vix_txt = vix if vix is not None else "확인불가"

    prev_villain = (prev_state or {}).get("villain")
    if prev_villain and prev_villain == villain:
        second = f"금리 {tnx_txt}, 공포지수 {vix_txt}. {villain}은 아직 물러나지 않았다."
    elif prev_villain:
        second = f"금리 {tnx_txt}, 공포지수 {vix_txt}. {prev_villain}이 물러난 자리에 {villain}이 나타났다."
    else:
        second = f"금리 {tnx_txt}, 공포지수 {vix_txt}. 그때 {villain}이 모습을 드러냈다."

    return [
        fallback_hook_line(hook_type, villain, market_data),
        second,
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


def _build_continuity_context(prev_state: dict | None, market_data: dict, villain: str = "") -> str:
    """이전 회차와의 연결고리를 프롬프트용 문장으로 만든다."""
    if not prev_state:
        return "이번이 첫 회차다. 이전 회차 언급 없이 시작해라."

    parts = []
    prev_ep = prev_state.get("episode")
    prev_villain = prev_state.get("villain")
    if prev_ep and prev_villain:
        parts.append(f"직전 {prev_ep}화의 빌런은 '{prev_villain}'이었다.")

    story_state = prev_state.get("story_state") or {}
    unresolved = story_state.get("unresolved")
    streak = story_state.get("villain_streak")
    if unresolved:
        parts.append(f"직전 회차에서 해결되지 않은 위협: {unresolved}")
    if isinstance(streak, int) and streak >= 2 and prev_villain == villain:
        parts.append(f"'{villain}'은 이번이 {streak + 1}회 연속 등장이다. 장기전임을 반영해라.")

    # 전일 대비 변화 서사
    prev_snapshot = prev_state.get("market_snapshot") or {}
    for key, label in (("TNX", "10년물 금리"), ("VIX", "VIX")):
        now_v = (market_data.get(key) or {}).get("close")
        old_v = (prev_snapshot.get(key) or {}).get("close")
        if isinstance(now_v, (int, float)) and isinstance(old_v, (int, float)):
            direction = "올랐다" if now_v > old_v else ("내렸다" if now_v < old_v else "그대로다")
            parts.append(f"{label}는 직전 회차 {old_v}에서 {now_v}로 {direction}.")

    if not parts:
        return "이전 회차 정보가 부족하다. 이전 회차를 구체적으로 언급하지 마라."
    return " ".join(parts)


def _generate_narrations(
    villain: str, theme: str, market_data: dict, prev_state: dict | None = None,
    hook_type: str = "A",
) -> tuple[list[str], str | None]:
    """Gemini로 내레이션 6줄을 생성한다. 실패 시 (폴백 문장, 사유)."""
    fallback = _fallback_narrations(villain, theme, market_data, prev_state, hook_type)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return fallback, "story:no_api_key"

    try:
        from google import genai
    except ImportError:
        return fallback, "story:google_genai_not_installed"

    tnx = market_data.get("TNX", {}).get("close")
    vix = market_data.get("VIX", {}).get("close")
    nasdaq = market_data.get("NASDAQ", {}).get("change_pct")
    spx = market_data.get("SPX", {}).get("change_pct")
    dxy = market_data.get("DXY", {}).get("close")
    gold = market_data.get("GOLD", {}).get("close")
    continuity = _build_continuity_context(prev_state, market_data, villain)
    spec = HOOK_SPECS[hook_type]

    prompt = (
        "너는 한국어 주식투자 숏폼 영상의 내레이션 작가다. "
        f"오늘 시장 데이터: 미국10년물금리 {tnx}, VIX {vix}, 나스닥 등락률 {nasdaq}%, "
        f"S&P500 등락률 {spx}%, 달러인덱스 {dxy}, 금 {gold}. "
        f"빌런은 '{villain}', 주제는 '{theme}'. 히어로는 체인소를 무기로 쓰는 호랑이 캐릭터 'EDT'다.\n"
        f"[이전 회차 맥락] {continuity}\n"
        f"[1번 문장 = 오프닝 훅] 유형: {spec['name']}. {spec['guide']} "
        f"예시: \"{spec['example']}\"\n"
        f"1번 문장은 반드시 {HOOK_MIN_CHARS}자 이상 {HOOK_MAX_CHARS}자 이하로 쓴다. "
        "쇼츠 피드에서 스크롤을 멈추게 하는 것이 목적이므로, 군더더기 서두 없이 "
        "첫 어절부터 강하게 시작한다.\n"
        "아래 6개 장면 순서에 맞춰 각각 한 문장씩 한국어 내레이션을 써라.\n"
        "1) 오프닝 훅 2) 시장 상황과 빌런 등장 3) 시장 타격 4) EDT 등장 "
        "5) 대결 6) 마무리 겸 다음 회차 예고(클리프행어)\n"
        "6번 문장은 반드시 여운이나 다음 편에 대한 궁금증을 남기는 형태로 끝내라.\n"
        f"2~6번 문장은 소리내어 읽었을 때 4초 이내여야 하며 25자~45자 사이로 쓴다. "
        "과장된 투자 권유나 수익 보장 표현은 절대 쓰지 마라.\n"
        '반드시 다음 JSON 배열 형식으로만 출력해라. 설명이나 마크다운 없이: '
        '["문장1","문장2","문장3","문장4","문장5","문장6"]'
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=STORY_MODEL, contents=prompt)
        parsed = _parse_narrations(response.text or "")

        # 훅 길이/형식 제약은 모델이 자주 어긴다(기존 25~45자 요구도 초과한 전례).
        # 실패 시 제약을 더 강하게 재주입해 1회만 재생성한다.
        if parsed and not is_valid_hook_line(parsed[0], hook_type):
            logger.warning(
                "hook_line_rejected len=%s line=%s -- retrying once",
                len(parsed[0]), parsed[0],
            )
            retry_prompt = (
                prompt
                + f"\n\n[재작성 지시] 1번 문장이 규격을 벗어났다. "
                f"반드시 {HOOK_MIN_CHARS}~{HOOK_MAX_CHARS}자 한국어로 다시 써라. "
                + ("반드시 '[긴급]' 으로 시작해야 한다. " if hook_type == "D" else "")
                + "6개 문장 JSON 배열만 출력해라."
            )
            retry = client.models.generate_content(model=STORY_MODEL, contents=retry_prompt)
            retried = _parse_narrations(retry.text or "")
            if retried and is_valid_hook_line(retried[0], hook_type):
                parsed = retried
            elif parsed:
                # 나머지 5줄은 살리고 훅만 규칙 문장으로 교체한다
                parsed[0] = fallback_hook_line(hook_type, villain, market_data)
                logger.warning("hook_line_fallback_applied type=%s", hook_type)
    except Exception as e:  # noqa: BLE001 - 외부 API 실패는 규칙 문장으로 폴백
        if is_quota_exhausted(e):
            logger.warning("story_generation_aborted reason=quota_exhausted")
            return fallback, "story:quota_exhausted"
        logger.warning("story_generation_failed reason=%s: %s", type(e).__name__, e)
        return fallback, f"story:{type(e).__name__}"

    if parsed is None:
        logger.warning("story_generation_malformed -- falling back to rule template")
        return fallback, "story:malformed_response"

    return parsed, None


def build_story_state(
    villain: str, prev_state: dict | None, narrations: list[str], hook_type: str = "A"
) -> dict:
    """다음 회차가 이어받을 서사 상태를 만든다."""
    prev = prev_state or {}
    prev_story = prev.get("story_state") or {}
    streak = 1
    if prev.get("villain") == villain:
        prev_streak = prev_story.get("villain_streak")
        streak = (prev_streak + 1) if isinstance(prev_streak, int) else 2

    return {
        "villain": villain,
        "villain_streak": streak,
        # 다음 회차가 같은 훅 유형을 연속으로 쓰지 않도록 기록한다
        "hook_type": hook_type,
        # 마지막 비트가 클리프행어이므로 다음 회차의 '미해결 위협' 입력이 된다
        "unresolved": narrations[-1] if narrations else None,
    }


def build_storyboard(
    market_data: dict, villain: str, theme: str, prev_state: dict | None = None
) -> tuple[list[dict], dict, str | None]:
    """6비트 스토리보드, 다음 회차용 서사 상태, 폴백 사유를 반환한다.

    각 비트: {"beat", "scene", "narration"}
    """
    prev = prev_state or {}
    prev_story = prev.get("story_state") or {}
    streak = 1
    if prev.get("villain") == villain:
        prev_streak = prev_story.get("villain_streak")
        streak = (prev_streak + 1) if isinstance(prev_streak, int) else 2

    hook_type = select_hook_type(villain, prev_state, streak)

    logger.info(
        "storyboard_started beats=%s prev_villain=%s hook_type=%s",
        BEAT_COUNT, prev.get("villain"), hook_type,
    )
    narrations, degraded = _generate_narrations(
        villain, theme, market_data, prev_state, hook_type
    )

    storyboard = [
        {"beat": beat, "scene": scene, "narration": narration}
        for (beat, scene), narration in zip(BEAT_SCENES, narrations, strict=True)
    ]
    # 첫 비트에 훅 메타데이터를 실어 renderer/tts 가 특별 처리하게 한다
    storyboard[0]["is_hook"] = True
    storyboard[0]["hook_type"] = hook_type
    storyboard[0]["tts_tone"] = HOOK_SPECS[hook_type]["tts_tone"]
    storyboard[0]["sfx"] = HOOK_SPECS[hook_type]["sfx"]
    story_state = build_story_state(villain, prev_state, narrations, hook_type)
    logger.info(
        "storyboard_finished beats=%s streak=%s hook_type=%s hook='%s' degraded=%s",
        len(storyboard), story_state["villain_streak"], hook_type,
        storyboard[0]["narration"], degraded,
    )
    return storyboard, story_state, degraded
