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

from src.characters import HERO_APPEARANCE, get_villain_appearance

logger = logging.getLogger(__name__)

IMAGE_MODEL = "gemini-3.1-flash-image"


def _build_prompt(script_data: dict, scene: str | None = None) -> str:
    villain = script_data.get("villain", "Unknown")
    theme = script_data.get("theme", "")
    scene_line = (
        f"Scene to depict: {scene} "
        if scene
        else f"The tiger hero confronts {villain} in a dramatic market battle scene. "
    )
    return (
        "Vertical 9:16 comic-style illustration for a financial-market story "
        "called 'EDT Universe'. "
        f"{HERO_APPEARANCE} "
        f"{get_villain_appearance(villain)} "
        f"{scene_line}"
        f"Theme: {theme}. "
        "Keep the art style, character designs and colour palette perfectly consistent "
        "with the described characters across every scene. "
        "CRITICAL: the image must contain absolutely NO text, NO letters, NO words, "
        "NO numbers, NO captions, NO speech bubbles, NO signage, NO watermarks and "
        "NO logos of any kind, in any language. Purely visual illustration only. "
        "Leave the bottom third of the image visually simple, with no important "
        "subject matter, so a caption can be overlaid there later."
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
    scenes: list[str] | None = None,
    count: int = 2,
) -> tuple[list[str | None], str | None]:
    """비트별 장면 이미지를 생성해 (경로 목록, 실패 사유) 를 반환한다.

    scenes 가 주어지면 각 장면 지시문마다 이미지를 1장씩 생성한다.
    실패한 장면은 목록에서 None 이며, renderer 가 해당 장면을 건너뛴다.
    사유가 None 이면 전부 정상 생성된 것이다.
    """
    scene_list: list[str | None] = list(scenes) if scenes else [None] * count
    total = len(scene_list)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("image_generation_skipped reason=no_api_key")
        return [None] * total, "image:no_api_key"

    try:
        from google import genai
    except ImportError:
        logger.warning("image_generation_skipped reason=google_genai_not_installed")
        return [None] * total, "image:google_genai_not_installed"

    client = genai.Client(api_key=api_key)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: list[str | None] = []
    last_error: str | None = None

    for idx, scene in enumerate(scene_list):
        prompt = _build_prompt(script_data, scene)
        try:
            response = client.models.generate_content(model=IMAGE_MODEL, contents=prompt)
            image_bytes = _extract_image_bytes(response)
        except Exception as e:  # noqa: BLE001 - 외부 API 실패는 렌더링 폴백으로 흡수
            last_error = f"{type(e).__name__}"
            logger.warning("image_generation_call_failed index=%s reason=%s: %s", idx, last_error, e)
            paths.append(None)
            continue

        if not image_bytes:
            last_error = "no_inline_image_in_response"
            logger.warning("image_generation_empty index=%s", idx)
            paths.append(None)
            continue

        path = out_dir / f"scene_{idx}.png"
        path.write_bytes(image_bytes[0])
        paths.append(str(path))

    ok_count = sum(1 for p in paths if p)
    logger.info("image_generation_finished count=%s total=%s model=%s", ok_count, total, IMAGE_MODEL)

    if ok_count == 0:
        return paths, f"image:{last_error or 'unknown'}"
    if ok_count < total:
        return paths, f"image:partial_{ok_count}of{total}"
    return paths, None
