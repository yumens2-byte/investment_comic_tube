"""CAST-LINE 비트별 한국어 내레이션 TTS — DN2026_0115 P2 미디어 레이어.

- 합성은 src.tts.synthesize_narrations 재사용(무수정, import만). 반환 (paths, reason).
- reason 이 None 이 아니거나 경로에 None 이 있으면 NarrationError(fail-closed, 부분 내레이션 발행 금지).
- DN2026_0116: 전 비트 '빠르고 또렷한 속도' 지시 → 앞뒤 무음 제거 → ffprobe 실측
  → 10초 컷 기준 1.10배 넘게 압축해야 하는 비트만 '아주 빠르게' 1회 재합성(짧아질 때만 교체)
  → fit_cuts(컷 5~10초, tempo ≤1.25, 합성 ≤58초) 위반이면 NarrationError(fal 호출 전).
  tempo_for 는 DN2026_0115 호환용(미사용).
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
# DN2026_0116: 컷 길이를 내레이션 실측에 맞춘다(fal duration 5~15 정수, 공식 문서)
CUT_MIN_SEC = 5
CUT_MAX_SEC = 10
CUT_TAIL_SEC = 0.6        # 내레이션 끝 뒤 여유
FIT_MAX_TEMPO = 1.25      # 10초 컷에 넣기 위한 최대 압축(atempo, 음높이 유지) — DN2026_0116 shadow 44139523787 실측(초당 2.0~3.4자)으로 1.10→1.25
SILENCE_DB = "-45dB"      # 앞뒤 무음 판정 임계 — DN2026_0116
SILENCE_KEEP_HEAD = 0.08  # 앞 무음 남김(초)
SILENCE_KEEP_TAIL = 0.12  # 뒤 무음 남김(초)
MIN_TRIMMED_SEC = 0.3     # 이보다 짧아지면 무음 제거 결과를 버리고 원본 사용
TOTAL_MIN_SEC = 16        # 합성 하한 15초 + 1초
TOTAL_MAX_SEC = 58        # Shorts 60초 이내(합성 상한 60초 - 2초)
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


PACE_PREFIX = "빠르고 또렷한 속도의 "          # DN2026_0116 D안: shadow 실측 초당 2.0~3.1자 → 전 비트 속도 지시
RETRY_PACE_PREFIX = "아주 빠르고 또렷한 속도의 "  # DN2026_0116 D안: 과길이 비트 1회 재합성
DEFAULT_ROLE_TONE = "자연스러운 톤"
RETRY_TEMPO = 1.10        # 10초 컷 기준 이 배율을 넘게 압축해야 하는 비트는 1회 재합성


def tone_for(role: str | None, retry: bool = False) -> str:
    """역할 → 낭독 지시(속도 + 톤). 미정의 역할은 자연스러운 톤. # DN2026_0115 → DN2026_0116"""
    return (RETRY_PACE_PREFIX if retry else PACE_PREFIX) + ROLE_TONE.get(role or "", DEFAULT_ROLE_TONE)


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


def trim_silence(wav: str) -> str:
    """DN2026_0116 — TTS wav 앞뒤 무음 제거 → <stem>_trim.wav 경로. 실패·과도 절삭이면 원본 경로(fail-open, 원본 길이로 판정)."""
    out = os.path.splitext(wav)[0] + "_trim.wav"
    af = (f"silenceremove=start_periods=1:start_threshold={SILENCE_DB}:start_silence={SILENCE_KEEP_HEAD},"
          f"areverse,silenceremove=start_periods=1:start_threshold={SILENCE_DB}:start_silence={SILENCE_KEEP_TAIL},"
          "areverse")
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", wav, "-af", af, out],
                       check=True, capture_output=True, timeout=PROBE_TIMEOUT_SEC)
        if _probe(out) < MIN_TRIMMED_SEC:
            logger.warning("silence_trim_rejected wav=%s", os.path.basename(wav))
            return wav
        return out
    except (subprocess.SubprocessError, OSError, NarrationError) as e:
        logger.warning("silence_trim_failed wav=%s err=%s", os.path.basename(wav), type(e).__name__)
        return wav


def _composed_total(secs: list[float], tempos: list[float]) -> float:
    """compose._narration_plan 과 같은 식의 합성 길이 추정: Σ max(min_cut, sec/tempo + 0.6)."""
    min_cut = max(CUT_MIN_SEC, TOTAL_MIN_SEC / max(1, len(secs)))
    return sum(max(min_cut, s / t + CUT_TAIL_SEC) for s, t in zip(secs, tempos))


