"""CAST-LINE 발행 진입점 — 컷 합성(src.cast.compose) → 원장 선기록 → YouTube (DN2026_0114).

입력: artifacts_cast/cast_plan.json + generate_ledger.json + 컷 mp4
산출: artifacts_cast/cast_short.mp4, artifacts_cast/publish_result.json
"""
from __future__ import annotations

import json
import logging
import os

from src.cast.compose import compose
from src.cast.publish import PublishBlocked, publish_cast

try:
    from src.cast import VERSION
except ImportError:
    VERSION = "1.1.0"

logger = logging.getLogger("cast.publish")

ART_DIR = "artifacts_cast"
OUT_PATH = os.path.join(ART_DIR, "cast_short.mp4")
RESULT_PATH = os.path.join(ART_DIR, "publish_result.json")


def resolve_mode() -> str:
    """CAST_EXECUTION_MODE 정규화 — src.cast.mode 단일 파서 사용(R6-01 #14).  # DN2026_0114"""
    from src.cast.mode import resolve_mode as _resolve
    return _resolve(os.environ.get("CAST_EXECUTION_MODE"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    mode = resolve_mode()
    logger.info("[CAST] v%s 시작 stage=publish mode=%s", VERSION, mode)  # DN2026_0114

    with open(os.path.join(ART_DIR, "cast_plan.json"), encoding="utf-8") as f:
        plan_doc = json.load(f)
    with open(os.path.join(ART_DIR, "generate_ledger.json"), encoding="utf-8") as f:
        gen_ledger = json.load(f)

    # DN2026_0114 부분 생성물 발행 금지 — 계획 컷 수 == 실존 mp4 수
    planned = len(plan_doc.get("plan") or [])
    cuts = [r for r in (gen_ledger.get("results") or [])
            if r.get("path") and os.path.exists(r["path"])]
    if planned == 0 or len(cuts) != planned:
        raise PublishBlocked(f"컷 수 불일치 planned={planned} existing={len(cuts)} — 부분 생성물 발행 금지")

    composed = compose(cuts, plan_doc.get("villain"), OUT_PATH)

    def _write(result: dict) -> None:
        with open(RESULT_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    base = {"version": VERSION, "mode": mode, "run_id": plan_doc.get("run_id"),
            "video_path": composed["path"], "duration_sec": composed["duration_sec"],
            "thumbnail": composed["thumbnail"]}
    # DN2026_0114 (R6-02 #4): 업로드 직후·원장 mark 전에 video_id를 먼저 기록(mark 실패 시 대사 근거)
    video_id = publish_cast(composed["path"], plan_doc, mode, composed["thumbnail"],
                            on_uploaded=lambda vid: _write({**base, "status": "uploaded_unmarked",
                                                           "youtube_video_id": vid}))

    result = {
        "version": VERSION, "mode": mode, "run_id": plan_doc.get("run_id"),
        "video_path": composed["path"], "duration_sec": composed["duration_sec"],
        "thumbnail": composed["thumbnail"],
        "status": "published" if video_id else "skipped",
        "youtube_video_id": video_id,
    }
    _write(result)
    logger.info("[CAST] publish_result status=%s", result["status"])


if __name__ == "__main__":
    main()
