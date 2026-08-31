"""Validated episode contract used by the pilot pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import re
from typing import Any


EPISODE_ID = re.compile(r"^EP-(\d{8})-(\d{2})$")
DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")
REQUIRED_MARKET_FIELDS = {
    "date",
    "DGS10",
    "VIX",
    "NASDAQ_change",
    "S&P500_change",
    "DOW_change",
}


class EpisodeValidationError(ValueError):
    """Raised when generated episode JSON is not safe to process."""


def _number(value: str, *, percent: bool = False) -> float:
    text = value.strip()
    if percent:
        if not text.endswith("%"):
            raise EpisodeValidationError(f"percentage value must end with %: {value!r}")
        text = text[:-1]
    try:
        return float(text)
    except ValueError as exc:
        raise EpisodeValidationError(f"invalid numeric value: {value!r}") from exc


@dataclass(frozen=True)
class Sequence:
    sequence_id: str
    duration_seconds: float
    caption: str
    narration_tts: str
    video_prompt: str

    @classmethod
    def from_dict(cls, value: dict[str, Any], position: int) -> "Sequence":
        expected_id = f"Seq_{position}"
        if value.get("sequence_id") != expected_id:
            raise EpisodeValidationError(f"sequence_id must be {expected_id}")
        match = DURATION.fullmatch(str(value.get("duration", "")))
        if not match:
            raise EpisodeValidationError(f"invalid duration in {expected_id}")
        duration = float(match.group(1))
        if not 1 <= duration <= 20:
            raise EpisodeValidationError(f"duration must be 1-20 seconds in {expected_id}")

        required_text = ("caption", "narration_tts", "video_prompt")
        for field in required_text:
            if not str(value.get(field, "")).strip():
                raise EpisodeValidationError(f"{field} is required in {expected_id}")

        prompt = str(value["video_prompt"])
        if "9:16" not in prompt:
            raise EpisodeValidationError(f"video_prompt must specify 9:16 in {expected_id}")
        return cls(
            sequence_id=expected_id,
            duration_seconds=duration,
            caption=str(value["caption"]),
            narration_tts=str(value["narration_tts"]),
            video_prompt=prompt,
        )


@dataclass(frozen=True)
class Episode:
    episode_id: str
    market_date: date
    dgs10: float
    vix: float
    nasdaq_change: float
    sp500_change: float
    dow_change: float
    antagonist: str
    theme_type: str
    core_conflict: str
    sequences: tuple[Sequence, ...]
    source: dict[str, Any]

    @property
    def total_duration_seconds(self) -> float:
        return sum(item.duration_seconds for item in self.sequences)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Episode":
        match = EPISODE_ID.fullmatch(str(value.get("episode_id", "")))
        if not match:
            raise EpisodeValidationError("episode_id must match EP-YYYYMMDD-NN")

        summary = value.get("data_summary")
        if not isinstance(summary, dict) or REQUIRED_MARKET_FIELDS - summary.keys():
            missing = sorted(REQUIRED_MARKET_FIELDS - (summary or {}).keys())
            raise EpisodeValidationError(f"missing data_summary fields: {missing}")
        try:
            market_date = date.fromisoformat(str(summary["date"]))
        except ValueError as exc:
            raise EpisodeValidationError("data_summary.date must be ISO YYYY-MM-DD") from exc
        if market_date.strftime("%Y%m%d") != match.group(1):
            raise EpisodeValidationError("episode_id date must equal data_summary.date")

        narrative = value.get("narrative_theme")
        if not isinstance(narrative, dict):
            raise EpisodeValidationError("narrative_theme is required")
        for field in ("antagonist", "theme_type", "core_conflict"):
            if not str(narrative.get(field, "")).strip():
                raise EpisodeValidationError(f"narrative_theme.{field} is required")

        raw_sequences = value.get("sequence_pipeline")
        if not isinstance(raw_sequences, list) or not raw_sequences:
            raise EpisodeValidationError("sequence_pipeline must not be empty")
        sequences = tuple(
            Sequence.from_dict(item, position)
            for position, item in enumerate(raw_sequences, start=1)
        )
        total_duration = sum(item.duration_seconds for item in sequences)
        if not 15 <= total_duration <= 60:
            raise EpisodeValidationError("total duration must be 15-60 seconds")

        return cls(
            episode_id=str(value["episode_id"]),
            market_date=market_date,
            dgs10=_number(str(summary["DGS10"]), percent=True),
            vix=_number(str(summary["VIX"])),
            nasdaq_change=_number(str(summary["NASDAQ_change"]), percent=True),
            sp500_change=_number(str(summary["S&P500_change"]), percent=True),
            dow_change=_number(str(summary["DOW_change"]), percent=True),
            antagonist=str(narrative["antagonist"]),
            theme_type=str(narrative["theme_type"]),
            core_conflict=str(narrative["core_conflict"]),
            sequences=sequences,
            source=value,
        )

    @classmethod
    def from_json(cls, payload: str) -> "Episode":
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise EpisodeValidationError(f"invalid JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise EpisodeValidationError("episode payload must be an object")
        return cls.from_dict(value)
