"""script 테스트 — 가짜 genai 클라이언트, 네트워크 호출 없음 (DN2026_0115)."""
import itertools
import json

import pytest

import src.cast.script as C
from src import market_facts
from src.cast.casting import cast_for_tier

SNAP = {"TNX": {"close": 4.41, "change_pct": 12.34}, "VIX": {"close": 21.3, "change_pct": -12.34},
        "NASDAQ": {"close": 18000, "change_pct": -2.71}, "SPX": {"close": 5800, "change_pct": -1.05},
        "DXY": {"close": 104, "change_pct": 0.1}}


def decision(**kw):
    d = {"villain": "Debt Titan", "villain_raw": "Chaos Reaper", "axis": "volatility",
         "villain_scores": {}, "omen_villain": "Debt Titan", "omen_axis": "rates", "hero_focus": "FG-01",
         "season_no": 1, "season_week": 2, "villain_streak": 1, "ending_type": "TEASE",
         "omen_payoff": None, "prev_unresolved": None}
    d.update(kw)
    return d


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []
        self.models = self

    def generate_content(self, model, contents, config):
        assert model == C.STORY_MODEL and config["response_mime_type"] == "application/json"
        self.prompts.append(contents)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return FakeResp(r)


def good_beats(plan, **override):
    # DN2026_0122 D6: situation_en 에 화면 캐릭터 이름 필수
    beats = [{"cut": c["cut"], "narration": f"{c['role']} 장면이 이어진다"[:20],
              "situation_en": f"{' and '.join(C.screen_names(c['characters']))} turn toward the storm, slow push in."}
             for c in plan]
    beats[0]["narration"] = "{{FACT:VIX.change_pct}}, 혼돈이 시작됐다"
    for cut, nar in override.items():
        beats[int(cut[1:]) - 1]["narration"] = nar
    return json.dumps(beats, ensure_ascii=False)


@pytest.fixture
def t3():
    return cast_for_tier("T3")


@pytest.fixture
def client(monkeypatch):
    holder = {}

    def install(responses):
        holder["c"] = FakeClient(responses)
        monkeypatch.setattr(C, "_make_client", lambda key: holder["c"])
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        return holder["c"]
    return install


def test_dry_run_template_no_external_call(monkeypatch, t3):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(C, "_make_client", lambda key: pytest.fail("외부 호출 금지"))
    s = C.build_script("T3", t3, SNAP, decision(), None, "dry_run")
    assert s["source"] == "template" and s["version"] == C.SCRIPT_VERSION and s["ending_type"] == "TEASE"
    assert [b["cut"] for b in s["beats"]] == [c["cut"] for c in t3]
    assert [b["role"] for b in s["beats"]] == [c["role"] for c in t3]
    assert s["beats"][0]["narration"].startswith("VIX 12.34% 하락")
    assert "{{" not in json.dumps(s, ensure_ascii=False)


def test_no_key_uses_template(monkeypatch, t3):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(C, "_make_client", lambda key: pytest.fail("외부 호출 금지"))
    assert C.build_script("T3", t3, SNAP, decision(), None, "live")["source"] == "template"


AXES = ("rates", "volatility", "momentum")
ENDINGS = ("QUESTION", "DECLARE", "TEASE", "TWIST")
FOCUS = ("EDT", "FG-01", "LEV-01")
PAYOFF = (None, "hit", "twist")


VILLAINS = ("Debt Titan", "Volatility Hydra", "Algorithm Reaper")   # DN2026_0122
ACTS = ("INTRO", "RISE", "CLASH", "FINALE")
RECAPS = (None, {"villain": "Debt Titan"}, {"villain": "Volatility Hydra"}, {"villain": "Algorithm Reaper"})


