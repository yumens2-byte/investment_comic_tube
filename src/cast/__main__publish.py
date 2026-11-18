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
from src.log_mask import mask_id  # DN2026_0118

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

    # DN2026_0115: 대본·내레이션 있으면 내레이션 믹스 + 자막(컷 번호 오름차순 = compose 정렬 순서)
    script = plan_doc.get("script")
    beats = (script or {}).get("beats") if isinstance(script, dict) else None
    narration = gen_ledger.get("narration")
    if beats and narration:
        captions = [b["narration"] for b in sorted(beats, key=lambda b: int(b["cut"]))]
        composed = compose(cuts, plan_doc.get("villain"), OUT_PATH, narration=narration,
                           captions=captions, market_snapshot=plan_doc.get("market_snapshot"))
    else:
        if beats and not narration:
            # R6-05 #7: 대본이 있는데 내레이션이 없으면 무내레이션 발행 금지(부분 생성물 차단과 동일 원칙)
            raise PublishBlocked("script 있으나 generate_ledger narration 없음 — 무내레이션 발행 차단")
        composed = compose(cuts, plan_doc.get("villain"), OUT_PATH)

    def _write(result: dict) -> None:
        with open(RESULT_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    base = {"version": VERSION, "mode": mode, "run_id": plan_doc.get("run_id"),
            "video_path": composed["path"], "duration_sec": composed["duration_sec"],
            "thumbnail": composed["thumbnail"], "segments": composed.get("segments")}  # DN2026_0115
    # DN2026_0114 (R6-02 #4): 업로드 직후·원장 mark 전에 video_id를 먼저 기록(mark 실패 시 대사 근거)
    video_id = publish_cast(composed["path"], plan_doc, mode, composed["thumbnail"],
                            on_uploaded=lambda vid: _write({**base, "status": "uploaded_unmarked",
                                                           "youtube_video_id": mask_id(vid)}))

    result = {
        "version": VERSION, "mode": mode, "run_id": plan_doc.get("run_id"),
        "video_path": composed["path"], "duration_sec": composed["duration_sec"],
        "thumbnail": composed["thumbnail"], "segments": composed.get("segments"),  # DN2026_0115
        "status": "published" if video_id else "skipped",
        # DN2026_0118: artifact 는 공개 — 원문 ID는 원장(cast_runs)에만. mark 실패 시 대사는 YouTube Studio 제목·시각으로
        "youtube_video_id": mask_id(video_id) if video_id else None,
    }
    _write(result)
    logger.info("[CAST] publish_result status=%s", result["status"])


if __name__ == "__main__":
    main()
