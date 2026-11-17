"""generate 단위 테스트 — 네트워크 없이 계약 검증."""
import pytest

from src.cast.generate import (
    GenerationError, build_cut_prompt, build_fal_request, estimate_cost,
)
from src.cast.refsheet import load_ref_urls, ref_url_for


def test_prompt_is_canon_plus_situation_only():
    p = build_cut_prompt("HERO CANON BLOCK", "ENGAGE")
    assert p.startswith("HERO CANON BLOCK")
    assert "battle stance" in p


def test_unknown_role_rejected():
    with pytest.raises(GenerationError, match="미정의"):
        build_cut_prompt("c", "UNKNOWN_ROLE")


def test_fal_request_contract():
    r = build_fal_request("p", "https://x/y.png", 10)
    assert r["duration"] == 10 and r["resolution"] == "768P"
    assert r["prompt_expansion_mode"] == "disabled"
    assert r["image_url"] == "https://x/y.png"


def test_fal_request_t2v_when_no_ref():
    r = build_fal_request("p", None, 10)
    assert "image_url" not in r


def test_duration_bounds():
    with pytest.raises(GenerationError):
        build_fal_request("p", None, 4)
    with pytest.raises(GenerationError):
        build_fal_request("p", None, 16)


def test_cost_matches_measured_rate():
    assert estimate_cost(10.14) == 0.61   # 부록 E 실측치와 정합
    assert estimate_cost(70) == 4.2       # T3 회차 원가


def test_missing_fal_key_blocks():
    import os
    from src.cast import generate
    saved = {k: os.environ.pop(k) for k in ("FAL_AI_KEY", "FAL_KEY") if k in os.environ}
    try:
        with pytest.raises(GenerationError, match="FAL_AI_KEY"):
            generate.submit_cut({"prompt": "x"})
    finally:
        os.environ.update(saved)


def test_refsheet_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CAST_REFSHEET_FILE", str(tmp_path / "none.json"))
    assert load_ref_urls() == {}


def test_ref_url_first_entry():
    refs = {"FG-01": ["https://a/1", "https://a/2"]}
    assert ref_url_for(refs, "FG-01") == "https://a/1"
    assert ref_url_for(refs, "EDT") is None


# ── DN2026_0114: 재제출 정책·비용/시간 가드·원장 기록 ─────────────────────────
import importlib
import json
import urllib.error

from src.cast import generate as G
from src.cast import ledger as L

gen_main = importlib.import_module("src.cast.__main__generate")


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.01)


@pytest.fixture
def fal(monkeypatch):
    """fal 상태머신 가짜 — 네트워크 없음. statuses: GET 응답 시퀀스(예외 객체 허용)."""
    monkeypatch.setenv("FAL_AI_KEY", "test-key")
    clock = FakeClock()
    monkeypatch.setattr(G, "time", clock)
    st = {"submits": 0, "statuses": [], "downloads": 0, "probe": lambda p: 6.0, "clock": clock}

    def submit(req):
        st["submits"] += 1
        return {"request_id": f"req{st['submits']}", "status_url": "s", "response_url": "r"}

    def get_json(url, headers):
        if url == "r":
            return {"video": {"url": "https://v/x.mp4"}}
        item = st["statuses"].pop(0) if st["statuses"] else {"status": "IN_PROGRESS"}
        if isinstance(item, Exception):
            raise item
        return item

    def download(url, out):
        st["downloads"] += 1

    monkeypatch.setattr(G, "submit_cut", submit)
    monkeypatch.setattr(G, "_get_json", get_json)
    monkeypatch.setattr(G, "_download", download)
    monkeypatch.setattr(G, "_probe_duration", lambda p: st["probe"](p))
    return st


def test_generate_cut_success(fal, tmp_path):
    fal["statuses"] = [{"status": "IN_QUEUE"}, {"status": "COMPLETED"}]
    r = G.generate_cut({"prompt": "p"}, str(tmp_path / "c.mp4"), cut_no=3, deadline_ts=fal["clock"].t + 600)
    assert r["cut"] == 3 and r["attempts"] == 1 and r["cost_usd"] == 0.36
    assert fal["submits"] == 1


def test_probe_failure_no_resubmit(fal, tmp_path):
    fal["statuses"] = [{"status": "COMPLETED"}]
    fal["probe"] = lambda p: (_ for _ in ()).throw(G.GenerationError("ffprobe 실패"))
    with pytest.raises(G.GenerationError, match="ffprobe"):
        G.generate_cut({}, str(tmp_path / "c.mp4"), cut_no=1, deadline_ts=fal["clock"].t + 600)
    assert fal["submits"] == 1


