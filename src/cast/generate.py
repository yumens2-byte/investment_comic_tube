"""fal i2v 컷 생성 — CAST-LINE T3 generate 단계 실행부.

확정 사실 반영 (2026-11-17 부록 E 실측):
- 엔드포인트 minimax/h3/image-to-video, 768P $0.06/s, duration 5~15 정수
- prompt_expansion_mode "disabled"
- fal은 젠스파크 래퍼 URL 다운로드 불가(422) → REF는 저장소 assets/refs raw URL 주입(refresh_refs, 부록 S) # DN2026_0114
- REF 미확정 캐릭터 컷은 호출 전 차단(fail-closed) — casting.cast_for_tier가 보장
- 컷당 재생성 1회 한정, 재차 실패 시 폐기·원장 기록 (캐논 규칙 §7)
- DN2026_0114: 재제출은 fal 터미널 실패(FAILED 등)에만 1회. 타임아웃·조회 오류·다운로드·
  ffprobe 실패는 재제출하지 않는다(이중 과금 방지). 시간 상한은 호출부가 deadline_ts로 배분.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request

logger = logging.getLogger("cast.generate")

FAL_ENDPOINT = os.environ.get("CAST_FAL_ENDPOINT", "minimax/h3/image-to-video")
FAL_PRICE_PER_SEC = 0.06          # 768P — fal 공식 요표 + 2026-11-17 실측 정합
MAX_RETRIES_PER_CUT = 1           # 컷당 재생성 한도 (캐논 규칙 §7)
POLL_INTERVAL_SEC = 12
POLL_TRANSIENT_MAX = 3            # 상태 조회 일시 오류 연속 허용 횟수 # DN2026_0114
DOWNLOAD_TRIES = 3                # 같은 URL 다운로드 시도 횟수(재제출 아님) # DN2026_0114
PROBE_TIMEOUT_SEC = 60            # DN2026_0114
# POLL_TIMEOUT_SEC 제거 — 컷별 deadline_ts(호출부 시간 예산 배분)로 대체 # DN2026_0114

# 비트별 상황값 — 발행용 프롬프트는 캐논 고정 블록 + 이 문장만 얹는다 (캐논 규칙 §3).
ROLE_SITUATION = {
    "ESTABLISH_THREAT": "The villain looms over a cracking financial city skyline, shockwave and debris.",
    "ESCALATE": "Crowds scatter through a modern financial district as market lights flicker and sirens wail.",
    "ENGAGE": "The hero lands in the foreground in a battle stance, determined, looking up at the threat.",
    "CRISIS": "A shockwave throws the hero backward through debris, shaky handheld framing.",
    "TURN": "Two contrasting zones of light and dark split the city as golden energy lights back up.",
    "RESOLVE": "Dawn light over a recovering skyline; the hero stands down, no heroic pose.",
    "NEXT_OMEN": "A new shadow looms on the horizon beyond the stabilized city at dusk.",
    "AFTERMATH_CALM": "Quiet aftermath over a settling skyline, dust and embers fading, no heroic pose.",
    "REGROUP": "The hero gathers himself on a rooftop at first light, calm and deliberate, city recovering.",
}


class GenerationError(RuntimeError):
    """컷 생성 실패 — 재시도 한도 초과 포함."""


def build_cut_prompt(canon_prompt: str, role: str) -> str:
    """캐논 고정 블록 + 비트 상황값. 1컷 1감정 1액션 원칙."""
    situation = ROLE_SITUATION.get(role)
    if not situation:
        raise GenerationError(f"미정의 비트 역할 '{role}' — 상황값 없이 생성 금지")
    return f"{canon_prompt} {situation}"


PROMPT_MAX_CHARS = 2000   # DN2026_0115
PROMPT_HEAD = "Comic-book cinematic vertical shot. "
PROMPT_TAIL = " Single action, single camera move. No text, no numbers, no letters anywhere in frame."
HERO_KEYS = ("EDT", "FG-01", "LEV-01")   # 캐논 _cast_video_canon.characters 히어로 키


def _video_canon_chars(canon: dict) -> dict:
    """canon_data 전체 또는 _cast_video_canon 블록 어느 쪽이 와도 characters 를 꺼낸다."""
    block = canon.get("_cast_video_canon", canon) if isinstance(canon, dict) else {}
    chars = block.get("characters") if isinstance(block, dict) else None
    return chars if isinstance(chars, dict) else {}


def build_cut_prompt_v2(characters: list[str], situation_en: str, canon: dict,
                        axis: str | None) -> str:
    """DN2026_0115 — 캐논 고정 블록(등장 캐릭터별 prompt_en) + 빌런 공격형태(axis) + 대본 상황값.

    히어로 키 EDT/FG-01/LEV-01, 빌런은 이름 키. 캐논에 없는 캐릭터는 이름만 넣는다.
    등장 캐릭터 negative_en 은 " Avoid: ..." 로 덧붙인다. 2000자 초과 시 GenerationError.
    """
    situation = (situation_en or "").strip()
    if not situation:
        raise GenerationError("situation_en 비어 있음 — 상황값 없이 생성 금지")
    chars = _video_canon_chars(canon)
    blocks: list[str] = []
    negatives: list[str] = []
    for name in characters or []:
        entry = chars.get(name)
        if not isinstance(entry, dict) or not entry.get("prompt_en"):
            blocks.append(f"{name}.")
            continue
        text = entry["prompt_en"].strip()
        attack = (entry.get("attack_forms_en") or {}).get(axis or "") if name not in HERO_KEYS else None
        if attack:
            text = f"{text.rstrip('.')}, {attack.strip().rstrip('.')}."
        elif not text.endswith("."):
            text += "."
        blocks.append(text)
        neg = (entry.get("negative_en") or "").strip().rstrip(".")
        if neg:
            negatives.append(neg)
    prompt = PROMPT_HEAD + " ".join(blocks)
    prompt = f"{prompt} {situation}{PROMPT_TAIL}" if blocks else f"{PROMPT_HEAD}{situation}{PROMPT_TAIL}"
    if negatives:
        prompt += " Avoid: " + "; ".join(negatives) + "."
    if len(prompt) > PROMPT_MAX_CHARS:
        raise GenerationError(f"prompt_too_long chars={len(prompt)} max={PROMPT_MAX_CHARS}")
    return prompt


def build_fal_request(prompt: str, image_url: str | None, duration: int = 10) -> dict:
    """fal 요청 JSON — T3 실측 계약 그대로."""
    if not 5 <= duration <= 15:
        raise GenerationError(f"duration={duration} — fal 지원 범위(5~15초) 이탈")
    req: dict = {
        "prompt": prompt,
        "duration": int(duration),
        "resolution": "768P",
        "prompt_expansion_mode": "disabled",
    }
    if image_url:
        req["image_url"] = image_url   # 첫 프레임 REF 조건화
    return req


def estimate_cost(duration_sec: float) -> float:
    return round(duration_sec * FAL_PRICE_PER_SEC, 2)


def _fal_headers() -> dict:
    key = os.environ.get("FAL_AI_KEY") or os.environ.get("FAL_KEY")
    if not key:
        raise GenerationError("FAL_AI_KEY 미설정 — fal 실생성은 키 주입 후에만")
    return {"Authorization": f"Key {key}", "Content-Type": "application/json"}


def _fal_json(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # DN2026_0114 (R6-01 #9): 응답 본문은 로그·artifact에 남기지 않는다 — 상태코드만
        raise GenerationError(f"fal HTTP {e.code}") from e


def submit_cut(request: dict) -> dict:
    """제출 — queue API. 테스트에서 네트워크 없이 상태머신 검증 가능하도록 분리."""
    r = urllib.request.Request(
        f"https://queue.fal.run/{FAL_ENDPOINT}",
        data=json.dumps(request).encode(),
        headers=_fal_headers(),
        method="POST",
    )
    sub = _fal_json(r)
    if not sub.get("request_id"):
        raise GenerationError("fal 제출 응답 불완전 — request_id 부재")
    return sub


class _TerminalFailure(Exception):
    """fal이 작업을 터미널 실패(FAILED 등)로 끝냄 — 재제출 1회 허용 대상. # DN2026_0114"""


