"""REF 시트 URL 주입 — gen/data/ref_sheets.json (investment-os canon.py 동일 구조).

파일 형식: {"EDT": ["https://..."], "LEV-01": ["https://..."], "FG-01": ["https://..."]}
URL은 refresh_refs가 실행 시점에 저장소 assets/refs raw URL로 채운다(부록 S, DN2026_0114 주석 정정).
"""
from __future__ import annotations

import json
import os

DEFAULT_REFSHEET_FILE = os.path.join(os.path.dirname(__file__), "_ref_sheets.json")


def _refsheet_file() -> str:
    """환경변수를 매 호출마다 읽는다 — 임포트 시 고정 버그 방지."""
    return os.environ.get("CAST_REFSHEET_FILE", DEFAULT_REFSHEET_FILE)


def load_ref_urls() -> dict[str, list[str]]:
    """REF 시트 URL 주입형 파일을 읽는다. 부재 시 캐릭터별 빈 리스트."""
    path = _refsheet_file()
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    return {k: list(v) for k, v in data.items() if isinstance(v, list)}


def ref_url_for(refs: dict[str, list[str]], character: str) -> str | None:
    """캐릭터의 첫 REF URL. 미등록이면 None — 호출부에서 fail-closed 판정한다."""
    urls = refs.get(character) or []
    return urls[0] if urls else None
