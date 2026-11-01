"""한국어 조사 선택의 단일 진입점 (DN2026_0089, STD-PROC-13).

사실 토큰은 '... 12.91% 상승' 처럼 프로그램이 대입하므로, 모델이 토큰 뒤에 쓴 조사는
대입 결과의 받침과 맞지 않을 수 있다(2026-10-31 run 41178817392 실측: '상승로').
읽기 기준으로 받침을 판정해 조사를 맞춘다.
"""
from __future__ import annotations

import re

VERSION = "1.0.0"

NONE, RIEUL, OTHER = "none", "rieul", "other"
# 숫자 끝자리의 한국어 읽기(영 일 이 삼 사 오 육 칠 팔 구)
_DIGIT_FINAL = {"0": OTHER, "1": RIEUL, "2": NONE, "3": OTHER, "4": NONE,
                "5": NONE, "6": OTHER, "7": RIEUL, "8": RIEUL, "9": NONE}
# 받침 없음 형 → 받침 있음 형
_PAIRS = {"는": "은", "가": "이", "를": "을", "와": "과"}
_TO_VOWEL = {"은": "는", "는": "는", "이": "가", "가": "가", "을": "를", "를": "를", "과": "와", "와": "와"}
# 뒤에 한글이 이어지면 조사가 아닐 수 있다('상승이었다') — 단독 조사만 고친다.
_PARTICLE = re.compile(r"(으로|로|은|는|이|가|을|를|과|와)(?![가-힣])")


def final_sound(text: str) -> str:
    """마지막 글자의 읽기 받침: NONE / RIEUL(ㄹ) / OTHER. %는 '퍼센트'로 읽는다."""
    stripped = (text or "").rstrip()
    if not stripped:
        return NONE
    ch = stripped[-1]
    if ch == "%":
        return NONE
    if ch in _DIGIT_FINAL:
        return _DIGIT_FINAL[ch]
    if "가" <= ch <= "힣":
        jong = (ord(ch) - 0xAC00) % 28
        return NONE if jong == 0 else (RIEUL if jong == 8 else OTHER)
    return NONE


def has_final_consonant(word: str) -> bool:
    """한글 마지막 글자에 받침이 있는지 (한글이 아니면 False — hooks 기존 동작 유지)."""
    if not word or not ("가" <= word[-1] <= "힣"):
        return False
    return final_sound(word) != NONE


def particle_for(phrase: str, particle: str) -> str:
    """phrase 뒤에 올 조사를 받침에 맞춘다. 알 수 없는 조사는 그대로 돌려준다."""
    sound = final_sound(phrase)
    if particle in ("으로", "로"):
        return "로" if sound in (NONE, RIEUL) else "으로"
    if particle not in _TO_VOWEL:
        return particle
    vowel = _TO_VOWEL[particle]
    return vowel if sound == NONE else _PAIRS[vowel]


def leading_particle(rest: str) -> str | None:
    """rest 가 단독 조사로 시작하면 그 조사, 아니면 None."""
    match = _PARTICLE.match(rest)
    return match[1] if match else None
