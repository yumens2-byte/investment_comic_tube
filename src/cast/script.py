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

SCRIPT_VERSION = 3  # DN2026_0122: 비트별 characters(D6) 추가 / DN2026_0116: 첫 비트 30자·토큰, 나머지 24자
MAX_TEMPLATE_CHARS = 28        # 프롬프트 규칙: 토큰 제외 28자
# DN2026_0116: 실측 낭독 속도 초당 약 3.1자(shadow run 44133047259, 30자=9.68초) — 컷 최대 10초 기준
MAX_RESOLVED_CHARS = 30        # 지표 토큰이 있는 첫 비트(대입 후, 일간 토큰)
# DN2026_0123 A안: 주간 토큰('이번 주' 접두)일 때만 34자. DN2026_0121 실측(대사만 초당 4.2~5.8자) 기준 약 6~8초
MAX_RESOLVED_WEEK_CHARS = 34
WEEK_PREFIX = "이번 주"
MAX_PLAIN_CHARS = 24           # 그 외 비트(토큰 금지)
MAX_SITUATION_WORDS = 45
# DN2026_0115 통합: "사라" 단독은 "사라진다" 등 일반어 오탐 → 명령형 어미로 한정
# R6-05 #9: 매매 동사 + 명령형 어미까지 확장("담아라", "팔아", "사두세요", 문중 "사라,")
_END = r"(?=[\s.,!?…]|$)"
# R6-06 D1: '매수·매도' 단독 차단 복원(매수세·매도세는 시장 묘사라 허용), '던져라' 추가
# R6-06 D4: 어미 '아/어' 단독은 '담아 맞선다'류 오탐이라 제외(명령형 어미만)
FORBIDDEN = re.compile(
    r"매[수도](?!세)|수익\s*보장|무조건|확실한\s*수익"
    rf"|(?:사|팔|담|사\s*두|사\s*모|정리하)(?:라|아라|어라|세요|십시오){_END}"
    rf"|던져라{_END}|파세요{_END}"
    # R6-07 #1·#3: 반말 명령형('지금 팔아', '던져')은 문장 끝일 때만 차단 — '팔아 버린', '던져 맞혔다'는 허용
    # R4-09 W1·W2: 앞 글자 경계('부담'·'오른팔' 오탐 제거) + 문장부호 앞(한 줄 안 첫 문장 끝 명령형 포함)
    r"|(?<![가-힣])(?:팔|담|사\s*둬|던져)(?:아|어)?(?=[.!?…]|\s*$)")
# R6-05 #8: 자막 폰트 미지원 글리프(이모지 등)를 과금 전에 차단 — 한글·ASCII·기본 문장부호만
ALLOWED_CHARS = re.compile(r"^[가-힣ㄱ-ㅎㅏ-ㅣA-Za-z0-9_\s.,!?%&()'\"·…\-~:+/—–“”‘’]*$")  # R6-06 D5
AXIS_FACT_KEY = {"rates": "TNX", "volatility": "VIX", "momentum": "NASDAQ"}

try:
    from src.hooks import VILLAIN_KR
except Exception:  # noqa: BLE001  # pragma: no cover — hooks 부재 시 영문명
    VILLAIN_KR = {}

# DN2026_0122 D2: 시즌 4막 가이드(막별 비트 목적)
ACT_GUIDE = {
    "INTRO": "도입 — 빌런이 처음 모습을 드러내고 EDT가 원칙으로 맞선다",
    "RISE": "고조 — 첫 대결에서 밀리고, 동료가 합류해 다시 준비한다",
    "CLASH": "결전 — 빌런과 정면으로 맞붙는 가장 큰 전투",
    "FINALE": "결말 — 원칙으로 승리하고, 다음 시즌의 새 위협을 암시한다",
}

