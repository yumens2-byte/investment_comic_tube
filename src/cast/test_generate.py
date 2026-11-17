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
