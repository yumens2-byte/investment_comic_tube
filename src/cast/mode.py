"""CAST-LINE 실행 모드 정규화 — 3값 외 입력은 dry_run 강등. # DN2026_0114

모드 표준식: workflow_dispatch inputs.mode → vars.CAST_EXECUTION_MODE → 'dry_run'.
yml 식이 어떤 값을 넘기더라도 코드는 dry_run/shadow/live 외 값을 실호출로 해석하지 않는다.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("cast.mode")

MODES = ("dry_run", "shadow", "live")  # DN2026_0114


def resolve_mode(raw: str | None) -> str:
    """strip·lower 후 MODES에 있으면 그 값, 아니면 'dry_run'(경고 로그). # DN2026_0114"""
    norm = (raw or "").strip().lower()
    if norm in MODES:
        return norm
    logger.warning("mode_demoted raw=%r", raw)
    return "dry_run"
