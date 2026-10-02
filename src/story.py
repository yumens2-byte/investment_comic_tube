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

from src.pipeline_control import generate as guarded_generate, current, ControlError, make_client

import json
import logging
import os
import re

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
            "Use one continuous close-up composition with consistent perspective across the entire frame."
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
# renderer 본문 자막 규격 (renderer._render_segment 와 동일). 안전 모드에서 유료 생성 전 검사한다.
BODY_WRAP_CHARS = 19
BODY_MAX_LINES = 3

# 마무리(6번 비트) 유형. 5일 운영 실측: 5회 중 4회가 '과연 EDT는 방어선을 지켜낼까요?' 변주였다.
# 훅과 같은 방식으로 유형을 회전시켜 반복을 끊는다.
ENDING_TYPES = ["QUESTION", "DECLARE", "TEASE", "TWIST"]
ENDING_SPECS = {
    "QUESTION": {
        "name": "질문형",
        "guide": "시청자에게 직접 던지는 한 가지 구체적 질문으로 끝낸다. '과연', '~까요' 같은 상투구는 쓰지 않는다.",
        "example": "당신의 계좌는 지금 어느 편에 서 있나",
    },
    "DECLARE": {
        "name": "선언형",
        "guide": "EDT 의 결의나 상황 판단을 단정문으로 끝낸다. 물음표 금지.",
        "example": "물러설 곳은 없다. 다음 방어선은 여기다.",
    },
    "TEASE": {
        "name": "예고형",
        "guide": "다음 회차에 벌어질 구체적 사건을 예고한다. 새로운 위협이나 인물의 등장을 암시한다.",
        "example": "내일, 그림자 속에서 두 번째 빌런이 깨어난다.",
    },
    "TWIST": {
        "name": "반전형",
        "guide": "지금까지의 전제를 뒤집는 한 줄 폭로로 끝낸다.",
        "example": "그런데 방어선을 뚫은 건 빌런이 아니었다.",
    },
}


def select_ending_type(prev_state: dict | None) -> str:
    """직전 회차와 다른 마무리 유형을 고른다."""
    prev_ending = ((prev_state or {}).get("story_state") or {}).get("ending_type")
    if prev_ending in ENDING_TYPES:
        idx = ENDING_TYPES.index(prev_ending)
        return ENDING_TYPES[(idx + 1) % len(ENDING_TYPES)]
    return ENDING_TYPES[0]

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
        "Use one continuous close-up composition. His torso and equipment continue naturally to the frame edges, with a single coherent background and camera perspective."
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
        "숫자 하나보다 금리와 변동성의 같은 방향 움직임을 먼저 본다.",
        "EDT는 예측 대신 비중과 손절 기준부터 다시 확인했다.",
        f"{theme}, 추격 매매보다 확인된 흐름에 대응할 시간이다.",
        "뉴스에 반응하기 전 내 투자 원칙부터 한 줄로 적어보자.",
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


# 안전 모드(OPERATIONAL_SAFETY_ENABLED) 폴백 문장. 발행 대상이 아니며
# 다음 회차의 이어가기/회피 목록 입력으로도 쓰지 않는다 (Ep.31·32 오염 사례).
SAFE_FALLBACK_TEXT = ["시장의 위협에 맞선다", "원칙으로 방어선을 세운다",
                      "노출과 분산을 점검한다", "다음 시장 신호를 기다린다"]
_PROMPT_NUMBER = re.compile(r"\d[\d.,]*")


def is_fallback_line(text: str | None) -> bool:
    return bool(text) and str(text).strip() in SAFE_FALLBACK_TEXT


def mask_numbers(text: str) -> str:
    """안전 모드 프롬프트에 원시 숫자가 들어가 모델이 숫자를 베끼는 것을 막는다."""
    return _PROMPT_NUMBER.sub("(수치)", text)


def _build_continuity_context(prev_state: dict | None, market_data: dict, villain: str = "",
                              *, numeric: bool = True) -> str:
    """이전 회차와의 연결고리를 프롬프트용 문장으로 만든다.

    numeric=False(안전 모드)면 원시 수치 대신 방향만 쓰고, 이전 문장 속 숫자를 가린다.
    """
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
    if unresolved and not is_fallback_line(unresolved):
        text = unresolved if numeric else mask_numbers(str(unresolved))
        parts.append(f"직전 회차에서 해결되지 않은 위협: {text}")
    if isinstance(streak, int) and streak >= 2 and prev_villain == villain:
        parts.append(f"'{villain}'은 이번이 {streak + 1}회 연속 등장이다. 장기전임을 반영해라.")

    # 전일 대비 변화 서사
    prev_snapshot = prev_state.get("market_snapshot") or {}
    for key, label in (("TNX", "10년물 금리"), ("VIX", "VIX")):
        now_v = (market_data.get(key) or {}).get("close")
        old_v = (prev_snapshot.get(key) or {}).get("close")
        if isinstance(now_v, (int, float)) and isinstance(old_v, (int, float)):
            direction = "올랐다" if now_v > old_v else ("내렸다" if now_v < old_v else "그대로다")
            if numeric:
                parts.append(f"{label}는 직전 회차 {old_v}에서 {now_v}로 {direction}.")
            else:
                parts.append(f"{label}는 직전 회차보다 {direction}.")

    if not parts:
        return "이전 회차 정보가 부족하다. 이전 회차를 구체적으로 언급하지 마라."
    return " ".join(parts)