@pytest.mark.parametrize("tier", ["T1", "T2", "T3"])
@pytest.mark.parametrize("snap", [SNAP, {"SPX": {"close": 1, "change_pct": 0.5}}, {}])
def test_all_templates_within_limits(tier, snap):
    plan = cast_for_tier(tier)
    combos = itertools.product(AXES, AXES, ENDINGS, FOCUS, PAYOFF, VILLAINS, ACTS, RECAPS)
    for axis, omen, ending, focus, payoff, villain, act, recap in combos:
        if villain not in ("Debt Titan", {"rates": "Debt Titan", "volatility": "Volatility Hydra",
                                          "momentum": "Algorithm Reaper"}[axis]):
            continue   # 화면 빌런은 축의 캐논 빌런 또는 게이트 대체(Debt Titan)뿐
        if snap is not SNAP and (act, recap) != ("INTRO", None):
            continue   # 빈 스냅샷 조합은 기본 막만(실행 시간)
        d = decision(axis=axis, omen_axis=omen, ending_type=ending, hero_focus=focus, omen_payoff=payoff,
                     villain=villain, act=act, prev_recap=recap)
        for i, cut in enumerate(plan):
            raw = C.template_line(cut["role"], i, d, snap, cut)
            assert market_facts.check_line(raw) is None, raw
            assert len(market_facts.TOKEN.sub("", raw)) <= C.MAX_TEMPLATE_CHARS, raw
            resolved = market_facts.resolve(raw, snap)
            assert len(resolved) <= (C.MAX_RESOLVED_CHARS if i == 0 else C.MAX_PLAIN_CHARS), resolved   # DN2026_0116
            if i > 0:
                assert not market_facts.TOKEN.search(raw), raw
            assert not C.FORBIDDEN.search(resolved), resolved
            sit = C.template_situation(cut, d, C.load_cast_canon())
            assert len(sit.split()) <= C.MAX_SITUATION_WORDS and not any(ch.isdigit() for ch in sit)


def test_template_fact_fallbacks():
    assert C.fact_token("rates", SNAP) == "{{FACT:TNX.change_pct}}"
    assert C.fact_token("momentum", {"SPX": {"change_pct": 1.0}}) == "{{FACT:SPX.change_pct}}"
    assert C.fact_token("rates", {}) is None


def test_template_payoff_and_villain_attack_form(t3):
    s = C._template_script(t3, decision(omen_payoff="hit"), SNAP, C.load_cast_canon())
    assert s["beats"][1]["narration"].startswith("예고대로 ")   # DN2026_0116: 회수 문구는 2번 비트
    assert "flickering chaotic shadows" in s["beats"][0]["situation_en"]     # volatility attack form
    assert "heavy chains of debt" in s["beats"][-1]["situation_en"]          # omen=rates
    assert "attack" not in s["beats"][1]["situation_en"]


def test_gemini_ok(client, t3):
    c = client([good_beats(t3)])
    s = C.build_script("T3", t3, SNAP, decision(), None, "live")
    assert s["source"] == "gemini" and len(c.prompts) == 1
    assert s["beats"][0]["narration"] == "VIX 12.34% 하락, 혼돈이 시작됐다"
    assert [b["cut"] for b in s["beats"]] == [1, 2, 3, 4, 5, 6, 7]
    p = c.prompts[0]
    assert market_facts.prompt_contract(SNAP) in p
    assert "원칙의 수호자" in p and "splitting into flickering" in p and "24자" in p


def test_gemini_violation_retries_once(client, t3):
    c = client([good_beats(t3, c3="금리 5% 돌파한다"), good_beats(t3)])
    s = C.build_script("T3", t3, SNAP, decision(), None, "shadow")
    assert s["source"] == "gemini" and len(c.prompts) == 2
    assert "cut 3:" in c.prompts[1]


def test_gemini_second_violation_template_replaces_beat(client, t3):
    bad = good_beats(t3, c6="지금 바로 매수하세요")
    c = client([bad, bad])
    s = C.build_script("T3", t3, SNAP, decision(), None, "live")
    assert s["source"] == "gemini+template" and len(c.prompts) == 2
    assert s["beats"][5]["narration"] == C.template_line("RESOLVE", 5, decision(), SNAP, t3[5])
    assert s["beats"][5]["narration"] in C._RESOLVE_POOL["volatility"]["default"]
    assert s["beats"][1]["narration"].startswith("ESCALATE")


