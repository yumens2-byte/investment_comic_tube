"""CAST-LINE — T3 주 1회 앙상블 라인 (2026-11-17 상세설계 v1.0 준용).

캐논 단일 출처: gen/hero_shorts/canon_data.json 의 spec_kr_verbatim.
REF 미확정 캐릭터 배정 컷은 생성 차단(fail-closed) — 텍스트 폴백 금지.
"""
from .tier import classify_tier
from .casting import cast_for_tier

__all__ = ["classify_tier", "cast_for_tier"]
