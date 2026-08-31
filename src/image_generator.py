"""Gemini 기반 정적 이미지 생성.

Imagen(`generate_images`)은 Developer API(API 키) 모드에서 사용할 수 없고
2026-08-17자로 종료됐다. 이미지 생성은 `generate_content` + 이미지 지원 모델로
수행하며, 응답은 parts 안의 inline_data(바이트)로 돌아온다.

API 미설정/실패 시 (빈 리스트, 사유) 를 반환한다.
renderer는 빈 리스트를 받으면 텍스트카드로 자동 폴백하고,
사유는 main에서 episodes.degraded_reason 으로 기록된다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_MODEL = "gemini-3.1-flash-image"


def _build_prompt(script_data: dict) -> str:
    villain = script_data.get("villain", "Unknown")
    theme = script_data.get("theme", "")
    return (
        "Vertical 9:16 comic-style illustration for a financial-market story called "
        f"'EDT Universe'. A heroic trader character confronting the antagonist "
        f"'{villain}'. Theme: {theme}. Dramatic market battle scene. "
        "No text, no logos, no watermarks."
    )


def _extract_image_bytes(response) -> list[bytes]:
    """generate_content 응답의 parts 에서 이미지 바이트만 뽑아낸다."""
    images: list[bytes] = []
    parts = getattr(response, "parts", None) or []
    for part in parts:
        inline = getattr(part, "inline_data", None)
        data = getattr(inline, "data", None) if inline is not None else None
        if data:
            images.append(data)
    return images


def generate_scene_images(
    script_data: dict,
    output_dir: str = "artifacts/images",
    count: int = 2,
) -> tuple[list[str], str | None]:
    """장면 이미지를 생성해 로컬에 저장하고 (경로 목록, 실패 사유) 를 반환한다.

    실패 사유가 None 이면 정상 생성된 것이다.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("image_generation_skipped reason=no_api_key")
        return [], "image:no_api_key"

    try:
        from google import genai
    except ImportError:
        logger.warning("image_generation_skipped reason=google_genai_not_installed")
        return [], "image:google_genai_not_installed"

    prompt = _build_prompt(script_data)
    client = genai.Client(api_key=api_key)

    collected: list[bytes] = []
    last_error: str | None = None
    for attempt in range(count):
        try:
            response = client.models.generate_content(model=IMAGE_MODEL, contents=prompt)
        except Exception as e:  # noqa: BLE001 - 외부 API 실패는 렌더링 폴백으로 흡수
            last_error = f"{type(e).__name__}: {e}"
            logger.warning("image_generation_call_failed attempt=%s reason=%s", attempt + 1, last_error)
            break

        image_bytes = _extract_image_bytes(response)
        if not image_bytes:
            last_error = "no_inline_image_in_response"
            logger.warning("image_generation_empty attempt=%s", attempt + 1)
            break
        collected.extend(image_bytes)

    if not collected:
        reason = f"image:{last_error or 'unknown'}"
        logger.warning("image_generation_failed reason=%s", reason)
        return [], reason

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for idx, data in enumerate(collected[:count]):
        path = out_dir / f"scene_{idx}.png"
        path.write_bytes(data)
        paths.append(str(path))

    logger.info("image_generation_finished count=%s model=%s", len(paths), IMAGE_MODEL)
    return paths, None