def test_gemini_exception_full_template(client, t3, caplog):
    client([ConnectionError("secret body test-key")])
    with caplog.at_level("WARNING", logger="cast.script"):
        s = C.build_script("T3", t3, SNAP, decision(), None, "live")
    assert s["source"] == "template"
    assert "ConnectionError" in caplog.text and "test-key" not in caplog.text and "secret body" not in caplog.text


def test_gemini_beat_count_mismatch(client, t3):
    short = json.dumps(json.loads(good_beats(t3))[:5], ensure_ascii=False)
    c = client([short, short])
    s = C.build_script("T3", t3, SNAP, decision(), None, "live")
    assert len(s["beats"]) == 7 and s["source"] == "gemini+template"
    assert s["beats"][6]["narration"] == "다음 주, 금리의 그림자가 깨어난다"
    assert "cut 6: missing_beat" in c.prompts[1]


def test_gemini_invalid_json_then_ok(client, t3):
    c = client(["not json", json.dumps({"beats": json.loads(good_beats(t3))})])
    assert C.build_script("T3", t3, SNAP, decision(), None, "live")["source"] == "gemini"
    assert len(c.prompts) == 2


@pytest.mark.parametrize("line,reason", [
    ("무조건 오른다", "forbidden_phrase"),
    ("확실한 수익이 기다린다", "forbidden_phrase"),
    ("지금 팔아라", "forbidden_phrase"),
    ("금리가 세 배로 뛴다", "unregistered_market_numeric_claim"),
    ("{{FACT:OIL.change_pct}} 기름이 끓는다", "resolve_failed:ValidationError"),
    ("가" * 37, "narration_too_long:37>30자"),
])
def test_check_beat_blocks(line, reason):
    item = {"cut": 1, "narration": line, "situation_en": "A calm push in."}
    assert C.check_beat(item, SNAP)[0] == reason


def test_check_beat_situation_rules():
    assert C.check_beat({"narration": "고요하다", "situation_en": "word " * 46}, SNAP)[0] == "situation_too_long"
    assert C.check_beat({"narration": "고요하다", "situation_en": "Shot 2 of city"}, SNAP)[0] == "situation_has_digit"
    assert C.check_beat(None, SNAP)[0] == "missing_beat"


@pytest.mark.parametrize("text,bad", [("안개가 사라진다", False), ("지금 사라", True), ("당장 사세요", True),
                                      ("수익 보장", True), ("손절도 전략이다", False)])
def test_forbidden_words_precise(text, bad):
    """DN2026_0115 통합 — '사라' 단독 오탐 제거, 명령형만 차단."""
    from src.cast.script import FORBIDDEN
    assert bool(FORBIDDEN.search(text)) is bad



@pytest.mark.parametrize("text,bad", [
    ("매수 기회다", True), ("매도 신호가 켜졌다", True), ("매수세가 거세다", False), ("매도세가 꺾였다", False),
    ("던져라", True), ("던져", True), ("지금 팔아", True), ("지금 팔아!", True), ("공을 던져 맞혔다", False),
    ("던져진 주사위", False), ("매수 대기", True), ("매수세에", False),
    ("금리 부담", False), ("이자 부담.", False), ("오른팔", False), ("지금 팔아. 그리고 본다", True), ("담아라", True), ("사두세요", True), ("정리하라", True),
    ("원칙을 담아 맞선다", False), ("공포에 팔아 버린 개미들", False), ("사라진다", False),
    ("손절도 원칙이다", False), ("VIX 18.70% 상승", False)])
