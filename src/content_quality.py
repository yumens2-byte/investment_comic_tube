"""Pre-publication checks for the six-beat EDT Shorts media package."""

from __future__ import annotations

from src.validation import ValidationError

VERSION = "1.0.0"
EXPECTED_IMAGE_SLOTS = 4
EXPECTED_NARRATIONS = 6


class ContentQualityError(ValidationError):
    """A required image or narration is absent from the finished story."""


def validate_media_package(image_paths: list[str | None], audio_paths: list[str | None]) -> None:
    """Reject incomplete packages before rendering or uploading a degraded video.

    Image slots are reused by six beats, but each planned slot must have its own
    generated image. Every beat needs narration; silent fallbacks obscure facts.
    Existence and codec checks belong to the renderer's output QA.
    """
    if len(image_paths) != EXPECTED_IMAGE_SLOTS or any(not path for path in image_paths):
        raise ContentQualityError(
            f"필수 장면 이미지 {EXPECTED_IMAGE_SLOTS}개가 모두 생성되지 않았다"
        )
    if len(set(image_paths)) != EXPECTED_IMAGE_SLOTS:
        raise ContentQualityError("서로 다른 장면 슬롯이 동일 이미지 파일을 참조한다")
    if len(audio_paths) != EXPECTED_NARRATIONS or any(not path for path in audio_paths):
        raise ContentQualityError(
            f"내레이션 {EXPECTED_NARRATIONS}개가 모두 생성되지 않았다"
        )
