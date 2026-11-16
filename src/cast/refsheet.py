"""REF 시트 URL 주입 — gen/data/ref_sheets.json (investment-os canon.py 동일 구조).

파일 형식: {"EDT": ["https://..."], "LEV-01": ["https://..."], "FG-01": ["https://..."]}
fal은 젠스파크 래퍼 URL을 다운로드하지 못하므로(실측 422) AI Drive readable URL을 기입한다.
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
