"""CAST-LINE — T3 주 1회 앙상블 라인 (2026-11-17 상세설계 v1.0 준용).

캐논: 레포 내 스냅샷 src/cast/_canon_data.json(원본 investment-os gen/hero_shorts/canon_data.json, 2026-09-21 동기화) — DN2026_0114 주석 정정.
REF 미확정 캐릭터 배정 컷은 생성 차단(fail-closed) — 텍스트 폴백 금지.
"""
from .tier import classify_tier
from .casting import cast_for_tier

VERSION = "1.3.4"  # DN2026_0114 → DN2026_0115(P2 스토리·내레이션) → DN2026_0116(내레이션 맞춤 컷 길이·무음 제거·압축 1.25·속도 지시·과길이 재합성) → DN2026_0117(썸네일·재생목록 미사용·ID 마스킹)

__all__ = ["VERSION", "classify_tier", "cast_for_tier"]  # DN2026_0114