ROLE_PURPOSE = {
    "ESTABLISH_THREAT": "지표 사실 토큰 1개 + 빌런 등장",
    "ESCALATE": "위험이 도시 전체로 확산",
    "ENGAGE": "EDT가 원칙을 선언",
    "CRISIS": "최대 위기(숫자·토큰 금지)",
    "TURN": "hero_focus와 EDT의 협업으로 전환",
    "RESOLVE": "교훈 1개(비중·분산·손절·관망 중 하나), 영웅 포즈 금지",
    "NEXT_OMEN": "다음 위협(omen 빌런/축)의 그림자 암시, 숫자 금지, ending_type 형식",
    "AFTERMATH_CALM": "폭풍이 지나간 시장의 고요(첫 비트면 지표 사실 토큰 1개)",
    "REGROUP": "EDT가 원칙을 재점검하며 다음을 준비",
}

# ---------- DN2026_0122 D5: 템플릿 후보 풀(역할·막·화면 히어로별 4~6개) ----------
# 규칙: 숫자·토큰 없음, {v}=빌런 한글명(조사 포함 형태는 _subj), 인용형(“…”) 비트는 캐릭터 대사.
# 길이는 test_script_pools 가 전 조합(빌런 3종 × 복선 접두)을 대입해 24자 이하를 검사한다.
_ESCALATE_POOL = {   # R6-12 (f): 복선 접두("예상 밖, " 6자) 포함 24자 → 본문 18자 이하
    "rates": ["빚의 무게가 도시로 번진다", "이자의 사슬이 거리를 조인다",
              "무거워진 공기가 시장을 누른다", "버티던 빌딩들이 하나둘 기운다"],
    "volatility": ["흔들림이 시장 곳곳으로 번진다", "번개가 치고 전광판이 깜빡인다",
                   "공포가 거리마다 퍼져 나간다", "어디서 칠지 아무도 모른다"],
    "momentum": ["과열된 질주가 거리를 휩쓴다", "멈추지 않는 속도에 도시가 끓는다",
                 "모두가 같은 방향으로 내달린다", "열기가 빌딩 숲을 삼킨다"],
}
_ESCALATE_HERO = {   # D6: 화면 히어로 시점(T3=LEV-01, T2=FG-01)
    "LEV-01": ["“크게 오르면 크게 떨어진다”", "과신한 질주가 먼저 꺾인다",
               "레버리지 전사가 경고를 외친다"],
    "FG-01": ["“노출부터 다시 재야 해”", "계산하던 손이 잠시 멈춘다",
              "위험 노출이 한계선에 닿는다"],
}
_RECAP = {   # D3: 직전 회차 이어받기(ESCALATE 대체 후보, 복선 회수가 없을 때)
    "same": [lambda pv, v: f"{_subj(v)} 다시 돌아왔다", lambda pv, v: f"{v}의 공세가 계속된다"],
    "diff": [lambda pv, v: f"{pv} 대신 {_subj(v)} 왔다",
             lambda pv, v: f"{pv} 다음은 {v}"],
}
_ENGAGE_POOL = {
    "INTRO": ["EDT가 말한다. 원칙이 먼저다", "“흔들려도 원칙은 그대로다”", "EDT가 전장 앞에 선다",
              "“겁먹지 마. 기준부터 세운다”", "EDT가 낮게 으르렁거린다"],
    "RISE": ["“지난번처럼 서두르지 않는다”", "EDT가 지난 교훈을 꺼낸다", "EDT가 다시 방패를 든다",
             "“같은 실수는 두 번 없다”", "상처를 딛고 EDT가 일어선다"],
    "CLASH": ["“이번엔 끝까지 버틴다”", "EDT가 정면으로 맞선다", "결전의 날, EDT가 나선다",
              "“여기서 물러서지 않는다”", "EDT가 마지막 방어선에 선다"],
    "FINALE": ["“마지막까지 원칙으로 간다”", "EDT가 마지막 전장에 선다", "EDT가 시즌의 끝에 선다",
               "“끝은 원칙이 정한다”", "EDT가 남은 힘을 모은다"],
}
_CRISIS_POOL = {
    "INTRO": ["{s} 방어선을 무너뜨린다", "{s} 첫 일격을 날린다", "{s} 도시의 숨통을 죈다",
              "첫 충돌에 동료가 튕겨 나간다"],
    "RISE": ["{s} 동료를 밀어낸다", "방어선이 뚫리고 동료가 쓰러진다", "{s} 한 발 더 밀고 들어온다",
             "버티던 동료가 무릎을 꿇는다"],
    "CLASH": ["{s} 전력을 다해 덮친다", "모든 방어선이 한꺼번에 흔들린다", "{s} 최후의 공격을 퍼붓는다",
              "도시 한복판에서 정면충돌한다"],
    "FINALE": ["{s} 마지막 발악을 한다", "{s} 모든 힘을 끌어모은다", "무너질 듯한 순간이 찾아온다",
               "마지막 일격이 도시를 가른다"],
}
_TURN_POOL = {
    "EDT": ["EDT가 원칙으로 흐름을 되돌린다", "“비중부터 줄인다. 그게 시작이다”", "EDT가 한 걸음씩 되찾는다",
            "“서두르지 않는다. 나눠서 간다”"],
    "FG-01": ["노출을 잰 동료와 EDT가 반격한다", "“헤지는 내가 맡을게”", "균형을 잡은 둘이 다시 일어선다",
              "“위험은 내가 재고, 넌 버텨”"],
    "LEV-01": ["과신을 버린 동료가 EDT 곁에 선다", "“이번엔 힘을 아껴 쓴다”", "속도를 줄인 둘이 반격한다",
               "“큰 힘일수록 조심히 쓴다”"],
}
_RESOLVE_POOL = {   # D2: 막별 결말 톤(고조=패배 인정, 결말=승리), 교훈 1개
    "rates": {"default": ["무게를 버티는 힘은 분산이다", "빚 앞에서는 분산이 방패다", "한곳에 몰지 않으면 버틴다"],
              "RISE": ["이번엔 물러섰다. 분산을 다시 짠다"], "FINALE": ["분산으로 버틴 도시가 다시 선다"]},
    "volatility": {"default": ["흔들릴수록 비중 관리가 먼저다", "공포 앞에서는 비중을 줄인다", "번개는 지나가고 비중은 남는다"],
                   "RISE": ["이번엔 밀렸다. 비중부터 다시 본다"], "FINALE": ["비중을 지킨 쪽이 끝까지 남았다"]},
    "momentum": {"default": ["과열 앞에서는 관망도 전략이다", "달릴 때일수록 손절선을 정한다", "열기가 식을 때까지 지켜본다"],
                 "RISE": ["이번엔 놓쳤다. 손절선부터 긋는다"], "FINALE": ["관망을 지킨 쪽이 살아남았다"]},
}
_REGROUP_POOL = ["EDT가 다시 원칙을 점검한다", "조용한 새벽, EDT가 숨을 고른다",
                 "“다음을 위해 지금 정리한다”", "EDT가 지난 전투를 되짚는다"]
