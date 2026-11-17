"""ref_sheets URL 러너 자동해석 — generate 직전에 REF URL을 확보.

변경 이력 (부록 S, 2026-11-17):
- 구현: gsk aidrive get_readable_url 서브프로세스 호출 → GitHub 러너에 gsk가
  없어 FileNotFoundError로 실패 (T1 수행 실측).
- 수정: REF 원본 3종을 저장소 assets/refs/에 커밋하고 raw.githubusercontent.com
  URL을 사용한다 (공개 저장소 — fal 다운로드 가능, 1시간 만료 없음).
- gsk 경로는 로컬(sandbox) 실행 폴백으로만 유지. raw HEAD 검증 실패 시에만
  폴백하며, 둘 다 실패하면 전체 중단(fail-closed).

주의: 저장소가 공개이므로 REF 이미지도 공개된다 — 마스터 승인 사항 (부록 S).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.request

logger = logging.getLogger("cast.refresh_refs")

RAW_BASE = "https://raw.githubusercontent.com/yumens2-byte/investment_comic_tube/main/assets/refs"
RAW_REFS = {
    "EDT": f"{RAW_BASE}/ref_edt_rcl07b.png",
    "LEV-01": f"{RAW_BASE}/ref_lev01.png",
    "FG-01": f"{RAW_BASE}/ref_fg01_v3.png",
}
DRIVE_REFS = {  # 로컬 폴백용 (gsk 가용 환경)
    "EDT": "/hero-shorts/media/ref_edt_rcl07b.png",
    "LEV-01": "/hero-shorts/media/ref_lev01.png",
    "FG-01": "/hero-shorts/media/ref_fg01_v3.png",
}
URL_RE = re.compile(r"https://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")


def _raw_url_ok(url: str) -> bool:
    """raw URL HEAD 200 검증 — 실패 시 폴백 트리거 (fail-closed)."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception as e:
        logger.info("raw_url_check_failed url_len=%d err=%s", len(url), type(e).__name__)
        return False


def _readable_url(drive_path: str) -> str:
    """[폴백] gsk aidrive get_readable_url → 공개 readable URL 1건 (로컬 전용)."""
    r = subprocess.run(["gsk", "aidrive", "get_readable_url", drive_path],
                       capture_output=True, text=True, timeout=120)
    m = URL_RE.search(r.stdout)
    if not m:
        raise RuntimeError(f"readable URL 발급 실패: {drive_path} (stdout 비어있음)")
    url = m.group(0).rstrip('",}]')
    return url


def _resolve_url(character: str) -> tuple[str, str]:
    """REF URL 해석 — raw 우선, gsk 폴백. 반환: (url, source)."""
    raw = RAW_REFS[character]
    if _raw_url_ok(raw):
        return raw, "raw"
    url = _readable_url(DRIVE_REFS[character])
    return url, "gsk_fallback"


def refresh(refsheet_path: str | None = None) -> dict[str, str]:
    """모든 캐릭터 REF URL 확보 → refsheet 파일 갱신. 반환: {캐릭터: url}.

    개별 실패는 전체 중단으로 처리한다 — REF 일부 누락 상태 생성은 fail-closed 위반.
    """
    from src.cast.refsheet import _refsheet_file
    path = refsheet_path or _refsheet_file()
    with open(path) as f:
        doc = json.load(f)

    refreshed = {}
    for character in RAW_REFS:
        url, source = _resolve_url(character)
        doc[character] = [url]
        refreshed[character] = url
        logger.info("ref_refreshed character=%s source=%s url_len=%d",
                    character, source, len(url))

    with open(path, "w") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return refreshed


def all_refs_fresh(refsheet_path: str | None = None) -> bool:
    """3종 REF URL이 모두 주입돼 있는지 — generate 진입 전 게이트."""
    from src.cast.refsheet import _refsheet_file
    path = refsheet_path or _refsheet_file()
    with open(path) as f:
        doc = json.load(f)
    return all(doc.get(c) for c in RAW_REFS)
