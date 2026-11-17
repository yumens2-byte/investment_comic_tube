"""CAST-LINE 비트별 한국어 내레이션 TTS — DN2026_0115 P2 미디어 레이어.

- 합성은 src.tts.synthesize_narrations 재사용(무수정, import만). 반환 (paths, reason).
- reason 이 None 이 아니거나 경로에 None 이 있으면 NarrationError(fail-closed, 부분 내레이션 발행 금지).
- 각 wav 길이는 ffprobe(timeout 30)로 측정, 컷 예산(budget_sec) 대비 tempo 산정.
  tempo = 1.0 (sec ≤ budget-0.3) / sec÷(budget-0.3) 그 외, 1.15 초과면 NarrationError.
- 키·외부 응답 본문은 로그·예외 메시지에 남기지 않는다(reason 코드만).
"""
from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger("cast.narration")

DEFAULT_TTS_MODEL = "gemini-3.8-flash-tts"   # DN2026_0115 — env CAST_TTS_MODEL 로 덮어씀
DEFAULT_TTS_VOICE = "Charon"
HEADROOM_SEC = 0.3        # 컷 예산 대비 여유 # DN2026_0115
MAX_TEMPO = 1.15          # 이보다 빨리 돌려야 하면 대본 길이 초과로 본다 # DN2026_0115
PROBE_TIMEOUT_SEC = 30

# DN2026_0115 — 역할별 낭독 톤(src.tts 가 "다음 대사를 {tone}으로 낭독해라" 로 감싼다)
ROLE_TONE = {
    "ESTABLISH_THREAT": "긴박한 톤",
    "ESCALATE": "점점 고조되는 톤",
    "ENGAGE": "단호한 톤",
    "CRISIS": "절박한 톤",
    "TURN": "반격하듯 힘찬 톤",
    "RESOLVE": "차분한 톤",
    "NEXT_OMEN": "낮고 불길한 톤",
}


class NarrationError(RuntimeError):
    """내레이션 합성·길이 검증 실패 — fal 제출 전에 중단한다."""


def tone_for(role: str | None) -> str | None:
    """역할 → 톤. 미정의 역할은 None(src.tts DEFAULT_TONE 사용)."""
    return ROLE_TONE.get(role or "")


def tempo_for(sec: float, budget_sec: float) -> float:
    """DN2026_0115 tempo 산정. 1.15 초과는 호출부가 오류로 처리."""
    limit = budget_sec - HEADROOM_SEC
    if limit <= 0:
        raise NarrationError(f"budget_invalid budget_sec={budget_sec}")
    if sec <= limit:
        return 1.0
    return round(sec / limit, 4)


def _probe(path: str) -> float:
    """wav 길이(초) — ffprobe. 실패는 NarrationError."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], capture_output=True, text=True,
                           timeout=PROBE_TIMEOUT_SEC, check=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise NarrationError(f"ffprobe 실행 실패({type(e).__name__})") from e
    if r.returncode != 0:
        raise NarrationError(f"ffprobe 실패 — {os.path.basename(path)}")
    try:
        sec = float(r.stdout.strip())
    except ValueError as e:
        raise NarrationError(f"ffprobe 길이 해석 실패 — {os.path.basename(path)}") from e
    if sec <= 0:
        raise NarrationError(f"내레이션 길이 0 — {os.path.basename(path)}")
    return sec


def _tts_fn():
    from src import (
        tts,  # 지연 import — 테스트는 src.tts.synthesize_narrations 를 monkeypatch
    )
    return tts.synthesize_narrations


def plan_only(beats: list[dict]) -> list[dict]:
    """dry_run 용 계획 — 문장·글자수만(외부 호출 0). # DN2026_0115"""
    return [{"cut": int(b["cut"]), "role": b.get("role"), "text": b["narration"],
             "chars": len(b["narration"])} for b in beats]


def synthesize(beats: list[dict], out_dir: str = "artifacts_cast/audio", *,
               model: str | None = None, voice: str | None = None,
               budget_sec: float) -> list[dict]:
    """비트별 내레이션 합성 → [{cut, wav, sec, tempo}]. # DN2026_0115"""
    if not beats:
        raise NarrationError("beats 없음")
    texts = []
    for b in beats:
        text = (b.get("narration") or "").strip()
        if not text:
            raise NarrationError(f"narration_empty cut={b.get('cut')}")
        texts.append(text)
    model = model or os.environ.get("CAST_TTS_MODEL") or DEFAULT_TTS_MODEL
    voice = voice or os.environ.get("TTS_VOICE") or DEFAULT_TTS_VOICE
    tones = [tone_for(b.get("role")) for b in beats]
    logger.info("narration_start beats=%d model=%s voice=%s budget=%.1fs", len(beats), model, voice, budget_sec)

    paths, reason = _tts_fn()(texts, output_dir=out_dir, tones=tones,
                              voice_name=voice, model_name=model)
    if reason is not None or len(paths) != len(beats) or any(p is None for p in paths):
        ok = sum(1 for p in paths if p)
        raise NarrationError(f"tts_failed reason={reason} ok={ok}/{len(beats)}")

    out: list[dict] = []
    for b, wav in zip(beats, paths):
        sec = _probe(wav)
        tempo = tempo_for(sec, budget_sec)
        if tempo > MAX_TEMPO:
            raise NarrationError(f"narration_too_long cut={b['cut']} sec={sec:.2f} budget={budget_sec}")
        out.append({"cut": int(b["cut"]), "wav": str(wav), "sec": round(sec, 3), "tempo": tempo})
        logger.info("narration_cut cut=%s sec=%.2f tempo=%.3f", b["cut"], sec, tempo)
    return out
