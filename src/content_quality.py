"""Pre-publication checks for the six-beat EDT Shorts media package."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageStat, UnidentifiedImageError

from src.validation import ValidationError

VERSION = "1.2.0"
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


def validate_image_assets(image_paths: list[str | None], *, expected_slots: int = EXPECTED_IMAGE_SLOTS) -> None:
    """손상·가로 이미지와 파일럿에서 관찰된 하단 회색 빈 띠를 차단한다."""
    if len(image_paths) != expected_slots or any(not path for path in image_paths):
        raise ContentQualityError("필수 이미지 슬롯이 누락됐다")
    for index, path in enumerate(image_paths):
        try:
            with Image.open(path) as image:
                image.load()
                width, height = image.size
                if width < 512 or height < 900 or abs(width / height - 9 / 16) > 0.045:
                    raise ContentQualityError(f"scene_{index} 세로 이미지 규격 오류")
                rgb = image.convert("RGB")
                flat_bands = 0
                for fraction in (0.80, 0.85, 0.90, 0.95):
                    y = int(height * fraction)
                    sample = rgb.crop((0, y, width, min(height, y + 10)))
                    stats = ImageStat.Stat(sample)
                    if sum(stats.stddev) / 3 < 12 and max(stats.mean) - min(stats.mean) < 20:
                        flat_bands += 1
                if flat_bands >= 3:
                    raise ContentQualityError(f"scene_{index} 하단 빈 단색 영역 감지")
        except (OSError, UnidentifiedImageError) as exc:
            raise ContentQualityError(f"scene_{index} 이미지 파일을 읽을 수 없다") from exc


def validate_rendered_video(path: str) -> None:
    """최종 인코딩 결과를 업로드 전에 검사한다. 1분 미만에서 길이 목표는 강제하지 않는다."""
    output = Path(path)
    if not output.is_file() or output.stat().st_size <= 0:
        raise ContentQualityError("완성 영상 파일이 없거나 비어 있다")
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
             "-of", "json", str(output)],
            capture_output=True, text=True, check=True, timeout=20,
        )
        metadata = json.loads(probe.stdout)
        duration = float(metadata["format"]["duration"])
        streams = metadata["streams"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as e:
        raise ContentQualityError("완성 영상 메타데이터를 확인할 수 없다") from e
    if not math.isfinite(duration) or not 0 < duration < 60:
        raise ContentQualityError(f"완성 영상 길이가 1분 미만 범위를 벗어났다: {duration}")
    if not any(s.get("codec_type") == "video" and s.get("width") == 1080 and s.get("height") == 1920
               for s in streams):
        raise ContentQualityError("완성 영상의 세로 해상도가 1080x1920이 아니다")
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise ContentQualityError("완성 영상에 오디오 스트림이 없다")
