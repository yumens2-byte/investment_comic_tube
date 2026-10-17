"""Durable text responses: cache before validation, never refund reservations."""
from types import SimpleNamespace

from src.asset_store import read_json, write_json
from src.pipeline_control import current, fingerprint, ControlError


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
