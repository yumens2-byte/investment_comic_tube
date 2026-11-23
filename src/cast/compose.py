"""CAST-LINE 전용 합성 — fal i2v 컷 mp4 N개 → 1080x1920 Shorts (DN2026_0114).

src.renderer.render_video 는 정지 이미지 입력 전제(caption 필수, `-loop 1`)라
mp4 컷을 넘기면 실패한다. CAST는 i2v 모션을 유지한 채 정규화·연결·BGM 믹스만 한다.
renderer 는 _find_bgm 만 읽기 전용으로 재사용한다(무수정).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

try:
    from src.cast import VERSION
except ImportError:
    VERSION = "1.1.0"

logger = logging.getLogger("cast.compose")

# DN2026_0114 합성 규격
TARGET_W, TARGET_H, TARGET_FPS = 1080, 1920, 30
MIN_TOTAL_SEC, MAX_TOTAL_SEC = 15.0, 60.0
FADE_OUT_SEC = 1.5
THUMB_AT_SEC = 1.5
CLIP_TIMEOUT_SEC = 180
MUX_TIMEOUT_SEC = 300
PROBE_TIMEOUT_SEC = 30
# DN2026_0115 내레이션 믹스 규격
NARR_MIN_CUT_SEC = 5.0       # 내레이션 있을 때 컷 최소 길이
NARR_TAIL_SEC = 0.6          # 내레이션 끝 이후 여유
MIN_TOTAL_WITH_MARGIN = 16.0  # R6-05 #3: 합성 하한(15초)보다 1초 여유
BGM_UNDER_NARR_VOLUME = 0.15
VIDEO_FILTER = (
    f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
    f"crop={TARGET_W}:{TARGET_H},fps={TARGET_FPS},setsar=1,format=yuv420p"
)


class ComposeError(RuntimeError):
    """합성 실패·규격 위반 — 발행하지 않는다."""


def _run(cmd: list[str], timeout: int, what: str) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ComposeError(f"{what} timeout {timeout}s") from e
    except FileNotFoundError as e:
        raise ComposeError(f"{what} 실행 불가: {e.filename}") from e
    if proc.returncode != 0:
        raise ComposeError(f"{what} 실패 rc={proc.returncode}: {(proc.stderr or '')[-300:]}")
    return proc


def _probe(path: str) -> dict:
    proc = _run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height", "-of", "json", path],
        PROBE_TIMEOUT_SEC, f"ffprobe {os.path.basename(path)}",
    )
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        raise ComposeError(f"ffprobe 출력 해석 실패: {path}") from e


def _duration(info: dict) -> float:
    try:
        return float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        return 0.0


# DN2026_0122: 영상 캐논 빌런은 전용 BGM이 없어 같은 축의 기존 곡을 쓴다(renderer 무수정, assets/bgm 실재)
BGM_AXIS_ALIAS = {"Volatility Hydra": "Chaos Reaper", "Algorithm Reaper": "Bull Brute"}


def _caption_filter(text: str, villain: str | None, market_snapshot: dict | None,
                    caption_file: Path) -> str:
    """DN2026_0115 — src.caption_layout(무수정) 레이아웃 → drawtext 체인. LayoutError → ComposeError."""
    from src.caption_layout import LayoutError, build_layout, drawtext_filter
    protected: list[str] = []
    try:
        from src.cast.script import (
            CAST_VILLAIN_KR,  # DN2026_0122: 영상 캐논 빌런 한글명도 줄바꿈 보호
        )
        from src.story import (
            caption_protected,  # (market_data, line, villain) -> list[str]
        )
        protected = list(caption_protected(market_snapshot or {}, text, villain))
        for kr in CAST_VILLAIN_KR.values():   # R6-12 제안: 이어받기 문장의 직전 빌런명 포함
            if kr in text and kr not in protected:
                protected.append(kr)
    except Exception as e:  # noqa: BLE001 - 보호 구절은 줄바꿈 보조, 실패 시 빈 목록
        logger.warning("[CAST] compose caption_protected_skipped err=%s", type(e).__name__)
    try:
        layout = build_layout(text, protected)
        return drawtext_filter(layout, caption_file)
    except LayoutError as e:
        raise ComposeError(f"caption_layout 실패 {caption_file.name}: {e}") from e


def _narration_plan(ordered: list[dict], narration: list[dict], clip_durs: list[float]) -> list[dict]:
    """DN2026_0115 — 컷별 target = min(clip, max(5.0, narr_sec/tempo + 0.6)). 누락 컷은 fail-closed."""
    by_cut = {int(n["cut"]): n for n in narration}
    plan = []
    for cut, clip_dur in zip(ordered, clip_durs):
        n = by_cut.get(int(cut["cut"]))
        if n is None:
            raise ComposeError(f"내레이션 누락 cut={cut['cut']}")
        wav = n.get("wav")
        if not wav or not os.path.exists(wav):
            raise ComposeError(f"내레이션 파일 없음 cut={cut['cut']}")
        tempo = float(n.get("tempo") or 1.0)
        sec = float(n.get("sec") or 0.0)
        if tempo <= 0 or sec <= 0:
            raise ComposeError(f"내레이션 길이·tempo 이상 cut={cut['cut']}")
        # R6-05 #3: 컷 수가 적으면(T1 3컷) 합계가 15초 경계에 걸리지 않게 최소 길이를 올린다
        min_cut = max(NARR_MIN_CUT_SEC, MIN_TOTAL_WITH_MARGIN / max(1, len(ordered)))
        target = min(clip_dur, max(min_cut, sec / tempo + NARR_TAIL_SEC))
        if sec / tempo > target - 0.05:   # R6-05 #4: 클립이 내레이션보다 짧으면 다음 비트와 겹침 → 차단
            raise ComposeError(f"내레이션이 컷보다 김 cut={cut['cut']} narr={sec / tempo:.2f}s clip={clip_dur:.2f}s")
        plan.append({"cut": int(cut["cut"]), "target_sec": round(target, 3), "tempo": tempo, "wav": wav})
    return plan


def compose(cuts: list[dict], villain: str | None, out_path: str,
            work_dir: str = "artifacts_cast/compose", narration: list[dict] | None = None,
            captions: list[str] | None = None, market_snapshot: dict | None = None) -> dict:
    """컷 mp4 들을 합성해 Shorts 한 편을 만든다.  # DN2026_0114 / DN2026_0115

    narration: [{cut, wav, sec, tempo}] — 있으면 컷 트림·내레이션 배치·BGM 덕킹(0.15).
    captions: 컷 번호 오름차순으로 정렬된 컷과 같은 순서의 자막 문장 목록.
    narration·captions 가 None 이면 DN2026_0114 동작 그대로(하위호환).
    반환: {"path", "duration_sec", "thumbnail", "segments":[{cut, target_sec, tempo}]}
    위반(해상도·오디오·15~60초)·ffmpeg 실패 시 ComposeError.
    """
    if not cuts:
        raise ComposeError("컷 없음")
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    ordered = sorted(cuts, key=lambda c: int(c["cut"]))
    if captions is not None and len(captions) != len(ordered):   # DN2026_0115
        raise ComposeError(f"자막 수 불일치 captions={len(captions)} cuts={len(ordered)}")
    logger.info("[CAST] compose v%s 시작 cuts=%d out=%s narration=%s captions=%s", VERSION,
                len(ordered), out_path, narration is not None, captions is not None)

    clip_durs: list[float] = []
    for cut in ordered:
        src = cut.get("path")
        if not src or not os.path.exists(src):
            raise ComposeError(f"컷 파일 없음 cut={cut.get('cut')}")
        dur = cut.get("duration_sec")
        if not isinstance(dur, (int, float)) or dur <= 0:
            dur = _duration(_probe(src))
        if dur <= 0:
            raise ComposeError(f"컷 길이 확인 불가 cut={cut.get('cut')}")
        clip_durs.append(float(dur))
    narr_plan = _narration_plan(ordered, narration, clip_durs) if narration is not None else None

    # 1) 클립 정규화 — 정지 이미지 옵션·zoompan 없이 모션 유지
    segments: list[Path] = []
    seg_info: list[dict] = []
    for i, cut in enumerate(ordered):
        dur = narr_plan[i]["target_sec"] if narr_plan else clip_durs[i]   # DN2026_0115 트림
        vf = VIDEO_FILTER
        if captions is not None:   # DN2026_0115 자막 번인
            vf = f"{vf},{_caption_filter(captions[i], villain, market_snapshot, work / f'cap_{i:02d}.txt')}"
        seg = work / f"seg_{i:02d}.mp4"
        _run(["ffmpeg", "-y", "-i", cut["path"], "-t", f"{float(dur):.3f}", "-vf", vf,
              "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(seg)],
             CLIP_TIMEOUT_SEC, f"normalize cut={cut.get('cut')}")
        segments.append(seg)
        seg_info.append({"cut": int(cut["cut"]), "target_sec": round(float(dur), 3),
                         "tempo": narr_plan[i]["tempo"] if narr_plan else 1.0})
        logger.info("[CAST] compose segment cut=%s dur=%.2f", cut.get("cut"), float(dur))

    # 2) concat demuxer — 무음 영상
    list_file = work / "concat.txt"
    list_file.write_text(
        "".join(f"file '{seg.resolve().as_posix()}'\n" for seg in segments), encoding="utf-8")
    silent = work / "silent.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
          "-c", "copy", str(silent)], MUX_TIMEOUT_SEC, "concat")
    total = _duration(_probe(str(silent)))
    if total <= 0:
        raise ComposeError("연결 영상 길이 확인 불가")

    # 3) BGM 믹스 — renderer._find_bgm 읽기 전용 재사용, 없으면 무음 트랙
    from src.renderer import _find_bgm
    bgm = _find_bgm(BGM_AXIS_ALIAS.get(villain or "", villain))
    if bgm:
        audio_in = ["-stream_loop", "-1", "-i", bgm]
    else:
        audio_in = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    fade_st = max(0.0, total - FADE_OUT_SEC)
    afilter = f"afade=t=out:st={fade_st:.3f}:d={FADE_OUT_SEC},loudnorm=I=-14:TP=-1.5:LRA=11"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    if narr_plan is None:
        _run(["ffmpeg", "-y", "-i", str(silent), *audio_in,
              "-map", "0:v:0", "-map", "1:a:0", "-af", afilter, "-shortest",
              "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
              "-movflags", "+faststart", out_path], MUX_TIMEOUT_SEC, "mux")
    else:
        # DN2026_0115: 내레이션(atempo·adelay, 컷 시작 오프셋) → amix(normalize=0) 단일 트랙,
        # BGM volume 0.15 루프 → 내레이션과 amix(duration=first=영상 길이) → afade → loudnorm
        narr_in: list[str] = []
        chains: list[str] = []
        offset = 0.0
        for j, item in enumerate(narr_plan):
            narr_in += ["-i", item["wav"]]
            ms = round(offset * 1000)
            tempo = f",atempo={item['tempo']:.4f}" if abs(item["tempo"] - 1.0) > 1e-6 else ""
            chains.append(f"[{j + 2}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
                          f"{tempo},adelay={ms}|{ms}[n{j}]")
            offset += item["target_sec"]
        labels = "".join(f"[n{j}]" for j in range(len(narr_plan)))
        if len(narr_plan) > 1:
            chains.append(f"{labels}amix=inputs={len(narr_plan)}:normalize=0:duration=longest[narr]")
        else:
            chains.append(f"{labels}anull[narr]")
        chains.append(f"[narr]apad,atrim=0:{total:.3f}[narrp]")
        chains.append(f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                      f"volume={BGM_UNDER_NARR_VOLUME}[bgm]")
        chains.append(f"[narrp][bgm]amix=inputs=2:normalize=0:duration=first,{afilter}[aout]")
        _run(["ffmpeg", "-y", "-i", str(silent), *audio_in, *narr_in,
              "-filter_complex", ";".join(chains),
              "-map", "0:v:0", "-map", "[aout]", "-shortest",
              "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
              "-movflags", "+faststart", out_path], MUX_TIMEOUT_SEC, "mux narration")
    logger.info("[CAST] compose mux bgm=%s", os.path.basename(bgm) if bgm else "anullsrc")

    # 4) 썸네일 — 첫 컷 1.5초(짧으면 중간) 프레임
    first_dur = _duration(_probe(str(segments[0])))
    at = THUMB_AT_SEC if first_dur > THUMB_AT_SEC else max(0.0, first_dur / 2)
    thumb = work / "thumb.png"
    _run(["ffmpeg", "-y", "-ss", f"{at:.3f}", "-i", str(segments[0]),
          "-frames:v", "1", str(thumb)], CLIP_TIMEOUT_SEC, "thumbnail")
    if not thumb.exists():
        raise ComposeError("썸네일 생성 실패")

    # 5) 규격 검증
    info = _probe(out_path)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = _duration(info)
    if not video or (video.get("width"), video.get("height")) != (TARGET_W, TARGET_H):
        raise ComposeError(f"해상도 위반: {video and (video.get('width'), video.get('height'))}")
    if not has_audio:
        raise ComposeError("오디오 스트림 없음")
    if not (MIN_TOTAL_SEC <= duration <= MAX_TOTAL_SEC):
        raise ComposeError(f"길이 위반 {duration:.2f}s (허용 {MIN_TOTAL_SEC}~{MAX_TOTAL_SEC}s)")
    logger.info("[CAST] compose done path=%s duration=%.2f thumb=%s", out_path, duration, thumb)
    return {"path": out_path, "duration_sec": round(duration, 3), "thumbnail": str(thumb),
            "segments": seg_info}   # DN2026_0115
