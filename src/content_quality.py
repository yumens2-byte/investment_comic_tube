"""Pre-publication checks for the six-beat EDT Shorts media package."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageStat, UnidentifiedImageError

from src.validation import ValidationError

VERSION = "1.3.0"
EXPECTED_IMAGE_SLOTS = 4
EXPECTED_NARRATIONS = 6
# 운영 발행 최소 길이. 6비트 렌더 하한은 훅 3.0초 + 본문 5×3.5초 + 아웃트로 2.0초 = 22.5초이며
# 폴백 대본(Ep.31·32)이 이 하한 근처로 렌더됐다. 정상 회차(25~45자 문장)는 이보다 길다.
MIN_PUBLISH_DURATION_SEC = 25.0


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


def validate_rendered_video(path: str, *, min_seconds: float = 0.0) -> None:
    """최종 인코딩 결과를 업로드 전에 검사한다.

    min_seconds 는 운영 발행 경로에서만 지정한다. 저비용 미리보기(1장면)는 기본값 0을 쓴다.
    """
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
    if duration < min_seconds:
        raise ContentQualityError(f"완성 영상이 최소 길이 {min_seconds:.1f}초보다 짧다: {duration:.2f}")
    if not any(s.get("codec_type") == "video" and s.get("width") == 1080 and s.get("height") == 1920
               for s in streams):
        raise ContentQualityError("완성 영상의 세로 해상도가 1080x1920이 아니다")
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise ContentQualityError("완성 영상에 오디오 스트림이 없다")