_AFTERMATH_PLAIN = ["폭풍이 지나고 시장이 숨을 고른다", "잔해 위로 고요가 내려앉는다", "소음이 멎고 도시가 숨을 쉰다"]
_SHADOW = {"rates": "금리의 그림자", "volatility": "변동성의 그림자", "momentum": "과열의 그림자"}
_OMEN = {"QUESTION": lambda s: f"{s}{particle_for(s, '는')} 어디로 향할까",
         "DECLARE": lambda s: f"{_subj(s)} 온다. 준비는 끝났다",
         "TEASE": lambda s: f"다음 주, {_subj(s)} 깨어난다",
         "TWIST": lambda s: f"그런데 진짜 위협은 {s}였다"}


def _FINALE_OMEN(s: str) -> str:   # D2 시즌 마지막 회
    return f"다음 시즌, {_subj(s)} 깨어난다"


_PAYOFF = {"hit": "예고대로 ", "twist": "예상 밖, "}
# DN2026_0122: 영상 캐논 빌런 한글명(Notion 캐논에 한글 표기 없음 — 일간 라인 표기 규칙(붙여쓰기 음역) 준용)
CAST_VILLAIN_KR = {"Volatility Hydra": "볼래틸리티히드라", "Algorithm Reaper": "알고리즘리퍼"}