def _get_json(url: str, headers: dict) -> dict:
    """상태·결과 GET 1회. 예외 분류는 호출부(_get_json_transient)가 한다."""
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return json.loads(r.read())


def _get_json_transient(url: str, headers: dict) -> dict:
    """URLError·HTTP 5xx는 연속 POLL_TRANSIENT_MAX회까지 대기 후 재시도. # DN2026_0114
    4xx·그 외는 GenerationError(재제출 없음 — 이중 과금 방지)."""
    fails = 0
    while True:
        try:
            return _get_json(url, headers)
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise GenerationError(f"fal 상태 조회 HTTP {e.code}") from e
            err: Exception = e
        except urllib.error.URLError as e:
            err = e
        except (TimeoutError, OSError) as e:    # 소켓 timeout 등 일시 오류
            err = e
        fails += 1
        if fails > POLL_TRANSIENT_MAX:
            raise GenerationError(f"fal 상태 조회 연속 실패 {fails}회 — {type(err).__name__}") from err
        logger.warning("fal_poll_transient n=%d err=%s", fails, type(err).__name__)
        time.sleep(POLL_INTERVAL_SEC)


def poll_cut(sub: dict, deadline_ts: float) -> str:
    """완료 폴링 → video URL. # DN2026_0114
    IN_QUEUE/IN_PROGRESS 계속, COMPLETED → url, 그 외 상태 → _TerminalFailure,
    deadline_ts 초과 → GenerationError("fal 타임아웃")."""
    headers = {"Authorization": _fal_headers()["Authorization"]}
    while True:
        if time.time() > deadline_ts:
            raise GenerationError("fal 타임아웃")
        st = _get_json_transient(sub["status_url"], headers)
        status = st.get("status")
        if status == "COMPLETED":
            res = _get_json_transient(sub["response_url"], headers)
            url = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
            # DN2026_0115: fal queue 실패는 COMPLETED 결과의 error/detail 로 온다(공식 queue 문서).
            # 응답 본문은 로그하지 않고 error_type 만 남긴다 → 재제출 1회 경로.
            if not url and ("error" in res or "detail" in res):
                etype = res.get("error_type")
                etype = etype if isinstance(etype, str) and len(etype) <= 64 else "unknown"
                raise _TerminalFailure(f"fal_failed type={etype}")
            if not url:
                raise GenerationError("fal 완료 응답에 video.url 부재")
            return url
        if status not in ("IN_QUEUE", "IN_PROGRESS"):
            raise _TerminalFailure(f"fal 비정상 상태 {status}")
        remaining = deadline_ts - time.time()
        time.sleep(max(0.0, min(POLL_INTERVAL_SEC, remaining)))


