# DN2026_0123
"""CAST 장면 키프레임 — 컷별 첫 프레임 이미지 생성·업로드. # DN2026_0123 D7·D8

흐름(컷 1개): 화면 히어로 REF 파일(저장소 assets/refs, 최대 4장) + 캐논 외형 + 장면 설명
  → gemini-3.1-flash-image (9:16, 1K = 768x1376) → 크기 검증 → fal_client.upload_file(만료 1일)
  → fal i2v 의 image_url(첫 프레임).

- 근거: fal H3 i2v 는 이미지가 없으면 16:9 text-to-video 로 처리하고, 출력 비율은 입력 이미지를
  따른다(공식 llms.txt). 그래서 빌런 단독 컷도 9:16 키프레임을 준다.
- 실패 정책(회장 승인 11-23): 1회 재시도 후에도 실패하면 None — 호출부가 히어로 컷은 REF 원본,
  빌런 단독 컷은 현행(이미지 없음)으로 진행하고 경고를 남긴다.
- 일간 라인의 src.image_generator 는 재사용하지 않는다(EDT 전용 프롬프트·참조 디렉터리 고정).
- 비용: 장당 $0.067(1K, ai.google.dev/pricing). 호출부가 원장·상한에 합산한다.
"""
from __future__ import annotations

import io
import logging
import os

logger = logging.getLogger("cast.keyframe")

KEYFRAME_MODEL = os.environ.get("CAST_KEYFRAME_MODEL", "gemini-3.1-flash-image")
KEYFRAME_PRICE_USD = 0.067        # 1K 장당(공식 가격표, DN2026_0123 D8)
KEYFRAME_SIZE = (768, 1376)       # 9:16 1K(공식 해상도 표)
MAX_ATTEMPTS = 2                  # 최초 1회 + 재시도 1회
MAX_REFS = 4                      # 캐릭터 참조 최대 4장(공식 문서)
UPLOAD_EXPIRES_IN = "1d"          # fal CDN 업로드 만료(fal_client.StorageSettings)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REF_DIR = os.environ.get("CAST_REF_DIR") or os.path.join(_REPO_ROOT, "assets", "refs")
REF_FILES = {"EDT": "ref_edt_rcl07b.png", "LEV-01": "ref_lev01.png", "FG-01": "ref_fg01_v3.png"}
HEROES = tuple(REF_FILES)

IMAGE_HEAD = ("Single vertical 9:16 comic-book cinematic frame, the opening frame of a short video shot. "
              "Keep every character exactly as in the reference images (face, colors, costume). ")
IMAGE_TAIL = " No text, no letters, no numbers, no logos, no watermark, no captions, no speech bubbles."


class KeyframeError(RuntimeError):
    """키프레임 1장 실패(재시도 후). 호출부가 폴백한다."""


def enabled() -> bool:
    """비상 정지 스위치 CAST_KEYFRAME_ENABLED(미등록·빈 값 = 켜짐, 'false' 만 끔)."""
    return (os.environ.get("CAST_KEYFRAME_ENABLED") or "true").strip().lower() != "false"


def ref_paths(characters: list[str]) -> list[str]:
    paths = []
    for c in characters or []:
        name = REF_FILES.get(c)
        if name:
            path = os.path.join(REF_DIR, name)
            if not os.path.exists(path):
                raise KeyframeError(f"REF 파일 없음 {c}")
            paths.append(path)
    return paths[:MAX_REFS]


