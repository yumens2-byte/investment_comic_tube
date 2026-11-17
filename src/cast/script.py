# DN2026_0115
"""CAST-LINE 비트별 한국어 내레이션 대본. # DN2026_0115

- dry_run 또는 GEMINI_API_KEY 부재 → 결정적 template(외부 호출 0).
- 그 외 → Gemini(src.story.STORY_MODEL) JSON 응답 → 검증 → 위반 시 1회 재호출 → 재위반 비트만 template.
- 수치는 {{FACT:KEY.field}} 토큰만 허용하고 src.market_facts.resolve 로 대입한다(지어낸 수치 금지).
- 키·응답 본문은 로그에 남기지 않는다(예외 유형명만).
"""
from __future__ import annotations

import json
import logging
import os
import re

from src import market_facts
from src.korean_text import particle_for
from src.story import ENDING_SPECS, STORY_MODEL

from .generate import ROLE_SITUATION
from .storyline import HEROES, load_cast_canon

logger = logging.getLogger("cast.script")

SCRIPT_VERSION = 1
MAX_TEMPLATE_CHARS = 28        # 프롬프트 규칙: 토큰 제외 28자
MAX_RESOLVED_CHARS = 36        # 검증: 토큰 대입 후 36자
MAX_SITUATION_WORDS = 45
FORBIDDEN = re.compile(r"매수|매도|사라|팔아라|수익 보장|무조건|확실한 수익")
AXIS_FACT_KEY = {"rates": "TNX", "volatility": "VIX", "momentum": "NASDAQ"}

try:
    from src.hooks import VILLAIN_KR
except Exception:  # noqa: BLE001  # pragma: no cover — hooks 부재 시 영문명
    VILLAIN_KR = {}

ROLE_PURPOSE = {
    "ESTABLISH_THREAT": "지표 사실 토큰 1개 + 빌런 등장",
    "ESCALATE": "위험이 도시 전체로 확산",
    "ENGAGE": "EDT가 원칙을 선언",
    "CRISIS": "최대 위기(토큰 1개까지 허용)",
    "TURN": "hero_focus와 EDT의 협업으로 전환",
    "RESOLVE": "교훈 1개(비중·분산·손절·관망 중 하나), 영웅 포즈 금지",
    "NEXT_OMEN": "다음 위협(omen 빌런/축)의 그림자 암시, 숫자 금지, ending_type 형식",
    "AFTERMATH_CALM": "폭풍이 지나간 시장의 고요(첫 비트면 지표 사실 토큰 1개)",
    "REGROUP": "EDT가 원칙을 재점검하며 다음을 준비",
}

_ESCALATE = {"rates": "빚의 무게가 도시 전체로 번진다",
             "volatility": "흔들림이 시장 곳곳으로 번진다",
             "momentum": "과열된 질주가 거리를 휩쓴다"}
_TURN = {"EDT": "EDT가 원칙으로 흐름을 되돌린다",
         "FG-01": "노출을 잰 동료와 EDT가 반격한다",
         "LEV-01": "과신을 버린 동료가 EDT 곁에 선다"}
_RESOLVE = {"rates": "무게를 버티는 힘은 분산이다",
            "volatility": "흔들릴수록 비중 관리가 먼저다",
            "momentum": "과열 앞에서는 관망도 전략이다"}
_SHADOW = {"rates": "금리의 그림자", "volatility": "변동성의 그림자", "momentum": "과열의 그림자"}
_OMEN = {"QUESTION": lambda s: f"{s}{particle_for(s, '는')} 어디로 향할까",
         "DECLARE": lambda s: f"{_subj(s)} 온다. 준비는 끝났다",
         "TEASE": lambda s: f"다음 주, {_subj(s)} 깨어난다",
         "TWIST": lambda s: f"그런데 진짜 위협은 {s}였다"}
_PAYOFF = {"hit": "예고대로 ", "twist": "예상 밖, "}


def villain_kr(villain: str) -> str:
    return VILLAIN_KR.get(villain, villain)


def _subj(word: str) -> str:
    return word + particle_for(word, "이")


def fact_token(axis: str, snapshot: dict) -> str | None:
    """축 대표키 change_pct 토큰 → SPX → None(숫자 없는 문장)."""
    for key in (AXIS_FACT_KEY.get(axis), "SPX"):
        if key and (snapshot.get(key) or {}).get("change_pct") is not None:
            return f"{{{{FACT:{key}.change_pct}}}}"
    return None