def villain_kr(villain: str) -> str:
    return CAST_VILLAIN_KR.get(villain) or VILLAIN_KR.get(villain, villain)


def _subj(word: str) -> str:
    return word + particle_for(word, "이")


def _is_week_token(line: str, snapshot: dict) -> bool:
    """줄의 수치 토큰이 모두 주간 누적 지표인지(R6-13 치명: 지표 단위 판정)."""
    tokens = list(market_facts.TOKEN.finditer(line or ""))
    return bool(tokens) and all(m[2] == "change_pct" and (snapshot.get(m[1]) or {}).get("change_basis") == "week"
                                for m in tokens)


def fact_token(axis: str, snapshot: dict) -> str | None:
    """축 대표키 change_pct 토큰 → SPX → None(숫자 없는 문장)."""
    for key in (AXIS_FACT_KEY.get(axis), "SPX"):
        if key and (snapshot.get(key) or {}).get("change_pct") is not None:
            return f"{{{{FACT:{key}.change_pct}}}}"
    return None


_FORM_KR = {"rates": "", "volatility": "혼돈의 ", "momentum": "과열된 "}


def _seed(decision: dict, role: str) -> int:
    """D5: 시즌·주차·역할 결정 시드(재실행 재현성 — 같은 결정이면 같은 문장)."""
    import hashlib
    key = f"{decision.get('season_no')}|{decision.get('season_week')}|{role}|{decision.get('villain')}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


def _pick(pool: list[str], decision: dict, role: str, banned: set[str]) -> str:
    """D5: 시드 순환 순서로 금지 목록(직전 2회 대사)에 없는 첫 후보. 전부 금지면 시드 후보."""
    start = _seed(decision, role) % len(pool)
    order = pool[start:] + pool[:start]
    for line in order:
        if line not in banned:
            return line
    logger.info("template_pool_exhausted role=%s", role)   # R6-12 제안
    return order[0]


def banned_lines(decision: dict) -> set[str]:
    """R6-12 (b): prev_lines = [[직전 회], [2회 전]] (옛 형식 평면 목록도 허용) → 금지 문장 집합."""
    out: set[str] = set()
    for item in decision.get("prev_lines") or []:
        if isinstance(item, list):
            out.update(str(x) for x in item if x)
        elif item:
            out.add(str(item))
    return out


def _hero_on_screen(cut: dict | None) -> str | None:
    for c in (cut or {}).get("characters") or []:
        if c in HEROES and c != "EDT":
            return c
    return None