def _download(url: str, out_path: str) -> None:
    """같은 URL을 최대 DOWNLOAD_TRIES회 다운로드(urlopen timeout=120). 재제출 없음. # DN2026_0114"""
    last: Exception | None = None
    for n in range(1, DOWNLOAD_TRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(out_path, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            return
        except (urllib.error.URLError, OSError) as e:
            last = e
            logger.warning("cut_download_retry n=%d err=%s", n, type(e).__name__)
    raise GenerationError(f"컷 다운로드 실패({DOWNLOAD_TRIES}회) — {type(last).__name__}")


def generate_cut(request: dict, out_path: str, *, cut_no: int, deadline_ts: float,
                 on_submit=None) -> dict:
    """컷 1개 생성 — 제출 1회, 터미널 실패(FAILED 등)일 때만 재제출 1회. # DN2026_0114

    재제출하지 않는 경우: 타임아웃·조회 연속 실패·다운로드 실패·ffprobe 실패(이중 과금 방지).
    on_submit: 각 제출 직전 호출(비용 가드·원장 선기록). 예외 시 제출하지 않는다.
    """
    attempts = 0
    sub: dict | None = None
    video_url = None
    while video_url is None:
        if time.time() > deadline_ts:
            raise GenerationError(f"컷{cut_no} fal 타임아웃")
        if on_submit is not None:
            on_submit()
        attempts += 1
        sub = submit_cut(request)
        try:
            video_url = poll_cut(sub, deadline_ts)
        except _TerminalFailure as e:
            if attempts > MAX_RETRIES_PER_CUT:
                raise GenerationError(f"컷{cut_no} 생성 실패(재제출 {MAX_RETRIES_PER_CUT}회 소진): {e}") from e
            logger.warning("cut_resubmit cut=%d attempt=%d reason=%s", cut_no, attempts, e)
    _download(video_url, out_path)
    duration = _probe_duration(out_path)
    return {"cut": cut_no, "path": out_path, "duration_sec": duration,
            "cost_usd": estimate_cost(duration), "request_id": sub["request_id"],
            "attempts": attempts}


def _probe_duration(path: str) -> float:
    """ffprobe 기술검증 — investment-os _validate_output 패턴 준용."""
    import subprocess
    try:  # DN2026_0114 — timeout·미설치도 GenerationError(재제출 없음)
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], capture_output=True, text=True,
                           timeout=PROBE_TIMEOUT_SEC, check=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise GenerationError(f"ffprobe 실행 실패({type(e).__name__}) — {path}") from e
    if r.returncode != 0:
        raise GenerationError(f"ffprobe 실패 — 손상 파일 {path}")
    try:
        return float(r.stdout.strip())
    except ValueError as e:
        raise GenerationError(f"ffprobe 길이 해석 실패 — {path}") from e
