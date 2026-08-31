#!/usr/bin/env python3
"""Validate story JSON, render a preview, and update its persistence state."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.episode import Episode  # noqa: E402
from src.episode_repository import LocalEpisodeRepository, SupabaseEpisodeRepository  # noqa: E402
from src.logging_config import configure_logging  # noqa: E402
from src.video_pilot import render_storyboard_preview  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the story-to-video pilot without YouTube upload")
    parser.add_argument("input", type=Path)
    parser.add_argument("--artifact-dir", default=os.getenv("ARTIFACT_DIR", "artifacts"))
    parser.add_argument("--backend", choices=("local", "supabase"), default="local")
    args = parser.parse_args()
    configure_logging()
    try:
        episode = Episode.from_json(args.input.read_text(encoding="utf-8"))
        repository = (
            LocalEpisodeRepository(args.artifact_dir)
            if args.backend == "local"
            else SupabaseEpisodeRepository.from_environment()
        )
        repository.save(episode)
        video = render_storyboard_preview(episode, args.artifact_dir)
        repository.mark_rendered(episode, video)
    except Exception as exc:
        logging.getLogger(__name__).exception("video_pilot_failed")
        print(json.dumps({"result": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({
        "result": "PASS", "episode_id": episode.episode_id, "status": "RENDERED",
        "video_path": str(video.path), "video_hash": video.sha256,
        "duration_seconds": video.duration_seconds, "resolution": f"{video.width}x{video.height}",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
