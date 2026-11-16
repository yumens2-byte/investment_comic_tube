"""fal i2v 컷 생성 — CAST-LINE T3 generate 단계 실행부.

확정 사실 반영 (2026-11-17 부록 E 실측):
- 엔드포인트 minimax/h3/image-to-video, 768P $0.06/s, duration 5~15 정수
- prompt_expansion_mode "disabled"
- fal은 젠스파크 래퍼 URL 다운로드 불가(422) → REF는 AI Drive readable URL 주입
- REF 미확정 캐릭터 컷은 호출 전 차단(fail-closed) — casting.cast_for_tier가 보장
- 컷당 재생성 1회 한정, 재차 실패 시 폐기·원장 기록 (캐논 규칙 §7)
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
POLL_TIMEOUT_SEC = 900

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
        detail = e.read().decode(errors="replace")[:300]
        raise GenerationError(f"fal HTTP {e.code} — {detail}") from e


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


def poll_cut(sub: dict, timeout_sec: int = POLL_TIMEOUT_SEC) -> str:
    """완료 폴링 → video URL. 비정상 상태·타임아웃 시 GenerationError."""
    headers = {"Authorization": _fal_headers()["Authorization"]}
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        with urllib.request.urlopen(urllib.request.Request(sub["status_url"], headers=headers), timeout=30) as r:
            st = json.loads(r.read())
        status = st.get("status")
        if status == "COMPLETED":
            with urllib.request.urlopen(urllib.request.Request(sub["response_url"], headers=headers), timeout=30) as r:
                res = json.loads(r.read())
            url = (res.get("video") or {}).get("url")
            if not url:
                raise GenerationError("fal 완료 응답에 video.url 부재")
            return url
        if status not in ("IN_QUEUE", "IN_PROGRESS"):
            raise GenerationError(f"fal 비정상 상태 {status}")
        time.sleep(POLL_INTERVAL_SEC)
    raise GenerationError(f"fal 타임아웃({timeout_sec}s)")


def generate_cut(request: dict, out_path: str, retries: int = MAX_RETRIES_PER_CUT) -> dict:
    """컷 1개 생성(제출→폴링→저장) + 비용 계산. 실패 시 재시도 1회 후 예외."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            sub = submit_cut(request)
            video_url = poll_cut(sub)
            urllib.request.urlretrieve(video_url, out_path)
            duration = _probe_duration(out_path)
            return {"cut": request.get("_cut_no"), "path": out_path, "duration_sec": duration,
                    "cost_usd": estimate_cost(duration), "request_id": sub["request_id"],
                    "attempts": attempt + 1}
        except (GenerationError, urllib.error.URLError, OSError) as e:
            last_err = e
            logger.warning("cut_generate_retry attempt=%d err=%s", attempt + 1, e)
    raise GenerationError(f"컷 생성 실패(재시도 {retries}회 소진): {last_err}")


def _probe_duration(path: str) -> float:
    """ffprobe 기술검증 — investment-os _validate_output 패턴 준용."""
    import subprocess
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise GenerationError(f"ffprobe 실패 — 손상 파일 {path}")
    return float(r.stdout.strip())