def test_forbidden_r6_06(text, bad):
    """R6-06 D1·D4 — 단독 매수/매도 차단 복원, 던져라 추가, '아/어' 단독 오탐 제거."""
    from src.cast.script import FORBIDDEN
    assert bool(FORBIDDEN.search(text)) is bad


@pytest.mark.parametrize("text,ok", [("공포 지수 22.90 — 혼돈", True), ("“따옴표”", True),
                                     ("S&P 500 1.92% 하락", True), ("🚀 폭등", False), ("☆별", False)])
def test_allowed_chars(text, ok):
    """R6-05 #8·R6-06 D5 — 자막 미지원 글리프는 과금 전 차단, 대시·둥근따옴표 허용."""
    from src.cast.script import ALLOWED_CHARS
    assert bool(ALLOWED_CHARS.match(text)) is ok


def test_merge_keeps_first_valid_when_second_violates(client, t3):
    """R6-05 #12 — 1차 유효 비트는 2차에서 위반이어도 유지, 1차 위반·2차 유효는 2차 채택."""
    first = good_beats(t3, c3="금리 5% 돌파한다")                 # cut3 위반
    second = good_beats(t3, c2="지금 바로 매수하세요", c3="EDT가 다시 선다")  # cut2 위반, cut3 유효
    client([first, second])
    s = C.build_script("T3", t3, SNAP, decision(), None, "shadow")
    assert s["source"] == "gemini"
    assert s["beats"][1]["narration"].startswith("ESCALATE")      # 1차 유효본 유지
    assert s["beats"][2]["narration"] == "EDT가 다시 선다"        # 2차 유효본 채택


def test_merge_second_call_exception_keeps_first_valid(client, t3):
    first = good_beats(t3, c3="금리 5% 돌파한다")
    client([first, ConnectionError("x")])
    s = C.build_script("T3", t3, SNAP, decision(), None, "shadow")
    assert s["source"] == "gemini+template"
    assert s["beats"][1]["narration"].startswith("ESCALATE")      # 1차 유효 유지, cut3만 템플릿



def test_token_only_in_first_beat_and_plain_limit(client, t3):
    """DN2026_0116 — 2번 이후 비트의 토큰·24자 초과는 위반(재호출→템플릿)."""
    bad = good_beats(t3, c4="{{FACT:VIX.close}} 공포가 번진다", c5="가나다라마바사아자차카타파하가나다라마바사아자차카")
    client([bad, bad])
    s = C.build_script("T3", t3, SNAP, decision(), None, "shadow")
    assert s["source"] == "gemini+template"
    assert "VIX" not in s["beats"][3]["narration"] and len(s["beats"][4]["narration"]) <= C.MAX_PLAIN_CHARS
    reason, _ = C.check_beat({"narration": "{{FACT:VIX.close}} 공포", "situation_en": "x y"}, SNAP, allow_token=False)
    assert reason == "token_not_allowed"


# ---------- DN2026_0122 D5·D6 ----------

def test_pools_avoid_banned_lines(t3):
    d = decision(act="RISE")
    first = C._template_script(t3, d, SNAP, C.load_cast_canon())
    lines = [b["narration"] for b in first["beats"]]
    again = C._template_script(t3, decision(act="RISE", prev_lines=[lines]), SNAP, C.load_cast_canon())
    plain = [(a["narration"], b["narration"]) for a, b in zip(first["beats"], again["beats"])
             if a["role"] not in ("ESTABLISH_THREAT", "NEXT_OMEN")]
    assert all(x != y for x, y in plain), plain


def test_template_deterministic_per_decision(t3):
    canon = C.load_cast_canon()
    assert C._template_script(t3, decision(), SNAP, canon) == C._template_script(t3, decision(), SNAP, canon)


def test_template_situation_names_on_screen(t3):
    s = C._template_script(t3, decision(villain="Volatility Hydra"), SNAP, C.load_cast_canon())
    for cut, beat in zip(t3, s["beats"]):
        assert not C.missing_on_screen(beat["situation_en"], cut["characters"]), beat
        assert beat["characters"] == cut["characters"]