def _build_story_prompt(
    villain: str, theme: str, market_data: dict, prev_state: dict | None,
    hook_type: str, ending_type: str, recent_cliffhangers: list[str] | None,
    *, safe: bool,
) -> str:
    """내레이션 프롬프트. safe=True면 원시 수치 대신 사실 토큰 목록만 제공한다."""
    continuity = _build_continuity_context(prev_state, market_data, villain, numeric=not safe)
    spec = HOOK_SPECS[hook_type]
    ending = ENDING_SPECS[ending_type]
    avoid = [line for line in (recent_cliffhangers or []) if not is_fallback_line(line)]
    if safe:
        avoid = [mask_numbers(line) for line in avoid]
        from src.market_facts import prompt_contract
        data_line = "오늘 시장 데이터는 아래 [수치 규칙]의 사실 토큰으로만 제공된다. "
        compare_rule = ("2번은 오늘 사실 토큰 두 개를 함께 써서 비교하고, 3번은 그 흐름이 시장 심리에 "
                        "미치는 인과를 설명하며, ")
        length_rule = ("2~6번 문장은 토큰 대입 후 자막 3줄(한 줄 19자) 안에 들어가야 하므로 "
                       "토큰을 제외한 본문을 15자~30자로 쓴다. 토큰은 한 문장에 최대 2개. ")
    else:
        tnx = market_data.get("TNX", {}).get("close")
        vix = market_data.get("VIX", {}).get("close")
        nasdaq = market_data.get("NASDAQ", {}).get("change_pct")
        spx = market_data.get("SPX", {}).get("change_pct")
        dxy = market_data.get("DXY", {}).get("close")
        gold = market_data.get("GOLD", {}).get("close")
        data_line = (f"오늘 시장 데이터: 미국10년물금리 {tnx}, VIX {vix}, 나스닥 등락률 {nasdaq}%, "
                     f"S&P500 등락률 {spx}%, 달러인덱스 {dxy}, 금 {gold}. ")
        compare_rule = ("2번은 오늘 수치 두 개를 비교하고, 3번은 그 수치가 시장 심리에 미치는 인과를 설명하며, ")
        length_rule = "2~6번 문장은 소리내어 읽었을 때 4초 이내여야 하며 25자~45자 사이로 쓴다. "

    prompt = (
        "너는 한국어 주식투자 숏폼 영상의 내레이션 작가다. "
        + data_line
        + f"빌런은 '{villain}', 주제는 '{theme}'. 히어로는 체인소를 무기로 쓰는 호랑이 캐릭터 'EDT'다.\n"
        f"[이전 회차 맥락] {continuity}\n"
        f"[1번 문장 = 오프닝 훅] 유형: {spec['name']}. {spec['guide']} "
        f"예시: \"{spec['example']}\"\n"
        f"1번 문장은 반드시 {HOOK_MIN_CHARS}자 이상 {HOOK_MAX_CHARS}자 이하로 쓴다. "
        "쇼츠 피드에서 스크롤을 멈추게 하는 것이 목적이므로, 군더더기 서두 없이 "
        "첫 어절부터 강하게 시작한다.\n"
        + ("1번 문장에는 사실 토큰과 숫자를 쓰지 않는다.\n" if safe else "")
        + "아래 6개 장면 순서에 맞춰 각각 한 문장씩 한국어 내레이션을 써라.\n"
        "1) 오프닝 훅 2) 시장 상황과 빌런 등장 3) 시장 타격 4) EDT 등장 "
        "5) 대결 6) 마무리 겸 다음 회차 예고(클리프행어)\n"
        f"[6번 문장 = 마무리] 유형: {ending['name']}. {ending['guide']} "
        f"예시: \"{ending['example']}\"\n"
        + (
            "다음은 최근 회차의 마무리 문장들이다. 표현·구조·어휘가 이것들과 겹치면 안 된다:\n"
            + "\n".join(f"  - {line}" for line in avoid)
            + "\n"
            if avoid
            else ""
        )
        + "'과연', '~할 수 있을까요', '방어선을 지켜낼까요' 같은 상투구를 반복하지 마라.\n"
        "각 문장은 단순한 영웅 서사가 아니라 투자자가 가져갈 정보가 있어야 한다. "
        + compare_rule
        + "5번은 예측이나 매수 추천 대신 비중·분산·손절·관망 중 하나의 대응 원칙을 담아라. "
        "'계좌가 비명을 질렀다', '우리에겐 EDT가 있다'처럼 어느 날에도 붙일 수 있는 빈 문장은 금지한다. "
        "수치를 지어내지 말고 위에 제공된 데이터만 사용한다.\n"
        + length_rule
        + "과장된 투자 권유나 수익 보장 표현은 절대 쓰지 마라.\n"
        '반드시 다음 JSON 배열 형식으로만 출력해라. 설명이나 마크다운 없이: '
        '["문장1","문장2","문장3","문장4","문장5","문장6"]'
    )
    if safe:
        prompt += prompt_contract(market_data)
    return prompt


