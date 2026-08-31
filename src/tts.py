"""Gemini TTS 기반 한국어 내레이션 음성 합성.

TTS 모델은 오디오 전용 출력이며, 응답은 24kHz / mono / 16-bit PCM 원시 바이트다.
그대로는 재생/합성이 안 되므로 WAV 헤더를 씌워 저장한다.

API 미설정/실패 시 (None, 사유) 를 반환한다. renderer는 오디오가 없으면
고정 길이 무음 장면으로 폴백하므로 파이프라인은 죽지 않는다.
"""

from __future__ import annotations

import logging
import os
import wave
from pathlib import Path

from src.quota import is_quota_exhausted

logger = logging.getLogger(__name__)

TTS_MODEL = "gemini-3.1-flash-tts-preview"
TTS_VOICE = os.getenv("TTS_VOICE", "Charon")

PCM_SAMPLE_RATE = 24000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH = 2  # 16-bit


def _write_wave(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(PCM_CHANNELS)
        wf.setsampwidth(PCM_SAMPLE_WIDTH)
        wf.setframerate(PCM_SAMPLE_RATE)
        wf.writeframes(pcm)


def _extract_pcm(response) -> bytes | None:
    """TTS 응답에서 PCM 바이트를 뽑아낸다."""
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline is not None else None
            if data:
                return data
    return None


def synthesize_narrations(
    narrations: list[str],
    output_dir: str = "artifacts/audio",
) -> tuple[list[str | None], str | None]:
    """내레이션 문장들을 음성 파일로 합성한다.

    반환: (문장별 wav 경로 목록(실패한 항목은 None), 폴백 사유)
    사유가 None 이면 전부 정상 합성된 것이다.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("tts_skipped reason=no_api_key")
        return [None] * len(narrations), "tts:no_api_key"

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.warning("tts_skipped reason=google_genai_not_installed")
        return [None] * len(narrations), "tts:google_genai_not_installed"

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=TTS_VOICE)
            )
        ),
    )

    paths: list[str | None] = []
    last_error: str | None = None

    for idx, line in enumerate(narrations):
        # 스타일 지시문이 그대로 낭독되지 않도록 낭독 구간을 명확히 분리한다
        prompt = (
            "다음 대사를 긴장감 있고 힘 있는 톤으로 또박또박 낭독해라. "
            f"낭독할 대사: {line}"
        )
        try:
            response = client.models.generate_content(
                model=TTS_MODEL, contents=prompt, config=config
            )
            pcm = _extract_pcm(response)
        except Exception as e:  # noqa: BLE001 - 외부 API 실패는 무음 장면으로 폴백
            last_error = f"{type(e).__name__}"
            logger.warning("tts_call_failed index=%s reason=%s: %s", idx, last_error, e)
            paths.append(None)
            if is_quota_exhausted(e):
                last_error = "quota_exhausted"
                remaining = len(narrations) - idx - 1
                logger.warning("tts_aborted reason=quota_exhausted remaining=%s", remaining)
                paths.extend([None] * remaining)
                break
            continue

        if not pcm:
            last_error = "no_audio_in_response"
            logger.warning("tts_empty index=%s", idx)
            paths.append(None)
            continue

        path = out_dir / f"narration_{idx}.wav"
        _write_wave(path, pcm)
        paths.append(str(path))

    ok_count = sum(1 for p in paths if p)
    logger.info("tts_finished ok=%s total=%s model=%s", ok_count, len(narrations), TTS_MODEL)

    if ok_count == 0:
        return paths, f"tts:{last_error or 'unknown'}"
    if ok_count < len(narrations):
        return paths, f"tts:partial_{ok_count}of{len(narrations)}"
    return paths, None