def test_finale_omen_teases_next_season(t3):
    s = C._template_script(t3, decision(act="FINALE"), SNAP, C.load_cast_canon())
    assert s["beats"][-1]["narration"].startswith("다음 시즌, ")


def test_villain_kr_canon_names():
    assert C.villain_kr("Volatility Hydra") == "볼래틸리티히드라"
    assert C.villain_kr("Algorithm Reaper") == "알고리즘리퍼"
    assert C.villain_kr("Debt Titan") == "뎁트타이탄"


def test_gemini_duplicate_line_is_violation(client, t3):
    banned_line = "ENGAGE 장면이 이어진다"
    c = client([good_beats(t3), good_beats(t3, c3="EDT가 새 각오를 다진다")])
    s = C.build_script("T3", t3, SNAP, decision(prev_lines=[banned_line]), None, "shadow")
    assert len(c.prompts) == 2 and "cut 3: duplicate_line" in c.prompts[1]
    assert banned_line in c.prompts[0]                       # 금지 목록 프롬프트 주입
    assert s["beats"][2]["narration"] == "EDT가 새 각오를 다진다"


def test_gemini_missing_character_is_violation(client, t3):
    beats = json.loads(good_beats(t3))
    beats[2]["situation_en"] = "A lone figure stands on a rooftop, slow push in."
    c = client([json.dumps(beats, ensure_ascii=False), good_beats(t3)])
    C.build_script("T3", t3, SNAP, decision(), None, "shadow")
    assert "cut 3: situation_missing_character" in c.prompts[1]


def test_gemini_characters_validated(client, t3):
    beats = json.loads(good_beats(t3))
    beats[1]["characters"] = ["LEV-01", "EDT"]; beats[1]["situation_en"] = "Leverage Muscle Man and EDT run."
    beats[2]["characters"] = ["Debt Titan"]                     # 빌런 없는 컷에 빌런 → 기본 출연
    beats[3]["characters"] = ["EDT"]                            # 빌런 컷에서 빌런 누락 → 기본 출연
    beats[4]["characters"] = ["LEV-01"]                         # TURN 은 고정
    c = client([json.dumps(beats, ensure_ascii=False)])
    s = C.build_script("T3", t3, SNAP, decision(), None, "live")
    assert s["source"] == "gemini" and len(c.prompts) == 1
    got = [b["characters"] for b in s["beats"]]
    assert got[1] == ["LEV-01", "EDT"] and got[2] == t3[2]["characters"]
    assert got[3] == t3[3]["characters"] and got[4] == t3[4]["characters"]


def test_payoff_escalate_respects_banned(t3):
    d = decision(omen_payoff="twist")
    first = C.template_line("ESCALATE", 1, d, SNAP, t3[1])
    again = C.template_line("ESCALATE", 1, decision(omen_payoff="twist", prev_lines=[[first]]), SNAP, t3[1])
    assert first.startswith("예상 밖, ") and again.startswith("예상 밖, ") and first != again



def test_every_pool_candidate_within_limits():
    """R6-12 (f): 시드가 고른 1개가 아니라 풀의 모든 후보를 금지 목록을 늘려 가며 전수 검사(STD-PROC-07)."""
    canon_villain = {"rates": "Debt Titan", "volatility": "Volatility Hydra", "momentum": "Algorithm Reaper"}
    roles = ("ESCALATE", "ENGAGE", "CRISIS", "TURN", "RESOLVE", "REGROUP", "AFTERMATH_CALM", "NEXT_OMEN")
    seen_total = 0
    for tier in ("T1", "T2", "T3"):
        for cut in cast_for_tier(tier):
            if cut["role"] not in roles:
                continue
            combos = itertools.product(AXES, ACTS, FOCUS, PAYOFF, RECAPS, ENDINGS, ("canon", "gated"))
            for axis, act, focus, payoff, recap, ending, vk in combos:
                villain = canon_villain[axis] if vk == "canon" else "Debt Titan"
                banned: list[str] = []
                for _ in range(12):
                    d = decision(axis=axis, act=act, hero_focus=focus, omen_payoff=payoff, prev_recap=recap,
                                 villain=villain, ending_type=ending, prev_lines=[banned])
                    line = C.template_line(cut["role"], 1, d, SNAP, cut)
                    if line in banned:
                        break
                    banned.append(line)
                    seen_total += 1
                    assert len(line) <= C.MAX_PLAIN_CHARS, line
                    assert market_facts.check_line(line) is None and not C.FORBIDDEN.search(line), line
                    assert C.ALLOWED_CHARS.match(line), line
    assert seen_total > 1000