def _check_safe_line(index: int, raw_line: str, hook_type: str, market_data: dict) -> tuple[str | None, str | None]:
    """안전 모드 한 문장 검사. (대입된 문장, 위반 사유) 중 하나를 반환한다."""
    from src.market_facts import check_line, resolve
    from src.renderer import HOOK_WRAP_CHARS, _wrap_korean
    from src.validation import ValidationError

    reason = check_line(raw_line)
    if reason:
        return None, reason
    try:
        line = resolve(raw_line, market_data).strip()
    except ValidationError as exc:
        return None, str(exc)
    if index == 0 and not is_valid_hook_line(line, hook_type):
        return None, f"hook_length_{HOOK_MIN_CHARS}_{HOOK_MAX_CHARS}_or_format"
    width, max_lines = (HOOK_WRAP_CHARS, 2) if index == 0 else (BODY_WRAP_CHARS, BODY_MAX_LINES)
    try:
        _wrap_korean(line, width, max_lines=max_lines)
    except ValueError:
        return None, f"caption_over_{max_lines}_lines"
    return line, None


def _evaluate_safe(raw_lines: list[str] | None, hook_type: str, market_data: dict):
    """(문장별 대입 결과 목록, {인덱스: 사유}). 파싱 실패면 전 문장을 실패로 본다."""
    if raw_lines is None:
        return [None] * BEAT_COUNT, {i: "malformed_json" for i in range(BEAT_COUNT)}
    results, problems = [], {}
    for index, raw_line in enumerate(raw_lines):
        line, reason = _check_safe_line(index, raw_line, hook_type, market_data)
        results.append(line)
        if reason:
            problems[index] = reason
            logger.warning("story_line_rejected beat=%s reason=%s line=%s", index + 1, reason, raw_line)
    return results, problems


def _generate_narrations_safe(
    client, prompt: str, villain: str, hook_type: str, market_data: dict, fallback: list[str],
) -> tuple[list[str], str | None]:
    """사실 토큰 규칙을 지키는 내레이션 생성 (최대 2회 호출: 생성 + 문장 단위 수정).

    일일 제어 한도(storyboard 2회) 안에서 동작한다. 수정 후에도 남는 위반 문장이 있으면
    짧은 폴백을 반환하고 사유를 남긴다. 폴백은 director 에서 발행 차단된다.
    """
    response = guarded_generate(client, "text", "storyboard", model=STORY_MODEL, contents=prompt)
    first_raw = _parse_narrations(response.text or "")
    first, problems = _evaluate_safe(first_raw, hook_type, market_data)
    if not problems:
        return first, None

    detail = "; ".join(f"{i + 1}번={reason}" for i, reason in sorted(problems.items()))
    repair_prompt = (
        prompt
        + "\n\n[수정 지시] 직전 응답에서 다음 문장이 규칙을 위반했다: " + detail + ". "
        + "숫자·%·수량 표현은 토큰으로만 쓰고, 토큰 앞뒤에 지표명·상승/하락을 붙이지 말고, "
        + f"1번은 {HOOK_MIN_CHARS}~{HOOK_MAX_CHARS}자로 쓴다. "
        + ("1번은 반드시 '[긴급]' 으로 시작한다. " if hook_type == "D" else "")
        + (f"직전 응답: {json.dumps(first_raw, ensure_ascii=False)}. 위반하지 않은 문장은 그대로 두고 "
           if first_raw else "")
        + "6개 문장 JSON 배열 전체만 다시 출력해라."
    )
    retry = guarded_generate(client, "text", "storyboard", model=STORY_MODEL, contents=repair_prompt)
    second, second_problems = _evaluate_safe(_parse_narrations(retry.text or ""), hook_type, market_data)

    merged: list[str | None] = []
    for index in range(BEAT_COUNT):
        if index not in second_problems:
            merged.append(second[index])
        elif index not in problems:
            merged.append(first[index])
        else:
            merged.append(None)

    if merged[0] is None:
        # 훅은 규칙 문장으로 대체할 수 있다 (프로그램 생성, 길이 보장)
        merged[0] = fallback_hook_line(hook_type, villain, market_data)
        logger.warning("hook_line_fallback_applied type=%s", hook_type)
    missing = [i + 1 for i, line in enumerate(merged) if line is None]
    if missing:
        logger.warning("story_fact_gate_failed beats=%s", missing)
        return fallback, "story:fact_gate_rejected"
    return merged, None


