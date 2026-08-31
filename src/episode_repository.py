"""Persistence adapters for validated pilot episodes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import requests

from src.episode import Episode
from src.video_pilot import VideoArtifact


@dataclass(frozen=True)
class SaveResult:
    episode_id: str
    backend: str
    content_hash: str
    artifact_path: str | None = None


def content_hash(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LocalEpisodeRepository:
    def __init__(self, artifact_dir: str | Path = "artifacts") -> None:
        self.artifact_dir = Path(artifact_dir)

    def save(self, episode: Episode) -> SaveResult:
        digest = content_hash(episode.source)
        target = self.artifact_dir / episode.episode_id
        target.mkdir(parents=True, exist_ok=True)
        episode_path = target / "episode.json"
        manifest_path = target / "manifest.json"
        episode_temporary = target / ".episode.json.tmp"
        manifest_temporary = target / ".manifest.json.tmp"
        episode_temporary.write_text(
            json.dumps(episode.source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        manifest = {
            "episode_id": episode.episode_id,
            "status": "SCRIPT_READY",
            "content_hash": digest,
            "total_duration_seconds": episode.total_duration_seconds,
            "sequence_count": len(episode.sequences),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        manifest_temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        episode_temporary.replace(episode_path)
        manifest_temporary.replace(manifest_path)
        return SaveResult(episode.episode_id, "local", digest, str(episode_path))

    def mark_rendered(self, episode: Episode, video: VideoArtifact) -> None:
        target = self.artifact_dir / episode.episode_id
        manifest_path = target / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Episode manifest does not exist: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("content_hash") != content_hash(episode.source):
            raise ValueError("Episode content changed after SCRIPT_READY")
        manifest.update({
            "status": "RENDERED",
            "video_path": str(video.path),
            "video_hash": video.sha256,
            "video_duration_seconds": video.duration_seconds,
            "video_width": video.width,
            "video_height": video.height,
            "video_codec": video.video_codec,
            "audio_codec": video.audio_codec,
            "rendered_at": datetime.now(timezone.utc).isoformat(),
        })
        temporary = target / ".manifest.json.tmp"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(manifest_path)


class SupabaseEpisodeRepository:
    """Small PostgREST adapter; keeps service-role credentials out of clients/logs."""

    def __init__(self, url: str, key: str, timeout_seconds: float = 15) -> None:
        if not url or not key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY are required")
        self.url = url.rstrip("/")
        self.key = key
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> "SupabaseEpisodeRepository":
        return cls(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", ""))

    def save(self, episode: Episode) -> SaveResult:
        digest = content_hash(episode.source)
        record = {
            "episode_id": episode.episode_id,
            "market_date": episode.market_date.isoformat(),
            "status": "SCRIPT_READY",
            "content_hash": digest,
            "payload": episode.source,
            "total_duration_seconds": episode.total_duration_seconds,
        }
        response = requests.post(
            f"{self.url}/rest/v1/episodes?on_conflict=episode_id",
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            json=record,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return SaveResult(episode.episode_id, "supabase", digest)

    def mark_rendered(self, episode: Episode, video: VideoArtifact) -> None:
        digest = content_hash(episode.source)
        response = requests.patch(
            f"{self.url}/rest/v1/episodes",
            params={"episode_id": f"eq.{episode.episode_id}", "content_hash": f"eq.{digest}"},
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json={
                "status": "RENDERED",
                "video_path": str(video.path),
                "video_hash": video.sha256,
                "video_metadata": {
                    "duration_seconds": video.duration_seconds,
                    "width": video.width,
                    "height": video.height,
                    "video_codec": video.video_codec,
                    "audio_codec": video.audio_codec,
                },
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        if not response.json():
            raise RuntimeError("Supabase render update matched no episode; content may have changed")
