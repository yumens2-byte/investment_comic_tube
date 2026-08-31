#!/usr/bin/env python3
"""Validate an episode fixture and persist a reproducible pilot artifact."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.episode import Episode, EpisodeValidationError  # noqa: E402
from src.episode_repository import (  # noqa: E402
    LocalEpisodeRepository,
    SupabaseEpisodeRepository,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the episode ingestion pilot")
    parser.add_argument("input", type=Path, help="episode JSON file")
    parser.add_argument("--artifact-dir", default=os.getenv("ARTIFACT_DIR", "artifacts"))
    parser.add_argument(
        "--backend", choices=("local", "supabase"), default="local",
        help="local is safe and is the default; supabase performs an external write",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        episode = Episode.from_json(args.input.read_text(encoding="utf-8"))
        repository = (
            LocalEpisodeRepository(args.artifact_dir)
            if args.backend == "local"
            else SupabaseEpisodeRepository.from_environment()
        )
        result = repository.save(episode)
    except (OSError, ValueError, EpisodeValidationError, requests.RequestException) as exc:
        print(json.dumps({"result": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps({
        "result": "PASS",
        "episode_id": result.episode_id,
        "backend": result.backend,
        "content_hash": result.content_hash,
        "artifact_path": result.artifact_path,
        "duration_seconds": episode.total_duration_seconds,
        "sequence_count": len(episode.sequences),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