def test_terminal_failed_resubmits_once(fal, tmp_path):
    fal["statuses"] = [{"status": "FAILED"}, {"status": "COMPLETED"}]
    calls = []
    r = G.generate_cut({}, str(tmp_path / "c.mp4"), cut_no=1, deadline_ts=fal["clock"].t + 600,
                       on_submit=lambda: calls.append(1))
    assert fal["submits"] == 2 and r["attempts"] == 2 and len(calls) == 2


def test_terminal_failed_twice_gives_up(fal, tmp_path):
    fal["statuses"] = [{"status": "FAILED"}, {"status": "ERROR"}]
    with pytest.raises(G.GenerationError, match="재제출"):
        G.generate_cut({}, str(tmp_path / "c.mp4"), cut_no=1, deadline_ts=fal["clock"].t + 600)
    assert fal["submits"] == 2


def test_poll_transient_errors_then_success(fal, tmp_path):
    fal["statuses"] = [urllib.error.URLError("dns"),
                       urllib.error.HTTPError("s", 503, "busy", None, None),
                       {"status": "COMPLETED"}]
    r = G.generate_cut({}, str(tmp_path / "c.mp4"), cut_no=1, deadline_ts=fal["clock"].t + 600)
    assert fal["submits"] == 1 and r["attempts"] == 1


def test_poll_transient_exhausted_no_resubmit(fal, tmp_path):
    fal["statuses"] = [urllib.error.URLError("x")] * 4
    with pytest.raises(G.GenerationError, match="연속 실패"):
        G.generate_cut({}, str(tmp_path / "c.mp4"), cut_no=1, deadline_ts=fal["clock"].t + 600)
    assert fal["submits"] == 1


def test_deadline_exceeded_no_resubmit(fal, tmp_path):
    fal["statuses"] = []   # 계속 IN_PROGRESS
    with pytest.raises(G.GenerationError, match="타임아웃"):
        G.generate_cut({}, str(tmp_path / "c.mp4"), cut_no=1, deadline_ts=fal["clock"].t + 60)
    assert fal["submits"] == 1


def test_request_has_no_internal_keys():
    r = build_fal_request("p", None, 6)
    assert "_cut_no" not in r and not any(k.startswith("_") for k in r)
    json.dumps(r)


# ── __main__generate 가드 ─────────────────────────────────────────────────

def _plan(n=3):
    return [{"cut": i + 1, "role": "ESTABLISH_THREAT", "characters": ["Debt Titan"]} for i in range(n)]


@pytest.fixture
def run_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts_cast").mkdir()
    plan_file = tmp_path / "artifacts_cast" / "cast_plan.json"
    monkeypatch.setenv("CAST_PLAN_FILE", str(plan_file))
    monkeypatch.setenv("CAST_REFSHEET_FILE", str(tmp_path / "none.json"))
    monkeypatch.setenv("FAL_AI_KEY", "test-key")
    for k in ("CAST_RUN_CAP_USD", "CAST_MONTH_CAP_USD", "CAST_CUT_SEC", "CAST_GEN_BUDGET_SEC"):
        monkeypatch.delenv(k, raising=False)
    calls = {"open": [], "add": [], "mark": [], "month": []}
    monkeypatch.setattr(L, "open_run", lambda *a: calls["open"].append(a))
    monkeypatch.setattr(L, "add_cost", lambda rid, usd: calls["add"].append(usd) or sum(calls["add"]))
    monkeypatch.setattr(L, "mark", lambda rid, st, **f: calls["mark"].append((st, f)))
    monkeypatch.setattr(L, "month_spent_usd", lambda d: calls["month"].append(d) or 0.0)
    calls["story"] = []   # DN2026_0115 통합: 실 ledger.record_story 존재 — 가짜로 대체
    monkeypatch.setattr(L, "record_story", lambda *a: calls["story"].append(a), raising=False)

    def write(n=3, mode="shadow"):
        plan_file.write_text(json.dumps({"tier": "T3", "plan": _plan(n), "mode": mode,
                                         "run_id": "777", "run_date_kst": "2026-11-21"}))
        monkeypatch.setenv("CAST_EXECUTION_MODE", mode)
    calls["write"] = write
    calls["ledger"] = lambda: json.loads((tmp_path / "artifacts_cast" / "generate_ledger.json").read_text())
    return calls