def test_resolve_characters_requires_confirmed_ref(monkeypatch, t3):
    monkeypatch.setattr(C, "confirmed_heroes", lambda: {"EDT", "FG-01"})
    cut = t3[1]
    assert C.resolve_characters({"characters": ["LEV-01", "EDT"]}, cut) == cut["characters"]
    assert C.resolve_characters({"characters": ["FG-01", "EDT"]}, cut) == ["FG-01", "EDT"]


def test_merge_duplicate_rechecked(client):
    """R6-12 (c): 1차 cut2 유효 + 2차 cut3 이 같은 문장이면 병합 후 템플릿 대체."""
    t2 = cast_for_tier("T2")
    first = json.loads(good_beats(t2, c2="같은 문장이다", c3="매수하세요"))
    second = json.loads(good_beats(t2, c2="지금 바로 매수하세요", c3="같은 문장이다"))
    client([json.dumps(first, ensure_ascii=False), json.dumps(second, ensure_ascii=False)])
    s = C.build_script("T2", t2, SNAP, decision(), None, "live")
    lines = [b["narration"] for b in s["beats"]]
    assert lines.count("같은 문장이다") == 1 and len(set(lines)) == len(lines)


def test_resolve_characters_accepts_english_names(t3):
    """DN2026_0123 shadow: Gemini 가 'Leverage Muscle Man' 처럼 이름으로 돌려줘도 코드로 받는다."""
    cut = t3[1]
    assert C.resolve_characters({"characters": ["Leverage Muscle Man", "EDT"]}, cut) == ["LEV-01", "EDT"]
    assert C.resolve_characters({"characters": ["Volatility Hydra", "Exposure Futures Girl"]},
                                dict(t3[3], characters=["Volatility Hydra", "FG-01"])) == ["Volatility Hydra", "FG-01"]


def test_violation_reasons_logged(client, t3, caplog):
    client([good_beats(t3, c3="금리 5% 돌파한다"), good_beats(t3)])
    with caplog.at_level("WARNING", logger="cast.script"):
        C.build_script("T3", t3, SNAP, decision(), None, "shadow")
    assert "reasons={3: 'unregistered_market_numeric_claim'}" in caplog.text


def test_violation_line_logged(client, t3, caplog):
    client([good_beats(t3, c3="금리 5% 돌파한다"), good_beats(t3)])
    with caplog.at_level("INFO", logger="cast.script"):
        C.build_script("T3", t3, SNAP, decision(), None, "shadow")
    assert "script_violation_line cut=3 line='금리 5% 돌파한다'" in caplog.text



def test_prompt_persona_quantity_sanitized_and_limits(t3):
    """DN2026_0123 진단: LEV-01 페르소나 '세 배' → 프롬프트에선 '몇 배', 비트별 글자 상한 표기."""
    p = C.build_prompt("T3", t3, SNAP, decision(), None, C.load_cast_canon())
    assert "LEV-01: 힘을 몇 배로 키우지만 추락도 몇 배라는" in p
    assert "narration 최대 24자" in p and "narration 최대 토큰 포함 30자" in p
    assert market_facts.check_line(C._persona_safe("힘을 세 배로 키우지만 추락도 세 배라는")) is None
