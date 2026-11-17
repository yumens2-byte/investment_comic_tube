"""CAST-LINE live 발행 진입점 — 컷 합성(renderer 재용) → YouTube 업로드.

실행 조건: CAST_EXECUTION_MODE=live (워크플로우 publish job 게이트와 이중 일치).
입력: artifacts_cast/cast_plan.json + generate_ledger.json + cut_XX.mp4
산출: output_short.mp4 (renderer 합성) → upload_to_youtube 결과.
"""
from __future__ import annotations

import json
import logging
import os

from src.cast import publish as cast_publish

logger = logging.getLogger("cast.publish")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    with open("artifacts_cast/cast_plan.json") as f:
        tier = json.load(f)["tier"]
    with open("artifacts_cast/generate_ledger.json") as f:
        ledger = json.load(f)
    mode = ledger.get("mode") or os.environ.get("CAST_EXECUTION_MODE", "live")
    cuts = [r for r in ledger["results"] if r.get("path") and os.path.exists(r["path"])]
    logger.info("publish_entry tier=%s mode=%s cuts=%d", tier, mode, len(cuts))

    scenes = cast_publish.build_cast_scenes(cuts)
    from src.renderer import render_video
    script_data = {"villain": "Debt Titan", "track": "CAST"}  # outro 제외·BGM 매칭용
    video_path = render_video(script_data, scenes=scenes, require_storyboard=True)

    result = cast_publish.publish_cast(video_path, tier, mode)
    if result is None:
        logger.info("publish_not_executed mode=%s", mode)
    else:
        logger.info("publish_done tier=%s", tier)


if __name__ == "__main__":
    main()
