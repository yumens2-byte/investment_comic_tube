"""오프닝 훅 유형 정의 및 선택 (F-1).

쇼츠 피드에서 0~3초 안에 스크롤을 멈추게 하는 것이 목적이다.
평서형 "시장 상황 제시"로는 스와이프를 막지 못하므로, 4가지 훅 유형 중
하나를 반드시 선행 배치하도록 강제한다.

유형 선택은 시장 국면(빌런)과 연동하되, 직전 회차와 같은 유형이 연속되지
않도록 강제 전환한다. 동일 패턴 반복은 시청자 피로를 부르고
X 안티봇 원칙(동일 일정·동일 문구 금지)에도 어긋난다.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

HOOK_A = "A"  # 파격적 충격/경고형
HOOK_B = "B"  # 빌런 도발/대결 구도형
HOOK_C = "C"  # 반전/팩트 폭격형
HOOK_D = "D"  # 긴급 속보/시그니처 오프닝형

HOOK_TYPES = [HOOK_A, HOOK_B, HOOK_C, HOOK_D]

# 훅 문장 길이 제약. 한국어 기준 18자 ≈ 낭독 2.6초로 3초 한도 안에 들어온다.
HOOK_MIN_CHARS = 12
HOOK_MAX_CHARS = 18

HOOK_SPECS = {
    HOOK_A: {
        "name": "충격/경고형",
        "guide": "시청자의 공포나 FOMO를 자극하는 경고. 단정적이고 위협적으로 끝낸다.",
        "example": "지금 들어가면 계좌 타버립니다",
        "tts_tone": "매우 높은 에너지로, 경고하듯 강하게 외치는 톤",
        "sfx": "hook_a",
    },
    HOOK_B: {
        "name": "빌런 대결형",
        "guide": "빌런이 방금 나타나 방어선이 뚫리기 직전인 충돌 상황을 선언한다.",
        "example": "나스닥 방어선이 뚫리기 직전",
        "tts_tone": "비장하고 묵직한 저음으로, 결전을 알리듯",
        "sfx": "hook_b",
    },
    HOOK_C: {
        "name": "반전/팩트형",
        "guide": "대중의 상식을 뒤엎는 한 줄. 궁금증을 남기고 답을 주지 않는다.",
        "example": "개미가 던질 때 그들은 담았다",
        "tts_tone": "낮게 속삭이듯 시작해 마지막 어절을 강하게 찍는 톤",
        "sfx": "hook_c",
    },
    HOOK_D: {
        "name": "긴급 속보형",
        "guide": "유니버스 긴급 상황 선언. 반드시 '[긴급]' 으로 시작한다.",
        "example": "[긴급] EDT 방어선 붕괴 직전",
        "tts_tone": "속보 아나운서처럼 빠르고 또렷하게, 높은 긴장감으로",
        "sfx": "hook_d",
    },
}

# 시장 국면(빌런) -> 1순위 훅 유형
VILLAIN_PREFERRED_HOOK = {
    "Chaos Reaper": HOOK_A,   # 변동성 폭발 -> 공포 경고
    "Debt Titan": HOOK_B,     # 금리 압력 -> 대결 구도
    "Bull Brute": HOOK_C,     # 상승 모멘텀 -> 반전 팩트
}

# 장기전(같은 빌런 연속 등장)일 때는 속보형으로 전환해 매너리즘을 깬다
STREAK_THRESHOLD_FOR_URGENT = 3

# 폴백 훅 문장은 18자 상한이라 영문 빌런명(Debt Titan=10자)을 쓰면 어절이 잘린다.
# 한글 별칭으로 길이를 확보한다.
VILLAIN_KR = {
    "Debt Titan": "뎁트타이탄",
    "Chaos Reaper": "카오스리퍼",
    "Bull Brute": "불브루트",
}


def _has_final_consonant(word: str) -> bool:
    """한글 마지막 글자에 받침이 있는지 판정한다 (조사 선택용)."""
    if not word:
        return False
    ch = word[-1]
    if not ("가" <= ch <= "힣"):
        return False
    return (ord(ch) - 0xAC00) % 28 != 0


def subject_particle(word: str) -> str:
    """받침 있으면 '이', 없으면 '가'."""
    return "이" if _has_final_consonant(word) else "가"


def select_hook_type(villain: str, prev_state: dict | None, villain_streak: int) -> str:
    """이번 회차에 쓸 훅 유형을 결정한다."""
    if villain_streak >= STREAK_THRESHOLD_FOR_URGENT:
        chosen = HOOK_D
        reason = f"streak={villain_streak}"
    else:
        chosen = VILLAIN_PREFERRED_HOOK.get(villain, HOOK_A)
        reason = f"villain={villain}"

    prev_hook = ((prev_state or {}).get("story_state") or {}).get("hook_type")
    if prev_hook and prev_hook == chosen:
        # 직전과 같은 유형이면 다음 유형으로 밀어 반복을 끊는다
        idx = HOOK_TYPES.index(chosen)
        chosen = HOOK_TYPES[(idx + 1) % len(HOOK_TYPES)]
        reason += f", rotated_from={prev_hook}"

    logger.info("hook_type_selected type=%s (%s) reason=%s",
                chosen, HOOK_SPECS[chosen]["name"], reason)
    return chosen


def is_valid_hook_line(line: str, hook_type: str) -> bool:
    """훅 문장이 길이/형식 제약을 만족하는지 검사한다."""
    if not line:
        return False
    text = line.strip()
    if not (HOOK_MIN_CHARS <= len(text) <= HOOK_MAX_CHARS):
        return False
    # 긴급 속보형은 시그니처 접두사가 곧 유형의 정체성이므로 강제한다
    return not (hook_type == HOOK_D and not text.startswith("[긴급]"))


def fallback_hook_line(hook_type: str, villain: str, market_data: dict) -> str:
    """모델이 제약을 못 맞췄을 때 쓰는 규칙 기반 훅 문장 (길이 보장)."""
    vix = (market_data.get("VIX") or {}).get("close")
    tnx = (market_data.get("TNX") or {}).get("close")

    name = VILLAIN_KR.get(villain, "빌런")

    candidates = {
        HOOK_A: f"공포지수 {vix} 경고등 켜졌다" if vix is not None else "시장에 경고등이 켜졌다",
        HOOK_B: f"{name}{subject_particle(name)} 방어선을 노린다",
        HOOK_C: f"금리 {tnx} 뒤에 숨은 진실" if tnx is not None else "지금 시장이 숨긴 진실",
        HOOK_D: f"[긴급] {name} 방어선 붕괴",
    }
    line = candidates.get(hook_type, "시장에 경고등이 켜졌다")

    # 상한 초과 시 어절 경계에서 자른다. 글자 단위로 자르면 단어가 깨진다.
    if len(line) > HOOK_MAX_CHARS:
        words = line.split()
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if len(candidate) > HOOK_MAX_CHARS:
                break
            line = candidate
        if not line:
            line = words[0][:HOOK_MAX_CHARS]
    return line
