"""CAST-LINE — T3 주 1회 앙상블 라인 (2026-11-17 상세설계 v1.0 준용).

캐논: 레포 내 스냅샷 src/cast/_canon_data.json(원본 investment-os gen/hero_shorts/canon_data.json, 2026-09-21 동기화) — DN2026_0114 주석 정정.
REF 미확정 캐릭터 배정 컷은 생성 차단(fail-closed) — 텍스트 폴백 금지.
"""
from .tier import classify_tier
from .casting import cast_for_tier

VERSION = "1.4.0"  # DN2026_0123 (…→0121→0122→0123)

__all__ = ["VERSION", "classify_tier", "cast_for_tier"]  # DN2026_0114
