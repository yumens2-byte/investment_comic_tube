"""워크플로 계약 테스트 — cast_t3·cast_watchdog (DN2026_0114). 실행 없음, 파싱만."""
import os

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WF = os.path.join(ROOT, ".github", "workflows")


def _load(name):
    with open(os.path.join(WF, name)) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def t3():
    return _load("cast_t3.yml")


@pytest.fixture(scope="module")
def wd():
    return _load("cast_watchdog.yml")


def _step_env(job, name_part):
    for s in job["steps"]:
        if name_part in (s.get("name") or ""):
            return s.get("env") or {}
    raise AssertionError(f"step '{name_part}' 없음")


def test_plan_mode_standard_expression(t3):
    env = _step_env(t3["jobs"]["plan"], "계층 판정")
    assert env["CAST_EXECUTION_MODE"] == (
        "${{ (github.event_name == 'workflow_dispatch' && inputs.mode) "
        "|| vars.CAST_EXECUTION_MODE || 'dry_run' }}")
    assert t3["jobs"]["plan"]["outputs"]["mode"]
    run = next(s for s in t3["jobs"]["plan"]["steps"] if "계층 판정" in (s.get("name") or ""))["run"]
    assert "resolve_mode" in run and "GITHUB_OUTPUT" in run and "run_date_kst" in run


def test_downstream_jobs_use_plan_mode(t3):
    gen = _step_env(t3["jobs"]["generate"], "fal i2v")
    pub = _step_env(t3["jobs"]["publish"], "합성·발행")
    assert gen["CAST_EXECUTION_MODE"] == "${{ needs.plan.outputs.mode }}"
    assert pub["CAST_EXECUTION_MODE"] == "${{ needs.plan.outputs.mode }}"
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "CAST_RUN_CAP_USD",
              "CAST_MONTH_CAP_USD", "CAST_CUT_SEC", "CAST_GEN_BUDGET_SEC"):
        assert k in gen
    for k in ("YOUTUBE_PLAYLIST_TITLE", "CAST_YOUTUBE_PRIVACY", "PIPELINE_CHANNEL_KEY"):
        assert k in pub


def test_publish_gate(t3):
    pub = t3["jobs"]["publish"]
    assert pub["needs"] == ["plan", "generate"]
    assert pub["if"] == "(needs.plan.outputs.mode == 'live' || needs.plan.outputs.mode == 'shadow') && needs.generate.result == 'success'"


def test_tier_force_options(t3):
    assert t3[True]["workflow_dispatch"]["inputs"]["tier_force"]["options"] == ["auto", "T1", "T2", "T3"]


def test_report_job(t3):
    rep = t3["jobs"]["report"]
    assert rep["needs"] == ["plan", "generate", "publish"] and rep["if"] == "always()"
    uses = [s.get("uses", "") for s in rep["steps"]]
    assert uses[0].startswith("actions/checkout@")
    run_steps = [s for s in rep["steps"] if "run" in s]
    assert run_steps and all("continue-on-error" not in s for s in run_steps)
    env = run_steps[-1]["env"]
    for k in ("CAST_RESULT_PLAN", "CAST_RESULT_GENERATE", "CAST_RESULT_PUBLISH"):
        assert k in env


def test_every_job_has_timeout(t3):
    expected = {"test": 10, "plan": 15, "generate": 50, "publish": 20, "report": 5}
    assert {k: j.get("timeout-minutes") for k, j in t3["jobs"].items()} == expected


@pytest.mark.parametrize("job", ["generate", "publish"])
def test_ffmpeg_installed_before_pip(t3, job):
    steps = t3["jobs"][job]["steps"]
    runs = [s.get("run", "") for s in steps]
    ff = next(i for i, r in enumerate(runs) if "apt-get install -y ffmpeg" in r)
    pip = next(i for i, r in enumerate(runs) if "pip install" in r)
    assert ff < pip and "ffprobe -version" in runs[ff]


def test_watchdog(wd):
    assert wd[True]["schedule"][0]["cron"] == "43 6 * * 6"
    assert wd["permissions"]["actions"] == "read"
    job = wd["jobs"]["check"]
    assert job["timeout-minutes"] == 5
    run = "\n".join(s.get("run", "") for s in job["steps"])
    assert "curl -fsS" in run and '"schedule"' in run and "점검일 아님" in run


# ---------- DN2026_0115 P2 스토리 레이어 ----------

def test_plan_story_env_and_flow(t3):
    env = _step_env(t3["jobs"]["plan"], "계층 판정")
    assert env["GEMINI_API_KEY"] == "${{ secrets.GEMINI_API_KEY }}"
    assert env["SUPABASE_URL"] == "${{ secrets.SUPABASE_URL }}"
    assert env["SUPABASE_SERVICE_ROLE_KEY"] == "${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}"
    run = next(s for s in t3["jobs"]["plan"]["steps"] if "계층 판정" in (s.get("name") or ""))["run"]
    order = ["fetch_market_data()", "fetch_last_story(", "storyline.decide(", "cast_for_tier(tier, villain=",
             "storyline.apply_focus(", "build_script(", "storyline.next_state("]
    pos = [run.index(k) for k in order]
    assert pos == sorted(pos)
    assert 'if mode in ("shadow", "live") else None' in run
    for k in ('"axis"', '"villain_scores"', '"villain_raw"', '"omen_villain"', '"omen_axis"',
              '"hero_focus"', '"script"', '"story_state_next"', "[CAST] story villain="):
        assert k in run


def test_generate_tts_env(t3):
    gen = _step_env(t3["jobs"]["generate"], "fal i2v")
    assert gen["GEMINI_API_KEY"] == "${{ secrets.GEMINI_API_KEY }}"
    assert gen["CAST_TTS_MODEL"] == "${{ vars.CAST_TTS_MODEL || 'gemini-3.8-flash-tts' }}"
    assert gen["TTS_VOICE"] == "${{ vars.CAST_TTS_VOICE || 'Charon' }}"


def test_publish_caption_font_after_ffmpeg(t3):
    steps = t3["jobs"]["publish"]["steps"]
    runs = [s.get("run", "") for s in steps]
    ff = next(i for i, r in enumerate(runs) if "apt-get install -y ffmpeg" in r)
    font = next(i for i, r in enumerate(runs) if "install_caption_font.py" in r)
    assert font == ff + 1 and runs[font].strip() == "sudo python3 scripts/install_caption_font.py"
    assert _step_env(t3["jobs"]["publish"], "합성·발행")["CAPTION_LAYOUT_POLICY"] == "caption-layout-v1"
