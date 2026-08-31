"""Gemini(Imagen) 기반 정적 이미지 생성.

API 미설정/실패 시 빈 리스트를 반환한다 (renderer가 텍스트카드로 자동 폴백).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_MODEL = "imagen-4.0-generate-001"


def generate_scene_images(script_data: dict, output_dir: str = "artifacts/images", count: int = 2) -> list[str]:
    """villain/theme 기반 장면 이미지를 생성해 로컬에 저장하고 경로 목록을 반환한다."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("image_generation_skipped reason=no_api_key")
        return []

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.warning("image_generation_skipped reason=google_genai_not_installed")
        return []

    villain = script_data.get("villain", "Unknown")
    theme = script_data.get("theme", "")
    prompt = (
        "Vertical 9:16 comic-style illustration for a financial-market story called "
        f"'EDT Universe'. A heroic trader character confronting the antagonist "
        f"'{villain}'. Theme: {theme}. Dramatic market battle scene. No text, no logos, "
        "no watermarks."
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_images(
            model=IMAGE_MODEL,
            prompt=prompt,
            config=types.GenerateImagesConfig(number_of_images=count, output_mime_type="image/jpeg"),
        )
    except Exception as e:  # noqa: BLE001 - 외부 API 실패는 렌더링 폴백으로 흡수
        logger.warning("image_generation_failed reason=%s: %s", type(e).__name__, e)
        return []

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for idx, generated in enumerate(response.generated_images or []):
        image_bytes = getattr(generated.image, "image_bytes", None)
        if not image_bytes:
            continue
        path = out_dir / f"scene_{idx}.jpg"
        path.write_bytes(image_bytes)
        paths.append(str(path))

    logger.info("image_generation_finished count=%s", len(paths))
    return paths