def _fake_cut(submits):
    def fake(request, out, *, cut_no, deadline_ts, on_submit=None):
        assert "_cut_no" not in request
        on_submit()
        submits.append(cut_no)
        return {"cut": cut_no, "path": out, "duration_sec": 6.0, "cost_usd": 0.36,
                "request_id": "x", "attempts": 1}
    return fake


def test_dry_run_never_touches_ledger_or_fal(run_env, monkeypatch):
    run_env["write"](3, "dry_run")
    for name in ("open_run", "add_cost", "mark", "month_spent_usd"):
        monkeypatch.setattr(L, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB 호출")))
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    gen_main.main()
    led = run_env["ledger"]()
    assert led["mode"] == "dry_run" and led["planned_cost_usd"] == 1.08 and len(led["results"]) == 3


def test_invalid_mode_demoted_to_dry_run(run_env, monkeypatch):
    run_env["write"](3, "shadow")
    monkeypatch.setenv("CAST_EXECUTION_MODE", "prod")
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    gen_main.main()
    assert run_env["ledger"]()["mode"] == "dry_run" and run_env["open"] == []


def test_shadow_happy_path_records_cost(run_env, monkeypatch):
    run_env["write"](3, "shadow")
    submits = []
    monkeypatch.setattr(G, "generate_cut", _fake_cut(submits))
    gen_main.main()
    assert submits == [1, 2, 3] and run_env["add"] == [0.36, 0.36, 0.36]
    assert run_env["open"][0][:4] == ("777", "2026-11-21", "T3", "shadow")
    assert run_env["mark"][-1][0] == "generated"
    assert run_env["ledger"]()["total_cost_usd"] == 1.08


def test_run_cap_blocks_before_any_call(run_env, monkeypatch):
    run_env["write"](7, "live")
    monkeypatch.setenv("CAST_RUN_CAP_USD", "2")          # 7*6*0.06 = 2.52 > 2
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    with pytest.raises(G.GenerationError, match="cost_cap_run"):
        gen_main.main()
    assert run_env["open"] == [] and run_env["add"] == []
    assert run_env["ledger"]()["error"] == "GenerationError:cost_cap_run"


def test_month_cap_blocks(run_env, monkeypatch):
    run_env["write"](3, "live")
    monkeypatch.setattr(L, "month_spent_usd", lambda d: 29.5)
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    with pytest.raises(G.GenerationError, match="cost_cap_month"):
        gen_main.main()
    assert run_env["open"] == []


def test_runtime_cap_blocks_resubmission(run_env, monkeypatch):
    run_env["write"](3, "shadow")
    monkeypatch.setenv("CAST_RUN_CAP_USD", "1.3")        # 계획 1.08 통과, 재제출로 4번째 제출 1.44 > 1.3

    def resubmitting(request, out, *, cut_no, deadline_ts, on_submit=None):
        on_submit()
        if cut_no == 2:
            on_submit()                                    # 터미널 실패 재제출 모사
        return {"cut": cut_no, "path": out, "duration_sec": 6.0, "cost_usd": 0.36,
                "request_id": "x", "attempts": 1}
    monkeypatch.setattr(G, "generate_cut", resubmitting)
    with pytest.raises(G.GenerationError, match="cost_cap_run_runtime"):
        gen_main.main()
    assert run_env["add"] == [0.36, 0.36, 0.36]
    assert run_env["mark"][-1] == ("gen_failed", {"error": "GenerationError:cost_cap_run_runtime"})


# ── R6-01 반영 회귀 (DN2026_0114) ─────────────────────────────────────────

def test_duration_out_of_range_blocks_before_spend(run_env, monkeypatch):
    run_env["write"](7, "live")
    monkeypatch.setenv("CAST_CUT_SEC", "9")              # 7×9=63s > 60s, 비용 3.78은 상한 이내
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    with pytest.raises(G.GenerationError, match="duration_out_of_range"):
        gen_main.main()
    assert run_env["open"] == [] and run_env["add"] == [] and run_env["month"] == []


def test_prior_cost_counts_toward_run_cap(run_env, monkeypatch):
    run_env["write"](3, "shadow")
    monkeypatch.setattr(L, "open_run", lambda *a: 4.5)    # 같은 run_id 재실행 — 이미 $4.50 누적
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    with pytest.raises(G.GenerationError, match="cost_cap_run"):
        gen_main.main()
    assert run_env["add"] == []


def test_month_cap_checked_at_runtime(run_env, monkeypatch):
    run_env["write"](3, "shadow")
    monkeypatch.setattr(L, "month_spent_usd", lambda d: 28.8)   # 28.8+1.08=29.88 통과
    monkeypatch.setenv("CAST_RUN_CAP_USD", "5")

    def resubmitting(request, out, *, cut_no, deadline_ts, on_submit=None):
        on_submit()
        if cut_no == 1:
            on_submit()                                    # 재제출 → 28.8+0.72 … 4번째 1.44 → 30.24 > 30
        return {"cut": cut_no, "path": out, "duration_sec": 6.0, "cost_usd": 0.36,
                "request_id": "x", "attempts": 1}
    monkeypatch.setattr(G, "generate_cut", resubmitting)
    with pytest.raises(G.GenerationError, match="cost_cap_month_runtime"):
        gen_main.main()
    assert run_env["add"] == [0.36, 0.36, 0.36]


def test_duplicate_run_does_not_overwrite_status(run_env, monkeypatch):
    run_env["write"](3, "live")

    def dup(*a):
        raise L.DuplicatePublish("run_id 이미 published")
    monkeypatch.setattr(L, "open_run", dup)
    with pytest.raises(L.DuplicatePublish):
        gen_main.main()
    assert run_env["mark"] == []                          # published 행을 gen_failed로 덮지 않는다


def test_exception_still_writes_ledger_json(run_env, monkeypatch):
    run_env["write"](3, "shadow")
    submits = []
    ok = _fake_cut(submits)

    def boom(request, out, *, cut_no, deadline_ts, on_submit=None):
        if cut_no == 2:
            raise G.GenerationError("fal 타임아웃")
        return ok(request, out, cut_no=cut_no, deadline_ts=deadline_ts, on_submit=on_submit)
    monkeypatch.setattr(G, "generate_cut", boom)
    with pytest.raises(G.GenerationError):
        gen_main.main()
    led = run_env["ledger"]()
    assert led["error"] == "GenerationError" and len(led["results"]) == 1
    assert led["total_cost_usd"] == 0.36
    assert run_env["mark"][-1][0] == "gen_failed"


def test_cut_sec_out_of_range(run_env, monkeypatch):
    run_env["write"](3, "shadow")
    monkeypatch.setenv("CAST_CUT_SEC", "20")
    with pytest.raises(G.GenerationError, match="CAST_CUT_SEC"):
        gen_main.main()
    assert run_env["open"] == []


def test_time_budget_guard(run_env, monkeypatch):
    run_env["write"](3, "shadow")
    monkeypatch.setenv("CAST_GEN_BUDGET_SEC", "100")      # 남은 시간 < 120s
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    with pytest.raises(G.GenerationError, match="time_budget"):
        gen_main.main()


def test_deadline_split_across_remaining_cuts(run_env, monkeypatch):
    run_env["write"](4, "shadow")
    clock = FakeClock(0.0)
    monkeypatch.setattr(gen_main, "time", clock)
    seen = []

    def fake(request, out, *, cut_no, deadline_ts, on_submit=None):
        seen.append(deadline_ts - clock.t)
        on_submit()
        clock.t += 300
        return {"cut": cut_no, "path": out, "duration_sec": 6.0, "cost_usd": 0.36,
                "request_id": "x", "attempts": 1}
    monkeypatch.setattr(G, "generate_cut", fake)
    gen_main.main()
    assert seen == [600.0, 700.0, 900.0, 1500.0]       # (2400-경과)/남은컷


# ── DN2026_0115 P2: build_cut_prompt_v2 · fal error 판정 · 대본 흐름 ─────────────────
from src.cast import narration as NR
from src.cast.casting import _load_canon


def test_prompt_v2_villain_attack_negative_and_no_text():
    canon = _load_canon()
    p = G.build_cut_prompt_v2(["EDT", "FG-01", "Debt Titan"], "The hero dodges a falling chain.",
                              canon, "rates")
    assert p.startswith("Comic-book cinematic vertical shot. EDT, a towering tiger warrior")
    assert "heavy chains of debt" in p                      # attack_forms_en[rates]
    assert "The hero dodges a falling chain." in p
    assert "No text, no numbers, no letters anywhere in frame." in p
    neg = canon["_cast_video_canon"]["characters"]["FG-01"]["negative_en"].strip().rstrip(".")
    assert p.endswith(f" Strictly: {neg}.")
    assert len(p) <= G.PROMPT_MAX_CHARS


def test_prompt_v2_unknown_character_name_only_and_no_axis():
    p = G.build_cut_prompt_v2(["Chaos Reaper"], "Shadows swirl.", _load_canon(), None)
    assert p == ("Comic-book cinematic vertical shot. Chaos Reaper. Shadows swirl."
                 " Single action, single camera move. No text, no numbers, no letters anywhere in frame.")
    p2 = G.build_cut_prompt_v2(["Debt Titan"], "x.", _load_canon(), None)
    assert "chains" not in p2 and "Strictly" not in p2


def test_prompt_v2_too_long_and_empty_situation():
    with pytest.raises(G.GenerationError, match="prompt_too_long"):
        G.build_cut_prompt_v2(["EDT"], "y" * 2000, _load_canon(), None)
    with pytest.raises(G.GenerationError, match="situation_en"):
        G.build_cut_prompt_v2(["EDT"], " ", _load_canon(), None)


def test_completed_with_error_resubmits_once(fal, tmp_path, monkeypatch, caplog):
    fal["statuses"] = [{"status": "COMPLETED"}, {"status": "COMPLETED"}]
    results = [{"detail": "SECRET body", "error_type": "content_policy_violation"},
               {"video": {"url": "https://v/x.mp4"}}]

    def get_json(url, headers):
        if url == "r":
            return results.pop(0)
        return fal["statuses"].pop(0)
    monkeypatch.setattr(G, "_get_json", get_json)
    r = G.generate_cut({}, str(tmp_path / "c.mp4"), cut_no=2, deadline_ts=fal["clock"].t + 600)
    assert fal["submits"] == 2 and r["attempts"] == 2
    assert "fal_failed type=content_policy_violation" in caplog.text and "SECRET" not in caplog.text


def test_completed_with_error_twice_gives_up(fal, tmp_path, monkeypatch):
    fal["statuses"] = [{"status": "COMPLETED"}, {"status": "COMPLETED"}]
    monkeypatch.setattr(G, "_get_json", lambda url, h: {"error": "x"} if url == "r" else fal["statuses"].pop(0))
    with pytest.raises(G.GenerationError, match="fal_failed type=unknown"):
        G.generate_cut({}, str(tmp_path / "c.mp4"), cut_no=1, deadline_ts=fal["clock"].t + 600)
    assert fal["submits"] == 2


def _script(n=3):
    roles = ["ESTABLISH_THREAT", "CRISIS", "RESOLVE"]
    return {"version": 1, "source": "test", "ending_type": "x",
            "beats": [{"cut": i + 1, "role": roles[i % 3], "narration": f"내레이션 {i + 1}",
                       "situation_en": f"Situation {i + 1}."} for i in range(n)]}


@pytest.fixture
def script_env(run_env, tmp_path):
    def write(n=3, mode="shadow", script=True):
        run_env["write"](n, mode)
        doc = json.loads((tmp_path / "artifacts_cast" / "cast_plan.json").read_text())
        doc.update({"villain": "Debt Titan", "axis": "volatility", "story_state_next": {"ep": 2}})
        if script:
            doc["script"] = _script(n)
        (tmp_path / "artifacts_cast" / "cast_plan.json").write_text(json.dumps(doc, ensure_ascii=False))
    run_env["write_script"] = write
    return run_env


def test_script_shadow_flow_tts_then_v2_prompts(script_env, monkeypatch):
    script_env["write_script"](3, "shadow")
    order, prompts, stories = [], [], []
    monkeypatch.setattr(L, "record_story", lambda *a: stories.append(a) or order.append("story"),
                        raising=False)
    monkeypatch.setattr(NR, "synthesize", lambda beats, out, **k: order.append(("tts", k["budget_sec"]))
                        or [{"cut": b["cut"], "wav": f"{out}/n{b['cut']}.wav", "sec": 3.0, "tempo": 1.0}
                            for b in beats])
    submits = []
    fake = _fake_cut(submits)

    def cut(request, out, **k):
        order.append("fal")
        prompts.append(request["prompt"])
        return fake(request, out, **k)
    monkeypatch.setattr(G, "generate_cut", cut)
    gen_main.main()
    assert order[:2] == ["story", ("tts", 6)] and order[2:] == ["fal"] * 3
    assert stories[0] == ("777", "Debt Titan", {"ep": 2}, _script(3))
    assert all(p.startswith("Comic-book cinematic vertical shot.") for p in prompts)
    assert "flickering chaotic shadows" in prompts[0] and "Situation 2." in prompts[1]
    led = script_env["ledger"]()
    assert [n["cut"] for n in led["narration"]] == [1, 2, 3] and led["story_recorded"] is True


def test_script_tts_failure_no_fal_and_gen_failed(script_env, monkeypatch):
    script_env["write_script"](3, "live")

    def boom(*a, **k):
        raise NR.NarrationError("tts_failed reason=tts:quota_exhausted ok=0/3")
    monkeypatch.setattr(NR, "synthesize", boom)
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    with pytest.raises(G.GenerationError, match="narration_failed"):
        gen_main.main()
    assert script_env["add"] == []
    assert script_env["mark"][-1] == ("gen_failed", {"error": "GenerationError:narration_failed"})
    assert script_env["ledger"]()["error"] == "GenerationError:narration_failed"


def test_script_dry_run_no_tts(script_env, monkeypatch):
    script_env["write_script"](3, "dry_run")
    monkeypatch.setattr(NR, "synthesize", lambda *a, **k: (_ for _ in ()).throw(AssertionError("TTS 호출")))
    monkeypatch.setattr(L, "record_story", lambda *a: (_ for _ in ()).throw(AssertionError("DB")),
                        raising=False)
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    gen_main.main()
    led = script_env["ledger"]()
    assert "narration" not in led
    assert led["narration_plan"][0] == {"cut": 1, "role": "ESTABLISH_THREAT", "text": "내레이션 1", "chars": 6}
    assert all(r["prompt_chars"] > 100 for r in led["results"])


def test_script_without_record_story_still_runs(script_env, monkeypatch):
    script_env["write_script"](3, "shadow")
    monkeypatch.delattr(L, "record_story", raising=False)
    monkeypatch.setattr(NR, "synthesize", lambda beats, out, **k: [
        {"cut": b["cut"], "wav": "w", "sec": 3.0, "tempo": 1.0} for b in beats])
    submits = []
    monkeypatch.setattr(G, "generate_cut", _fake_cut(submits))
    gen_main.main()
    assert submits == [1, 2, 3] and "story_recorded" not in script_env["ledger"]()


def test_script_mismatch_blocks_before_spend(script_env, monkeypatch, tmp_path):
    script_env["write_script"](3, "shadow")
    f = tmp_path / "artifacts_cast" / "cast_plan.json"
    doc = json.loads(f.read_text())
    doc["script"]["beats"] = doc["script"]["beats"][:2]
    f.write_text(json.dumps(doc))
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    with pytest.raises(G.GenerationError, match="script_mismatch"):
        gen_main.main()
    assert script_env["open"] == [] and script_env["add"] == []


def test_no_script_keeps_legacy_prompt(script_env, monkeypatch):
    script_env["write_script"](3, "shadow", script=False)
    monkeypatch.setattr(NR, "synthesize", lambda *a, **k: (_ for _ in ()).throw(AssertionError("TTS")))
    prompts = []
    fake = _fake_cut([])
    monkeypatch.setattr(G, "generate_cut", lambda req, out, **k: prompts.append(req["prompt"]) or fake(req, out, **k))
    gen_main.main()
    assert all(p.startswith(gen_main.CANON_PROMPT) for p in prompts)
    assert "narration" not in script_env["ledger"]()


def test_narration_requires_cut_sec_6(script_env, monkeypatch):
    """R6-06 D3 — 내레이션 회차에서 CAST_CUT_SEC=5는 과금 전 차단(T1 합계 15초 경계)."""
    script_env["write_script"](3, "live")
    monkeypatch.setenv("CAST_CUT_SEC", "5")
    monkeypatch.setattr(G, "generate_cut", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fal 호출")))
    with pytest.raises(G.GenerationError, match="cut_sec_too_short_for_narration"):
        gen_main.main()
    assert script_env["add"] == [] and script_env["open"] == []


def test_next_omen_prompt_uses_omen_axis():
    """R6-05 #14 — NEXT_OMEN 컷은 다음 주 예고 축의 공격 형태를 쓴다."""
    canon = _load_canon()["_cast_video_canon"]
    forms = canon["characters"]["Debt Titan"]["attack_forms_en"]
    cut = {"cut": 7, "role": "NEXT_OMEN", "characters": ["EDT", "Debt Titan"]}
    beat = {"situation_en": "A shadow looms on the horizon."}
    p = gen_main._cut_prompt(cut, beat, canon, "volatility", "rates")
    assert forms["rates"] in p and forms["volatility"] not in p
    other = {"cut": 4, "role": "CRISIS", "characters": ["Debt Titan", "FG-01"]}
    assert forms["volatility"] in gen_main._cut_prompt(other, beat, canon, "volatility", "rates")
