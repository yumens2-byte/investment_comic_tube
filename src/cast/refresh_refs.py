"""ref_sheets URL 러너 자동재발급 — generate 직전에 REF URL을 신선하게 확보.

배경 (부록 E 실측):
- fal은 젠스파크 래퍼 URL(/api/files/s/...)을 다운로드하지 못한다(422).
- AI Drive get_readable_url로 발급한 readable URL은 약 1시간 만료.
- 따라서 러너는 실행 시점마다 AI Drive에서 재발급해 _ref_sheets.json을 갱신한다.

방식: gsk CLI(러너 환경에 사전 구성)를 서브프로세스로 호출.
키·토큰은 절대 로그에 남기지 않는다(S4 누출 방지 — pipeline.yml preflight 원칙 준용).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess

logger = logging.getLogger("cast.refresh_refs")

DRIVE_REFS = {
    "EDT": "/hero-shorts/media/ref_edt_rcl07b.png",
    "LEV-01": "/hero-shorts/media/ref_lev01.png",
    "FG-01": "/hero-shorts/media/ref_fg01_v3.png",
}
URL_RE = re.compile(r"https://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")


def _readable_url(drive_path: str) -> str:
    """gsk aidrive get_readable_url → 공개 readable URL 1건."""
    r = subprocess.run(["gsk", "aidrive", "get_readable_url", drive_path],
                       capture_output=True, text=True, timeout=120)
    m = URL_RE.search(r.stdout)
    if not m:
        raise RuntimeError(f"readable URL 발급 실패: {drive_path} (stdout 비어있음)")
    url = m.group(0).rstrip('",}]')
    return url


def refresh(refsheet_path: str | None = None) -> dict[str, str]:
    """모든 캐릭터 REF URL 재발급 → refsheet 파일 갱신. 반환: {캐릭터: url}.

    개별 실패는 전체 중단으로 처리한다 — REF 일부 누락 상태 생성은 fail-closed 위반.
    """
    from src.cast.refsheet import _refsheet_file
    path = refsheet_path or _refsheet_file()
    with open(path) as f:
        doc = json.load(f)

    refreshed = {}
    for character, drive_path in DRIVE_REFS.items():
        url = _readable_url(drive_path)
        doc[character] = [url]
        refreshed[character] = url
        logger.info("ref_refreshed character=%s url_len=%d", character, len(url))

    with open(path, "w") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return refreshed


def all_refs_fresh(refsheet_path: str | None = None) -> bool:
    """3종 REF URL이 모두 주입돼 있는지 — generate 진입 전 게이트."""
    from src.cast.refsheet import load_ref_urls, _refsheet_file
    load_ref_urls.cache_clear() if hasattr(load_ref_urls, "cache_clear") else None
    path = refsheet_path or _refsheet_file()
    urls = load_ref_urls.__wrapped__(path) if hasattr(load_ref_urls, "__wrapped__") else None
    if urls is None:
        with open(path) as f:
            doc = json.load(f)
        urls = {k: v for k, v in doc.items() if k in DRIVE_REFS and isinstance(v, list)}
    return all(urls.get(c) for c in DRIVE_REFS)