def template_line(role: str, index: int, decision: dict, snapshot: dict, cut: dict | None = None) -> str:
    """토큰 포함 원문(대입 전). index=0 이면 지표 사실 토큰을 넣는다. DN2026_0122 D2·D3·D5·D6."""
    v = villain_kr(decision["villain"])
    axis = decision["axis"]
    act = decision.get("act") or "INTRO"
    banned = banned_lines(decision)
    payoff = _PAYOFF.get(decision.get("omen_payoff") or "", "")
    token = fact_token(axis, snapshot) if index == 0 else None
    if token and _is_week_token(token, snapshot):
        token = f"{WEEK_PREFIX} {token}"   # DN2026_0123 D4 · R6-13: 그 지표 값이 주간 누적일 때만
    if role == "ESTABLISH_THREAT":
        # R4-06: 게이트 대체(캐논 빌런 부재로 Debt Titan)면 축의 공격 형태를 밝혀 지표와 빌런을 맞춘다
        gated = decision["villain"] == "Debt Titan" and axis != "rates"
        form = _FORM_KR.get(axis, "") if gated else ""
        return f"{token}, {form}{v} 등장" if token else f"{_subj(form + v)} 도시를 덮쳤다"
    if role == "AFTERMATH_CALM":
        return f"{token}, 숨 고르는 시장" if token else _pick(_AFTERMATH_PLAIN, decision, role, banned)
    if role == "ESCALATE":
        if payoff:
            return _pick([payoff + x for x in _ESCALATE_POOL[axis]], decision, role, banned)
        pool = list(_ESCALATE_POOL[axis]) + list(_ESCALATE_HERO.get(_hero_on_screen(cut) or "", []))
        recap = decision.get("prev_recap") or {}
        if recap.get("villain"):
            pv = villain_kr(recap["villain"])
            kind = "same" if recap["villain"] == decision["villain"] else "diff"
            pool += [f(pv, v) for f in _RECAP[kind]]
        return _pick(pool, decision, role, banned)
    if role == "ENGAGE":
        return _pick(_ENGAGE_POOL.get(act, _ENGAGE_POOL["INTRO"]), decision, role, banned)
    if role == "CRISIS":
        pool = [t.format(s=_subj(v)) for t in _CRISIS_POOL.get(act, _CRISIS_POOL["INTRO"])]
        return _pick(pool, decision, role, banned)
    if role == "TURN":
        return _pick(_TURN_POOL.get(decision.get("hero_focus"), _TURN_POOL["EDT"]), decision, role, banned)
    if role == "RESOLVE":
        pools = _RESOLVE_POOL[axis]
        return _pick(pools.get(act, []) + pools["default"] if act in pools else pools["default"],
                     decision, role, banned)
    if role == "REGROUP":
        return _pick(_REGROUP_POOL, decision, role, banned)
    if role == "NEXT_OMEN":
        shadow = _SHADOW[decision.get("omen_axis") or axis]
        if act == "FINALE":
            return _FINALE_OMEN(shadow)
        ending = _OMEN.get(str(decision.get("ending_type") or "").upper(), _OMEN["TEASE"])
        return ending(shadow)
    raise ValueError(f"미정의 비트 역할 '{role}'")


def _villain_in(cut: dict) -> bool:
    return any(c not in HEROES for c in cut.get("characters") or [])


# DN2026_0122 D6: 화면 캐릭터를 situation_en 에 이름으로 명시(대사·화면 일치 검사 기준)
SCREEN_NAME_EN = {"EDT": "EDT", "FG-01": "Exposure Futures Girl", "LEV-01": "Leverage Muscle Man"}


def screen_names(characters: list[str]) -> list[str]:
    return [SCREEN_NAME_EN.get(c, c) for c in characters or []]


def missing_on_screen(situation: str, characters: list[str]) -> list[str]:
    """D6: situation_en 에 이름이 없는 화면 캐릭터(대소문자 무시). 빌런은 이름 또는 'villain' 허용."""
    low = (situation or "").lower()
    out = []
    for c in characters or []:
        name = SCREEN_NAME_EN.get(c, c).lower()
        if name in low or (c not in HEROES and "villain" in low):
            continue
        out.append(c)
    return out


def template_situation(cut: dict, decision: dict, canon: dict) -> str:
    base = ROLE_SITUATION.get(cut["role"], "")
    if _villain_in(cut):
        omen = cut["role"] == "NEXT_OMEN"
        name = decision["omen_villain"] if omen else decision["villain"]
        forms = (canon["characters"].get(name) or {}).get("attack_forms_en") or {}
        form = forms.get(decision["omen_axis"] if omen else decision["axis"])
        if form:
            base = f"{base} The villain is {form}."
    names = screen_names(cut.get("characters") or [])
    return f"On screen: {', '.join(names)}. {base}" if names else base