def _generate_narrations(
    villain: str, theme: str, market_data: dict, prev_state: dict | None = None,
    hook_type: str = "A", ending_type: str = "QUESTION",
    recent_cliffhangers: list[str] | None = None,
) -> tuple[list[str], str | None]:
    """Gemini로 내레이션 6줄을 생성한다. 실패 시 (폴백 문장, 사유)."""
    safe = bool(current())
    fallback = _fallback_narrations(villain, theme, market_data, prev_state, hook_type)
    if safe:
        from src.market_facts import resolve
        fallback = [SAFE_FALLBACK_TEXT[0], resolve("{{FACT:TNX.close}}", market_data),
                    resolve("{{FACT:VIX.close}}", market_data), *SAFE_FALLBACK_TEXT[1:]]

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return fallback, "story:no_api_key"

    try:
        from google import genai
    except ImportError:
        return fallback, "story:google_genai_not_installed"

    prompt = _build_story_prompt(villain, theme, market_data, prev_state, hook_type,
                                 ending_type, recent_cliffhangers, safe=safe)
    try:
        if safe:
            return _generate_narrations_safe(make_client(api_key), prompt, villain, hook_type,
                                             market_data, fallback)
        client = genai.Client(api_key=api_key)
        response = guarded_generate(client, "text", "storyboard", model=STORY_MODEL, contents=prompt)
        parsed = _parse_narrations(response.text or "")

        # 훅 길이/형식 제약은 모델이 자주 어긴다(기존 25~45자 요구도 초과한 전례).
        # 실패 시 제약을 더 강하게 재주입해 1회만 재생성한다.
        if parsed and not is_valid_hook_line(parsed[0], hook_type):
            logger.warning(
                "hook_line_rejected len=%s line=%s -- retrying once", len(parsed[0]), parsed[0],
            )
            retry_prompt = (
                prompt
                + f"\n\n[재작성 지시] 1번 문장이 규격을 벗어났다. "
                f"반드시 {HOOK_MIN_CHARS}~{HOOK_MAX_CHARS}자 한국어로 다시 써라. "
                + ("반드시 '[긴급]' 으로 시작해야 한다. " if hook_type == "D" else "")
                + "6개 문장 JSON 배열만 출력해라."
            )
            retry = guarded_generate(client, "text", "storyboard", model=STORY_MODEL, contents=retry_prompt)
            retried = _parse_narrations(retry.text or "")
            if retried and is_valid_hook_line(retried[0], hook_type):
                parsed = retried
            else:
                # 나머지 5줄은 살리고 훅만 규칙 문장으로 교체한다
                parsed[0] = fallback_hook_line(hook_type, villain, market_data)
                logger.warning("hook_line_fallback_applied type=%s", hook_type)
    except ControlError:
        raise
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
    villain: str, prev_state: dict | None, narrations: list[str],
    hook_type: str = "A", ending_type: str = "QUESTION",
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
        "ending_type": ending_type,
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
    ending_type = select_ending_type(prev_state)
    recent_cliffhangers = list(prev.get("recent_cliffhangers") or [])

    logger.info(
        "storyboard_started beats=%s prev_villain=%s hook_type=%s ending_type=%s avoid=%s",
        BEAT_COUNT, prev.get("villain"), hook_type, ending_type, len(recent_cliffhangers),
    )
    narrations, degraded = _generate_narrations(
        villain, theme, market_data, prev_state, hook_type, ending_type, recent_cliffhangers
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
    story_state = build_story_state(villain, prev_state, narrations, hook_type, ending_type)
    logger.info(
        "storyboard_finished beats=%s streak=%s hook_type=%s ending_type=%s hook='%s' degraded=%s",
        len(storyboard), story_state["villain_streak"], hook_type, ending_type,
        storyboard[0]["narration"], degraded,
    )
    return storyboard, story_state, degraded