def template_line(role: str, index: int, decision: dict, snapshot: dict) -> str:
    """토큰 포함 원문(대입 전). index=0 이면 지표 사실 토큰을 넣는다."""
    v = villain_kr(decision["villain"])
    axis = decision["axis"]
    payoff = _PAYOFF.get(decision.get("omen_payoff") or "", "")
    token = fact_token(axis, snapshot) if index == 0 else None
    if role == "ESTABLISH_THREAT":
        line = f"{token}, {v} 등장" if token else f"{_subj(v)} 도시를 덮쳤다"
        return payoff + line
    if role == "AFTERMATH_CALM":
        return f"{token}, 시장이 숨을 고른다" if token else "폭풍이 지나고 시장이 숨을 고른다"
    if role == "ESCALATE":
        return _ESCALATE[axis]
    if role == "ENGAGE":
        return "EDT가 말한다. 원칙이 먼저다"
    if role == "CRISIS":
        return f"{_subj(v)} 방어선을 무너뜨린다"
    if role == "TURN":
        return _TURN.get(decision.get("hero_focus"), _TURN["EDT"])
    if role == "RESOLVE":
        return _RESOLVE[axis]
    if role == "REGROUP":
        return "EDT가 다시 원칙을 점검한다"
    if role == "NEXT_OMEN":
        shadow = _SHADOW[decision.get("omen_axis") or axis]
        ending = _OMEN.get(str(decision.get("ending_type") or "").upper(), _OMEN["TEASE"])
        return ending(shadow)
    raise ValueError(f"미정의 비트 역할 '{role}'")


def _villain_in(cut: dict) -> bool:
    return any(c not in HEROES for c in cut.get("characters") or [])


def template_situation(cut: dict, decision: dict, canon: dict) -> str:
    base = ROLE_SITUATION.get(cut["role"], "")
    if _villain_in(cut):
        omen = cut["role"] == "NEXT_OMEN"
        name = decision["omen_villain"] if omen else decision["villain"]
        forms = (canon["characters"].get(name) or {}).get("attack_forms_en") or {}
        form = forms.get(decision["omen_axis"] if omen else decision["axis"])
        if form:
            base = f"{base} The villain is {form}."
    return base


def _template_beat(cut: dict, index: int, decision: dict, snapshot: dict, canon: dict) -> dict:
    raw = template_line(cut["role"], index, decision, snapshot)
    return {"cut": cut["cut"], "role": cut["role"],
            "narration": market_facts.resolve(raw, snapshot),
            "situation_en": template_situation(cut, decision, canon)}


def _template_script(plan, decision, snapshot, canon) -> dict:
    return {"version": SCRIPT_VERSION, "source": "template",
            "ending_type": decision["ending_type"],
            "beats": [_template_beat(c, i, decision, snapshot, canon) for i, c in enumerate(plan)]}


# ---------- Gemini ----------

def _make_client(api_key: str):
    from google import genai  # 지연 import — dry_run·테스트에서 미요구
    return genai.Client(api_key=api_key)


def _call(client, prompt: str) -> str:
    resp = client.models.generate_content(
        model=STORY_MODEL, contents=prompt,
        config={"response_mime_type": "application/json"})
    return getattr(resp, "text", None) or ""


def build_prompt(tier, plan, snapshot, decision, prev, canon) -> str:
    chars = canon["characters"]
    cast = sorted({c for cut in plan for c in cut["characters"]})
    persona = "\n".join(f"- {c}: {chars[c]['persona_kr']}" for c in cast
                        if c in chars and chars[c].get("persona_kr"))
    forms = (chars.get(decision["villain"]) or {}).get("attack_forms_en") or {}
    ending = ENDING_SPECS.get(str(decision["ending_type"]).upper(), {})
    payoff = {"hit": "지난 회 예고가 적중 — 1비트에 '예고대로'를 넣는다.",
              "twist": "지난 회 예고가 빗나감 — 1비트에 '예상 밖'을 넣는다."}.get(
        decision.get("omen_payoff") or "", "지난 회 예고 없음.")
    beats = "\n".join(f"- cut {c['cut']} {c['role']} (출연: {', '.join(c['characters'])}): "
                      f"{ROLE_PURPOSE.get(c['role'], '')}" for c in plan)
    unresolved = decision.get("prev_unresolved") or (((prev or {}).get("story_state") or {}).get("unresolved"))
    return (
        f"너는 주간 투자 히어로 앙상블 쇼츠의 한국어 내레이션 작가다. 계층 {tier}, 비트 {len(plan)}개.\n"
        f"[캐릭터]\n{persona}\n"
        f"[이번 회] 빌런={villain_kr(decision['villain'])}({decision['villain']}), 축={decision['axis']}, "
        f"공격형태={forms.get(decision['axis'], '')}, 주역 히어로={decision['hero_focus']}, "
        f"시즌 {decision['season_no']} {decision['season_week']}주차, 빌런 연속 {decision['villain_streak']}회.\n"
        f"[다음 회 복선] 빌런={decision['omen_villain']}, 축={decision['omen_axis']}. {payoff}\n"
        f"[직전 회차 미해결] {unresolved or '없음'}\n"
        f"[마무리 형식] {decision['ending_type']} — {ending.get('guide', '')}\n"
        f"[비트 계약]\n{beats}\n"
        "[narration 규칙] 각 비트 한 문장, 토큰 제외 28자 이하. 투자 권유·수익 보장·매수/매도 지시 금지. "
        "지어낸 수치 금지."
        f"{market_facts.prompt_contract(snapshot)}\n"
        "[situation_en 규칙] 영어 40단어 이하, 1액션 1카메라무브. 캐릭터 외형 묘사 금지(고정 블록이 따로 붙음). "
        "화면 텍스트·숫자·글자 금지.\n"
        f"[출력] JSON 배열만. 정확히 {len(plan)}개: "
        '[{"cut": 정수, "narration": "...", "situation_en": "..."}]'
    )


