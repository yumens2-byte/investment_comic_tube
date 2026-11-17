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
    beats = [{"cut": c["cut"], "narration": f"{c['role']} 장면이 이어진다"[:20],
              "situation_en": "The hero turns toward the storm, slow push in."} for c in plan]
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
    assert s["source"] == "template" and s["version"] == 1 and s["ending_type"] == "TEASE"
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


@pytest.mark.parametrize("tier", ["T1", "T2", "T3"])
@pytest.mark.parametrize("snap", [SNAP, {"SPX": {"close": 1, "change_pct": 0.5}}, {}])
def test_all_templates_within_limits(tier, snap):
    plan = cast_for_tier(tier)
    for axis, omen, ending, focus, payoff in itertools.product(AXES, AXES, ENDINGS, FOCUS, PAYOFF):
        d = decision(axis=axis, omen_axis=omen, ending_type=ending, hero_focus=focus, omen_payoff=payoff)
        for i, cut in enumerate(plan):
            raw = C.template_line(cut["role"], i, d, snap)
            assert market_facts.check_line(raw) is None, raw
            assert len(market_facts.TOKEN.sub("", raw)) <= C.MAX_TEMPLATE_CHARS, raw
            resolved = market_facts.resolve(raw, snap)
            assert len(resolved) <= C.MAX_RESOLVED_CHARS, resolved
            assert not C.FORBIDDEN.search(resolved), resolved
            sit = C.template_situation(cut, d, C.load_cast_canon())
            assert len(sit.split()) <= C.MAX_SITUATION_WORDS and not any(ch.isdigit() for ch in sit)


def test_template_fact_fallbacks():
    assert C.fact_token("rates", SNAP) == "{{FACT:TNX.change_pct}}"
    assert C.fact_token("momentum", {"SPX": {"change_pct": 1.0}}) == "{{FACT:SPX.change_pct}}"
    assert C.fact_token("rates", {}) is None


def test_template_payoff_and_villain_attack_form(t3):
    s = C._template_script(t3, decision(omen_payoff="hit"), SNAP, C.load_cast_canon())
    assert s["beats"][0]["narration"].startswith("예고대로 ")
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
    assert "원칙의 수호자" in p and "splitting into flickering" in p and "28자" in p


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
    assert s["beats"][5]["narration"] == "흔들릴수록 비중 관리가 먼저다"
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
    ("가" * 37, "narration_too_long"),
])
def test_check_beat_blocks(line, reason):
    item = {"cut": 1, "narration": line, "situation_en": "A calm push in."}
    assert C.check_beat(item, SNAP)[0] == reason


def test_check_beat_situation_rules():
    assert C.check_beat({"narration": "고요하다", "situation_en": "word " * 46}, SNAP)[0] == "situation_too_long"
    assert C.check_beat({"narration": "고요하다", "situation_en": "Shot 2 of city"}, SNAP)[0] == "situation_has_digit"
    assert C.check_beat(None, SNAP)[0] == "missing_beat"
