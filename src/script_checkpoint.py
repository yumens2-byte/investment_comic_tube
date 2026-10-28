"""Durable text responses: cache before validation, never refund reservations."""
import logging
from types import SimpleNamespace

from src.asset_store import read_json, write_json
from src.pipeline_control import current, fingerprint, ControlError

logger = logging.getLogger(__name__)

def stable_market(snapshot):
    return {key: {field: value for field, value in metric.items() if field != "collected_at_utc"}
            for key, metric in snapshot.items() if not key.startswith("_") and isinstance(metric, dict)}


def freeze_context(key, value):
    if not current():
        return value
    previous = read_json("script-context", key, {"schema_version": 1})
    if previous is not None and fingerprint(previous) != fingerprint(value):
        raise ControlError("input_context_changed")
    if previous is None:
        write_json("script-context", key, {"schema_version": 1}, value)
    return previous if previous is not None else value


def pin_market(track, snapshot):
    """하루의 모든 재시도를 그날 처음 동결된 시세에 묶는다.

    2026-10-28 사고: 첫 시도(06:26 UTC)가 Gemini 429로 중단된 뒤, 재시도(13:43 UTC)는
    미국 장중이라 yfinance 가 진행 중인 세션 봉을 돌려줬고, 동결 시세와 달라
    input_context_changed 로 그날 남은 재시도가 전부 막혔다.
    새 시세로 다시 쓰는 대신 동결 시세를 그대로 재사용하면, 캐시된 응답과 사실이
    섞이지 않으면서도 재시도가 가능하다. 트랙이 바뀌었거나 동결 시세가 오래돼
    더 이상 유효하지 않으면 지금처럼 중단한다.
    """
    if not current():
        return snapshot
    value = {"track": track, "market": stable_market(snapshot)}
    previous = read_json("script-context", "market", {"schema_version": 1})
    if previous is None:
        write_json("script-context", "market", {"schema_version": 1}, value)
        return snapshot
    if fingerprint(previous) == fingerprint(value):
        return snapshot
    if previous.get("track") != track or not isinstance(previous.get("market"), dict):
        raise ControlError("input_context_changed", {"field": "track"})
    changed = sorted(key for key in set(previous["market"]) | set(value["market"])
                     if previous["market"].get(key) != value["market"].get(key))
    pinned = {key: {**metric, "collected_at_utc": (snapshot.get(key) or {}).get("collected_at_utc")}
              for key, metric in previous["market"].items()}
    from copy import deepcopy
    from src.market_facts import validate_provenance
    from src.validation import ValidationError
    try:
        # stale_sessions 를 다시 쓰면 동결값과 어긋나므로 사본으로만 검사한다
        validate_provenance(deepcopy(pinned))
    except ValidationError as exc:
        raise ControlError("input_context_changed", {"field": "market", "pinned_invalid": str(exc)}) from exc
    logger.warning("market_context_pinned changed=%s", ",".join(changed))
    return pinned


def response(client, generate, asset, ordinal, *, model, contents):
    if not current():
        return generate(client, "text", asset, model=model, contents=contents)
    # One logical stage per day; old responses are preserved across policy changes.
    # Context binds raw facts/previous state. Revalidation always uses current policy.
    context = read_json("script-context", "generation", {"schema_version": 1})
    if context is None and asset == "goc":
        context = read_json("script-context", "goc-event", {"schema_version": 1})
    inputs = {"schema_version": 1, "asset": asset, "context": context}
    key = f"{asset}-{ordinal}"
    saved = read_json("text-response", key, inputs)
    if saved is not None:
        return SimpleNamespace(text=saved["text"])
    result = generate(client, "text", asset, model=model, contents=contents)
    text = result.text or ""
    write_json("text-response", key, inputs,
               {"text": text, "model": model, "prompt_hash": fingerprint(contents)})
    return SimpleNamespace(text=text)


def evaluation(asset, ordinal, lines, problems):
    if current():
        from src.caption_policy import policy_name
        write_json('text-evaluation', f'{asset}-{ordinal}', {'schema_version': 1},
                   {'policy': policy_name(), 'resolved': lines, 'problems': problems})