def fit_cuts(secs: list[float]) -> list[dict]:
    """DN2026_0116 — 내레이션 실측 길이 → 컷별 {cut_sec(정수), tempo}.

    1) 컷 tempo = max(1.0, sec/9.4) — 10초 컷에 들어가도록. FIT_MAX_TEMPO(1.25) 초과 비트는 오류.
    2) 합성 길이(Σ max(min_cut, sec/tempo+0.6))가 58초를 넘으면 전 컷 공통 tempo(≤FIT_MAX_TEMPO)를
       이분 탐색으로 올려 58초 이내로 맞춘다(R6-03 치명: 컷 올림 누적으로 61초 차단되던 문제).
       상한으로도 넘으면 오류(fal 호출 전).
    3) fal 컷 길이 = max(5, ceil(sec/tempo+0.6)) ≤ 10. 합계 16초 미만이면 짧은 컷부터 1초씩 늘린다.
    오류 메시지에 전 비트 실측 길이를 남긴다.
    """
    import math
    summary = ",".join(f"{s:.2f}" for s in secs)
    base = [max(1.0, s / (CUT_MAX_SEC - CUT_TAIL_SEC)) for s in secs]
    over = [i for i, t in enumerate(base) if t > FIT_MAX_TEMPO + 1e-9]
    if over:
        raise NarrationError(f"narration_too_long beats={over} secs=[{summary}]")
    floor = 1.0
    if _composed_total(secs, base) > TOTAL_MAX_SEC:
        if _composed_total(secs, [max(t, FIT_MAX_TEMPO) for t in base]) > TOTAL_MAX_SEC:
            raise NarrationError(
                f"narration_total_too_long est={_composed_total(secs, base):.2f}s secs=[{summary}]")
        lo, hi = 1.0, FIT_MAX_TEMPO
        for _ in range(40):
            mid = (lo + hi) / 2
            if _composed_total(secs, [max(t, mid) for t in base]) > TOTAL_MAX_SEC:
                lo = mid
            else:
                hi = mid
        floor = math.ceil(hi * 10000) / 10000
    tempos = [round(max(t, floor), 4) for t in base]
    fitted = [{"cut_sec": min(CUT_MAX_SEC, max(CUT_MIN_SEC, math.ceil(s / t + CUT_TAIL_SEC - 1e-9))),
               "tempo": t} for s, t in zip(secs, tempos)]
    while sum(f["cut_sec"] for f in fitted) < TOTAL_MIN_SEC:
        short = min(range(len(fitted)), key=lambda k: fitted[k]["cut_sec"])
        if fitted[short]["cut_sec"] >= CUT_MAX_SEC:
            break
        fitted[short]["cut_sec"] += 1
    return fitted


def synthesize(beats: list[dict], out_dir: str = "artifacts_cast/audio", *,
               model: str | None = None, voice: str | None = None) -> list[dict]:
    """비트별 내레이션 합성 → [{cut, wav, sec, tempo, cut_sec}]. # DN2026_0115 → DN2026_0116(컷 맞춤)"""
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
    logger.info("narration_start beats=%d model=%s voice=%s", len(beats), model, voice)

    paths, reason = _tts_fn()(texts, output_dir=out_dir, tones=tones,
                              voice_name=voice, model_name=model)
    if reason is not None or len(paths) != len(beats) or any(p is None for p in paths):
        ok = sum(1 for p in paths if p)
        raise NarrationError(f"tts_failed reason={reason} ok={ok}/{len(beats)}")

    raw = [_probe(wav) for wav in paths]
    paths = [trim_silence(wav) for wav in paths]   # DN2026_0116: 앞뒤 무음 제거 후 실측
    secs = [_probe(wav) for wav in paths]
    for b, r, sec, text in zip(beats, raw, secs, texts):
        logger.info("narration_measured cut=%s raw=%.2f sec=%.2f chars=%d", b["cut"], r, sec, len(text))
    # DN2026_0116 D안: 10초 컷에 RETRY_TEMPO 배 넘게 압축해야 하는 비트만 '아주 빠르게'로 1회 재합성.
    # 재합성이 실패하거나 더 길면 원본 유지(fail-open) → fit_cuts 가 최종 판정(fail-closed).
    slow = [i for i, s in enumerate(secs) if s / (CUT_MAX_SEC - CUT_TAIL_SEC) > RETRY_TEMPO]
    if slow:
        rpaths, rreason = _tts_fn()([texts[i] for i in slow], output_dir=os.path.join(out_dir, "retry"),
                                    tones=[tone_for(beats[i].get("role"), retry=True) for i in slow],
                                    voice_name=voice, model_name=model)
        for k, i in enumerate(slow):
            rp = rpaths[k] if rreason is None and k < len(rpaths) else None
            if not rp:
                logger.warning("narration_retry_failed cut=%s reason=%s", beats[i]["cut"], rreason)
                continue
            try:
                rp = trim_silence(rp)
                rsec = _probe(rp)
            except NarrationError:
                logger.warning("narration_retry_probe_failed cut=%s", beats[i]["cut"])
                continue
            logger.info("narration_retry cut=%s before=%.2f after=%.2f", beats[i]["cut"], secs[i], rsec)
            if rsec < secs[i]:
                paths[i], secs[i] = rp, rsec
    fitted = fit_cuts(secs)
    out: list[dict] = []
    for b, wav, sec, f in zip(beats, paths, secs, fitted):
        out.append({"cut": int(b["cut"]), "wav": str(wav), "sec": round(sec, 3),
                    "tempo": f["tempo"], "cut_sec": f["cut_sec"]})
    logger.info("narration_fitted cuts=%s total=%ds", [o["cut_sec"] for o in out], sum(o["cut_sec"] for o in out))
    return out
