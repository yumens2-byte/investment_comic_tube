"""GOC perspective of the latest EDT market event, for upload-free beta runs."""

from __future__ import annotations

from src.pipeline_control import generate as guarded_generate, current, ControlError, make_client

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from src.db_client import get_client
from src.drive_manager import EpisodeStateUnavailable, PUBLISHED_STATUSES
from src.renderer import HOOK_WRAP_CHARS, _wrap_korean
from src.validation import ValidationError

GOC_VOICE = "Kore"
GOC_SCENES = [
    ("HOOK", "Close-up of GOC assessing an approaching risk; use the provided character reference."),
    ("THREAT", "The same market villain threatens the financial city; GOC watches the exposed capital."),
    ("IMPACT", "The market shock reaches investor portfolios; GOC maps the vulnerable positions."),
    ("HERO", "GOC steps forward to protect capital and marks a clear defensive boundary."),
    ("CLASH", "GOC confronts the villain by reducing exposure and defending the boundary."),
    ("LESSON", "GOC reviews the remaining risk without promising a return or predicting the next move."),
]
GOC_IMAGE_SLOTS = [
    "Close-up of GOC, Guardian of Capital, assessing an immediate market risk. Keep lower caption area visually clear.",
    "The villain threatens a financial city; GOC measures the capital exposed to the shock.",
    "GOC establishes a protective boundary between investor capital and the villain.",
    "GOC defends that boundary and reviews the remaining uncertainty, with no guaranteed victory.",
]


def latest_edt_event(*, today=None, max_age_days: int = 0) -> dict:
    """Use the latest published EDT event, with an explicit Korean-day age limit."""
    today = today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        result = (get_client().table("episodes")
                  .select("episode_no,status,market_as_of,villain,market_snapshot")
                  .in_("status", PUBLISHED_STATUSES)
                  .order("episode_no", desc=True).limit(1).execute())
    except Exception as exc:
        raise EpisodeStateUnavailable("GOC 사건 조회 실패") from exc
    rows = result.data or []
    if not rows:
        raise ValidationError("발행된 EDT 사건이 없음")
    event = rows[0]
    try:
        timestamp = datetime.fromisoformat(event["market_as_of"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValidationError("EDT 사건의 기준 시각에 시간대가 없음")
        age_days = (today - timestamp.astimezone(ZoneInfo("Asia/Seoul")).date()).days
        if not 0 <= age_days <= max_age_days:
            raise ValidationError("EDT 사건이 허용된 한국 날짜 범위 밖임")
    except (KeyError, AttributeError, ValueError) as exc:
        raise ValidationError("EDT 사건의 기준 시각이 없음") from exc
    if not isinstance(event.get("market_snapshot"), dict) or not event.get("villain"):
        raise ValidationError("EDT 사건의 시장 데이터 또는 빌런이 없음")
    return event


def build_goc_script(event: dict) -> dict:
    """Generate a separate six-beat script; never substitute EDT narration."""
    from google import genai

    snapshot = event["market_snapshot"]
    values = {key: {field: metric.get(field) for field in ("close", "change_pct")}
              for key, metric in snapshot.items() if isinstance(metric, dict)
              and key in ("TNX", "VIX", "NASDAQ", "SPX", "DXY", "GOLD", "OIL")}
    prompt = (
        "한국어 6비트 쇼츠 대본을 JSON 문자열 배열 6개로만 작성. "
        "주인공 GOC(Guardian of Capital)는 같은 시장 사건을 자본 보호와 위험 통제 시점에서 본다. "
        "EDT의 대사나 행동을 복제하지 말고, 노출·비중·손실 한도·방어선·남은 불확실성을 설명한다. "
        "투자 수익 보장, 매수 권유, 제공하지 않은 숫자 금지. 각 장면 한 문장. "
        "영상 자막 공간 제한: 첫 문장은 한글 13자씩 최대 2줄, 나머지는 19자씩 최대 3줄. "
        "공백을 포함한 첫 문장 전체를 20자 이내, 나머지 각 문장은 40자 이내로 간결하게 작성. "
        "이 사건을 오늘의 신규 시장 데이터라고 말하지 말 것. "
        f"원본 시장 사건 기준 시각(ISO 8601): {event['market_as_of']}; "
        f"빌런: {event['villain']}; 회차: {event['episode_no']}; 사실 데이터: {json.dumps(values, ensure_ascii=False)}"
    )
    if current():
        from src.market_facts import prompt_contract
        prompt += prompt_contract()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValidationError("GOC 대본 API 키 없음")
    client = (make_client(key) if current() else genai.Client(api_key=key))
    for attempt in range(2):
        response = guarded_generate(client, "text", "goc",
            model="gemini-3.6-flash", contents=prompt)
        try:
            narrations = json.loads((response.text or "").strip())
        except (ValueError, AttributeError) as exc:
            if current() and attempt == 0:
                prompt += " JSON 불량. 문자열 6개 배열만 다시 작성."
                continue
            raise ValidationError("GOC 대본 JSON 파싱 실패") from exc
        if not isinstance(narrations, list) or len(narrations) != 6 or not all(
            isinstance(line, str) and line.strip() for line in narrations
        ):
            if current() and attempt == 0:
                prompt += " 구조 불량. 비어 있지 않은 문자열 6개 배열만 다시 작성."
                continue
            raise ValidationError("GOC 대본 6비트 불완전")
        if current():
            from src.market_facts import resolve
            try:
                narrations = [resolve(line, snapshot) for line in narrations]
            except ValidationError:
                if attempt == 1:
                    raise
                prompt += " 사실 검증 실패. 숫자·방향을 직접 쓰지 말고 등록 사실 토큰만 사용."
                continue
        invalid = []
        for index, line in enumerate(narrations):
            try:
                _wrap_korean(line.strip(), HOOK_WRAP_CHARS if index == 0 else 19,
                             max_lines=2 if index == 0 else 3)
            except ValueError:
                invalid.append(index + 1)
        if not invalid:
            break
        if attempt == 1:
            raise ValidationError(f"GOC 자막 길이 초과 비트={invalid}")
        prompt += f" 앞선 응답의 {invalid}번째 문장이 화면을 넘었다. 전체 JSON 배열을 더 짧게 다시 작성."
    storyboard = [
        {"beat": beat, "scene": scene, "narration": line.strip(), "is_hook": idx == 0}
        for idx, ((beat, scene), line) in enumerate(zip(GOC_SCENES, narrations, strict=True))
    ]
    return {"track": "GOC", "episode": event["episode_no"], "villain": event["villain"],
            "theme": "같은 시장, 자본 보호의 시점", "market_snapshot": snapshot,
            "storyboard": storyboard, "source_market_as_of": event["market_as_of"]}