def _parse(raw: str) -> dict[int, dict]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if isinstance(data, dict):
        data = data.get("beats")
    out = {}
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            cut = int(item.get("cut"))
        except (TypeError, ValueError):
            continue
        out.setdefault(cut, item)
    return out


def check_beat(item: dict | None, snapshot: dict) -> tuple[str | None, str | None]:
    """(위반 사유 또는 None, 대입 완료 narration)."""
    if not item:
        return "missing_beat", None
    line = str(item.get("narration") or "").strip()
    sit = str(item.get("situation_en") or "").strip()
    if not line or not sit:
        return "empty_field", None
    reason = market_facts.check_line(line)
    if reason:
        return reason, None
    try:
        resolved = market_facts.resolve(line, snapshot)
    except Exception as e:  # noqa: BLE001 — 사유 반환(위반 처리)
        return f"resolve_failed:{type(e).__name__}", None
    if len(resolved) > MAX_RESOLVED_CHARS:
        return "narration_too_long", None
    if FORBIDDEN.search(resolved):
        return "forbidden_phrase", None
    if len(sit.split()) > MAX_SITUATION_WORDS:
        return "situation_too_long", None
    if re.search(r"\d", sit):
        return "situation_has_digit", None
    return None, resolved


def _evaluate(raw: str, plan, snapshot):
    parsed = _parse(raw)
    if len(parsed) != len(plan):
        logger.warning("script_beat_count_mismatch got=%d want=%d", len(parsed), len(plan))
    results = {}
    for cut in plan:
        item = parsed.get(cut["cut"])
        results[cut["cut"]] = (item, *check_beat(item, snapshot))
    return results


def build_script(tier: str, plan: list[dict], market_data: dict, decision: dict,
                 prev: dict | None, mode: str) -> dict:
    snapshot = market_data or {}
    canon = load_cast_canon()
    api_key = os.environ.get("GEMINI_API_KEY")
    if mode == "dry_run" or not api_key:
        logger.info("script_template reason=%s", "dry_run" if mode == "dry_run" else "no_key")
        return _template_script(plan, decision, snapshot, canon)

    try:
        client = _make_client(api_key)
        prompt = build_prompt(tier, plan, snapshot, decision, prev, canon)
        results = _evaluate(_call(client, prompt), plan, snapshot)
        bad = {c: r[1] for c, r in results.items() if r[1]}
        if bad:
            logger.warning("script_violation attempt=1 cuts=%s", sorted(bad))
            repair = prompt + "\n[위반 수정 요청] " + "; ".join(f"cut {c}: {r}" for c, r in sorted(bad.items())) \
                + " — 규칙을 지켜 전체 배열을 다시 출력하라."
            results = _evaluate(_call(client, repair), plan, snapshot)
    except Exception as e:  # noqa: BLE001 — 네트워크 등 전부 template 강등
        logger.warning("script_gemini_failed err=%s → template", type(e).__name__)
        return _template_script(plan, decision, snapshot, canon)

    beats, replaced = [], []
    for i, cut in enumerate(plan):
        item, reason, resolved = results[cut["cut"]]
        if reason:
            replaced.append(cut["cut"])
            beats.append(_template_beat(cut, i, decision, snapshot, canon))
        else:
            beats.append({"cut": cut["cut"], "role": cut["role"], "narration": resolved,
                          "situation_en": str(item["situation_en"]).strip()})
    if replaced:
        logger.warning("script_template_replaced cuts=%s", replaced)
    return {"version": SCRIPT_VERSION, "source": "gemini+template" if replaced else "gemini",
            "ending_type": decision["ending_type"], "beats": beats}