def _template_beat(cut: dict, index: int, decision: dict, snapshot: dict, canon: dict) -> dict:
    raw = template_line(cut["role"], index, decision, snapshot, cut)
    return {"cut": cut["cut"], "role": cut["role"],
            "narration": market_facts.resolve(raw, snapshot),
            "situation_en": template_situation(cut, decision, canon),
            "characters": list(cut.get("characters") or [])}


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
    payoff = {"hit": "지난 회 예고가 적중 — 2번 비트에 '예고대로'를 넣는다.",   # DN2026_0116 R6-03
              "twist": "지난 회 예고가 빗나감 — 2번 비트에 '예상 밖'을 넣는다."}.get(
        decision.get("omen_payoff") or "", "지난 회 예고 없음.")
    beats = "\n".join(f"- cut {c['cut']} {c['role']} (기본 출연: {', '.join(c['characters'])}): "
                      f"{ROLE_PURPOSE.get(c['role'], '')}" for c in plan)
    unresolved = decision.get("prev_unresolved") or (((prev or {}).get("story_state") or {}).get("unresolved"))
    act = decision.get("act") or "INTRO"
    recap = decision.get("prev_recap") or {}
    recap_txt = ("없음" if not recap else
                 f"S{recap.get('season_no')}W{recap.get('season_week')}({recap.get('act')}) 빌런="
                 f"{villain_kr(recap.get('villain') or '')}, 전환='{recap.get('turn') or ''}', "
                 f"교훈='{recap.get('lesson') or ''}', 예고='{recap.get('omen') or ''}'")
    banned = sorted(banned_lines(decision))
    hero_names = ", ".join(f"{k}={SCREEN_NAME_EN[k]}" for k in sorted(HEROES))
    return (
        f"너는 주간 투자 히어로 앙상블 쇼츠의 한국어 내레이션 작가다. 계층 {tier}, 비트 {len(plan)}개.\n"
        f"[캐릭터]\n{persona}\n"
        f"[이번 회] 빌런={villain_kr(decision['villain'])}({decision['villain']}), 축={decision['axis']}, "
        f"공격형태={forms.get(decision['axis'], '')}, 주역 히어로={decision['hero_focus']}, "
        f"시즌 {decision['season_no']} {decision['season_week']}주차, 빌런 연속 {decision['villain_streak']}회.\n"
        f"[다음 회 복선] 빌런={decision['omen_villain']}, 축={decision['omen_axis']}. {payoff}\n"
        f"[직전 회차 미해결] {unresolved or '없음'}\n"
        f"[지난 회 요약] {recap_txt} — 2·3번 비트 중 하나가 지난 회를 이어받는다(복선 회수가 있으면 그 비트).\n"
        f"[시즌 막] {act} — {ACT_GUIDE.get(act, '')}\n"
        f"[금지 대사] 아래 문장과 같은 문장 금지(직전 2회): {' / '.join(banned) if banned else '없음'}\n"
        f"[마무리 형식] {decision['ending_type']} — {ending.get('guide', '')}\n"
        f"[비트 계약]\n{beats}\n"
        "[narration 규칙] 각 비트 한 문장. 해설과 캐릭터 대사(“…” 인용형)를 섞고 인용형은 2개 비트. "
        "같은 회차 안에서 같은 문장 반복 금지. 지표 토큰은 1번 비트에만 1개(대입 후 30자 이하, 주간 지표 토큰이면 '이번 주' 포함 34자 이하), "
        "나머지 비트는 숫자·토큰 없이 24자 이하(낭독 시간 제한). 복선 회수(예고대로/예상 밖)는 2번 비트에. "
        "투자 권유·수익 보장·매수/매도 지시 금지. "
        "지어낸 수치 금지."
        f"{market_facts.prompt_contract(snapshot)}{decision.get('weekly_note') or ''}\n"
        "[situation_en 규칙] 영어 40단어 이하, 1액션 1카메라무브. 캐릭터 외형 묘사 금지(고정 블록이 따로 붙음). "
        "화면 텍스트·숫자·글자 금지. characters 의 모든 캐릭터를 영문 이름으로 반드시 언급"
        f"({hero_names}, 빌런은 영문 이름).\n"
        "[characters 규칙] 비트별 화면 캐릭터 1~2명. 히어로는 EDT·FG-01·LEV-01 중에서, "
        "기본 출연에 빌런이 있으면 그 빌런을 반드시 포함하고 없으면 빌런 금지. TURN 은 기본 출연 그대로. "
        "narration 이 말하는 인물이 화면에 나오게 고른다.\n"
        f"[출력] JSON 배열만. 정확히 {len(plan)}개: "
        '[{"cut": 정수, "narration": "...", "situation_en": "...", "characters": ["..."]}]'
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


def confirmed_heroes() -> set[str]:
    from .casting import REF_CONFIRMED_PREFIX, _canon_ref_status, _load_canon
    canon = _load_canon()
    return {h for h in HEROES if _canon_ref_status(canon, h).startswith(REF_CONFIRMED_PREFIX)}


def resolve_characters(item: dict | None, cut: dict) -> list[str]:
    """DN2026_0122 D6: 대본이 고른 화면 캐릭터. 규칙 위반·누락이면 기본 출연(cut)으로 되돌린다."""
    base = list(cut.get("characters") or [])
    raw = (item or {}).get("characters")
    if cut.get("role") == "TURN" or not isinstance(raw, list):
        return base
    # DN2026_0123 shadow 45135210910: Gemini 가 코드 대신 영문 이름(Leverage Muscle Man)을 돌려줘 폴백 — 이름도 코드로
    by_name = {v.lower(): k for k, v in SCREEN_NAME_EN.items()}
    picked = list(dict.fromkeys(by_name.get(str(c).strip().lower(), str(c).strip())
                                for c in raw if str(c).strip()))
    villains = [c for c in base if c not in HEROES]
    heroes_ok = confirmed_heroes()   # R6-12 (d): REF CONFIRMED 히어로만(cast_for_tier fail-closed 이후 변경이므로)
    ok = (1 <= len(picked) <= 2
          and all(c in heroes_ok or c in villains for c in picked)
          and all(v in picked for v in villains))
    if not ok:
        logger.info("script_cast_fallback cut=%s picked=%s", cut.get("cut"), picked)
        return base
    return picked


def check_beat(item: dict | None, snapshot: dict, allow_token: bool = True,
               banned: set[str] | None = None,
               characters: list[str] | None = None) -> tuple[str | None, str | None]:
    """(위반 사유 또는 None, 대입 완료 narration). allow_token=False 비트는 토큰 금지·24자(DN2026_0116).
    DN2026_0122: banned(직전 2회 대사) 중복 → duplicate_line, characters 이름이 situation_en 에 없으면
    situation_missing_character."""
    if not item:
        return "missing_beat", None
    line = str(item.get("narration") or "").strip()
    sit = str(item.get("situation_en") or "").strip()
    if not line or not sit:
        return "empty_field", None
    reason = market_facts.check_line(line)
    if reason:
        return reason, None
    if not allow_token and market_facts.TOKEN.search(line):
        return "token_not_allowed", None
    try:
        resolved = market_facts.resolve(line, snapshot)
    except Exception as e:  # noqa: BLE001 — 사유 반환(위반 처리)
        return f"resolve_failed:{type(e).__name__}", None
    # DN2026_0123 · R6-13: '이번 주'는 주간 누적 토큰과만, 34자도 그때만
    week = allow_token and _is_week_token(line, snapshot)
    has_token = bool(market_facts.TOKEN.search(line))
    if has_token and week and not resolved.startswith(WEEK_PREFIX):
        return "week_prefix_missing", None
    if has_token and not week and WEEK_PREFIX in resolved:
        return "week_prefix_on_daily", None
    limit = (MAX_RESOLVED_WEEK_CHARS if week else MAX_RESOLVED_CHARS) if allow_token else MAX_PLAIN_CHARS
    if len(resolved) > limit:
        return "narration_too_long", None
    if FORBIDDEN.search(resolved):
        return "forbidden_phrase", None
    if not ALLOWED_CHARS.match(resolved):
        return "unsupported_char", None
    if len(sit.split()) > MAX_SITUATION_WORDS:
        return "situation_too_long", None
    if re.search(r"\d", sit):
        return "situation_has_digit", None
    if banned and resolved in banned:
        return "duplicate_line", None
    if characters is not None and missing_on_screen(sit, characters):
        return "situation_missing_character", None
    return None, resolved


def _evaluate(raw: str, plan, snapshot, banned: set[str] | None = None):
    parsed = _parse(raw)
    if len(parsed) != len(plan):
        logger.warning("script_beat_count_mismatch got=%d want=%d", len(parsed), len(plan))
    results = {}
    first = plan[0]["cut"] if plan else None
    seen: set[str] = set(banned or ())
    for cut in plan:
        item = parsed.get(cut["cut"])
        chars = resolve_characters(item, cut)
        reason, resolved = check_beat(item, snapshot, allow_token=(cut["cut"] == first),
                                      banned=seen, characters=chars)
        if resolved:
            seen.add(resolved)   # D5: 같은 회차 안 반복도 금지
        results[cut["cut"]] = (item, reason, resolved, chars)
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
        banned = banned_lines(decision)
        results = _evaluate(_call(client, prompt), plan, snapshot, banned)
        bad = {c: r[1] for c, r in results.items() if r[1]}
        if bad:
            # DN2026_0123: 사유 코드(내부 상수, 본문 아님)를 남겨 위반 원인을 운영 로그로 추적
            logger.warning("script_violation attempt=1 cuts=%s reasons=%s", sorted(bad),
                           {c: str(r).split(":")[0] for c, r in sorted(bad.items())})
            repair = prompt + "\n[위반 수정 요청] " + "; ".join(f"cut {c}: {r}" for c, r in sorted(bad.items())) \
                + " — 규칙을 지켜 전체 배열을 다시 출력하라."
            try:
                second = _evaluate(_call(client, repair), plan, snapshot, banned)
            except Exception as e:  # noqa: BLE001 — 2차 실패는 1차 유효 비트 유지
                logger.warning("script_repair_failed err=%s", type(e).__name__)
                second = {}
            # R6-05 #12: 비트 단위 병합 — 2차 유효 > 1차 유효 > 템플릿
            results = {c: (second[c] if c in second and not second[c][1] else first)
                       for c, first in results.items()}
    except Exception as e:  # noqa: BLE001 — 네트워크 등 전부 template 강등
        logger.warning("script_gemini_failed err=%s → template", type(e).__name__)
        return _template_script(plan, decision, snapshot, canon)

    beats, replaced = [], []
    used: set[str] = set()
    for i, cut in enumerate(plan):
        item, reason, resolved, chars = results[cut["cut"]]
        if not reason and resolved in used:   # R6-12 (c): 병합 후 회차 안 중복 재검사
            reason = "duplicate_line_after_merge"
        if reason:
            replaced.append(cut["cut"])
            beats.append(_template_beat(cut, i, decision, snapshot, canon))
        else:
            beats.append({"cut": cut["cut"], "role": cut["role"], "narration": resolved,
                          "situation_en": str(item["situation_en"]).strip(), "characters": chars})
        used.add(beats[-1]["narration"])
    if replaced:
        logger.warning("script_template_replaced cuts=%s reasons=%s", replaced,
                       {c: str(results[c][1]).split(":")[0] for c in replaced if results[c][1]}
                       | {c: "duplicate_line_after_merge" for c in replaced if not results[c][1]})
    return {"version": SCRIPT_VERSION, "source": "gemini+template" if replaced else "gemini",
            "ending_type": decision["ending_type"], "beats": beats}