def build_prompt(characters: list[str], situation_en: str, canon: dict, axis: str | None) -> str:
    """캐논 외형 블록(+빌런 공격 형태) + 장면 설명. 영상용 '1액션 1카메라무브' 꼬리는 쓰지 않는다."""
    from .generate import PROMPT_MAX_CHARS, _video_canon_chars
    situation = (situation_en or "").strip()
    if not situation:
        raise KeyframeError("situation_en 비어 있음")
    chars = _video_canon_chars(canon)
    blocks, negatives = [], []
    for name in characters or []:
        entry = chars.get(name) if isinstance(chars.get(name), dict) else {}
        text = (entry.get("prompt_en") or name).strip().rstrip(".")
        attack = (entry.get("attack_forms_en") or {}).get(axis or "") if name not in HEROES else None
        blocks.append(f"{text}, {attack.strip().rstrip('.')}." if attack else f"{text}.")
        if entry.get("negative_en"):
            negatives.append(entry["negative_en"].strip().rstrip("."))
    prompt = f"{IMAGE_HEAD}{' '.join(blocks)} {situation}{IMAGE_TAIL}"
    if negatives:
        prompt += " Strictly: " + "; ".join(negatives) + "."
    if len(prompt) > PROMPT_MAX_CHARS:
        raise KeyframeError(f"prompt_too_long chars={len(prompt)}")
    return prompt


def _make_client(api_key: str):
    from google import genai  # 지연 import — dry_run·테스트 미요구
    return genai.Client(api_key=api_key)


def _generate_image(client, prompt: str, refs: list[str]) -> bytes:
    from google.genai import types
    contents: list = []
    for path in refs:
        with open(path, "rb") as f:
            contents.append(types.Part.from_bytes(data=f.read(), mime_type="image/png"))
    contents.append(prompt)
    resp = client.models.generate_content(
        model=KEYFRAME_MODEL, contents=contents,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="9:16", image_size="1K")))
    for part in getattr(resp, "parts", None) or []:
        data = getattr(getattr(part, "inline_data", None), "data", None)
        if data:
            return data
    raise KeyframeError("no_inline_image")


def _check_size(data: bytes) -> tuple[int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        size = im.size
    # 9:16 세로 비율만 강제(정확 해상도는 로그로 관찰) — 가로 이미지는 i2v 를 가로로 만든다
    if not (size[1] > size[0] and abs(size[0] / size[1] - 9 / 16) < 0.02):
        raise KeyframeError(f"keyframe_not_vertical {size[0]}x{size[1]}")
    return size


def _upload(path: str, fal_key: str) -> str:
    import fal_client
    client = fal_client.SyncClient(key=fal_key)
    return client.upload_file(path, lifecycle=fal_client.StorageSettings(expires_in=UPLOAD_EXPIRES_IN))


def make_keyframe(cut: dict, situation_en: str, canon: dict, axis: str | None, out_dir: str,
                  *, client, fal_key: str, on_attempt=None) -> dict:
    """컷 1장 생성·업로드. 반환 {cut, url, path, size, attempts}. 재시도 후 실패면 KeyframeError.

    on_attempt(): 각 이미지 호출 직전(비용 가드·원장 가산). 예외 시 호출하지 않는다."""
    prompt = build_prompt(cut["characters"], situation_en, canon, axis)
    refs = ref_paths(cut["characters"])
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if on_attempt is not None:
            on_attempt()
        try:
            data = _generate_image(client, prompt, refs)
            size = _check_size(data)
        except KeyframeError as e:
            last = e
        except Exception as e:  # noqa: BLE001 — 외부 API 실패는 사유 유형만
            last = KeyframeError(type(e).__name__)
        else:
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"keyframe_{int(cut['cut']):02d}.png")
            with open(path, "wb") as f:
                f.write(data)
            try:
                url = _upload(path, fal_key)
            except Exception as e:
                raise KeyframeError(f"upload_failed {type(e).__name__}") from e
            logger.info("keyframe_ok cut=%s size=%dx%d refs=%d attempt=%d",
                        cut["cut"], size[0], size[1], len(refs), attempt)
            return {"cut": int(cut["cut"]), "url": url, "path": path, "size": list(size),
                    "attempts": attempt}
        logger.warning("keyframe_retry cut=%s attempt=%d reason=%s", cut["cut"], attempt, last)
    raise KeyframeError(f"keyframe_failed cut={cut['cut']} reason={last}")
