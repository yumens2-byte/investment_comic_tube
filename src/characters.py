"""EDT Universe 캐릭터 시트.

이미지 생성 프롬프트에 들어가는 외형 정의의 단일 소스(single source of truth).
캐릭터 외형을 바꾸려면 이 파일만 수정하면 되고, image_generator 는 건드리지 않는다.
회차마다 외형이 흔들리지 않도록(캐릭터 일관성) 문장을 구체적으로 고정해 둔다.

주의: 아래 외형 정의는 마스터 확정 사양이 아니라 기본안이다.
      확정 사양을 받으면 이 상수만 교체한다.
"""

from __future__ import annotations

# 히어로: 의인화 호랑이 (직립 이족보행, 수트 착용)
HERO_NAME = "EDT"
HERO_APPEARANCE = (
    "The hero is called EDT. He is an anthropomorphic tiger: a muscular bipedal tiger "
    "standing upright on two legs, with orange fur and bold black stripes, white muzzle "
    "and chest, fierce amber eyes. He wears a blue and orange high-tech combat suit, "
    "armored gauntlets and boots. He is clearly a tiger, not a human -- he has a tiger "
    "head, tiger ears, paws with claws, and a striped tail. "
    "His signature weapon is a large mechanical CHAINSAW: a heavy roaring chainsaw with "
    "a long toothed bar, glowing orange energy along the chain, gripped in both paws. "
    "The chainsaw must be clearly visible in his hands in every scene where he appears."
)

# 빌런: villain 키 -> 외형 정의
VILLAIN_APPEARANCE = {
    "Debt Titan": (
        "The villain 'Debt Titan' is a colossal stone-and-iron giant bound in heavy "
        "rusted chains, cracks glowing dark red like molten debt, hollow burning eyes."
    ),
    "Chaos Reaper": (
        "The villain 'Chaos Reaper' is a tall hooded specter of swirling black and "
        "violet smoke, wielding a jagged scythe made of shattered market charts, "
        "with no visible face except two cold white lights."
    ),
    "Bull Brute": (
        "The villain 'Bull Brute' is a massive armored bull-beast with golden horns, "
        "steaming nostrils and hooves wreathed in green flame, charging forward."
    ),
}

DEFAULT_VILLAIN_APPEARANCE = (
    "The villain is a towering menacing figure embodying market danger."
)


def get_villain_appearance(villain: str) -> str:
    """빌런 이름에 대응하는 외형 정의를 반환한다. 미등록 빌런은 일반 정의로 폴백."""
    return VILLAIN_APPEARANCE.get(villain, DEFAULT_VILLAIN_APPEARANCE)
