"""CAST 발행 테스트 — 모드 게이트·원장 선기록(fail-closed)·메타데이터·제목 (DN2026_0114)."""
import pytest

import src.cast.publish as pub
from src.cast import ledger
from src.cast.publish import PublishBlocked, cast_episode_metadata, publish_cast

PLAN = {
    "tier": "T3", "run_id": "run-1", "run_date_kst": "2026-11-17", "villain": "Debt Titan",
    "mode": "live", "plan": [{"cut": i, "role": "r", "characters": []} for i in range(1, 8)],
    "market_snapshot": {"SPX": {"close": 6000, "change_pct": -1.23},
                        "NASDAQ": {"close": 20000, "change_pct": 2.5},
                        "VIX": {"close": 20, "change_pct": -0.4}},
}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.mp4").write_bytes(b"\x00")   # DN2026_0114 R6-03 #3: claim 전 파일 실재 확인
    calls = []
    state = {"claim_exc": None, "mark_exc": None}

    def claim(run_id, run_date_kst):
        calls.append(("claim", run_id, run_date_kst))
        if state["claim_exc"]:
            raise state["claim_exc"]

    def next_no():
        calls.append(("next_no",))
        return 12

    def mark(run_id, status, **fields):
        calls.append(("mark", run_id, status, fields))
        if state["mark_exc"]:
            raise state["mark_exc"]

    monkeypatch.setattr(pub.ledger, "claim_upload", claim)
    monkeypatch.setattr(pub.ledger, "next_episode_no", next_no)
    monkeypatch.setattr(pub.ledger, "mark", mark)
    return calls, state


def make_uploader(calls, result="vid123", exc=None):
    def up(path, metadata):
        calls.append(("upload", path, metadata))
        if exc:
            raise exc
        return result
    return up


@pytest.mark.parametrize("mode", ["dry_run", "shadow"])
def test_non_live_touches_nothing(fake, mode):
    calls, _ = fake
    assert publish_cast("x.mp4", PLAN, mode, uploader=make_uploader(calls)) is None
    assert calls == []


def test_unknown_mode_blocked(fake):
    calls, _ = fake
    with pytest.raises(PublishBlocked):
        publish_cast("x.mp4", PLAN, "LIVE!", uploader=make_uploader(calls))
    assert calls == []


def test_live_happy_path_order(fake):
    calls, _ = fake
    vid = publish_cast("x.mp4", PLAN, "live", "thumb.png", uploader=make_uploader(calls))
    assert vid == "vid123"
    assert [c[0] for c in calls] == ["claim", "next_no", "upload", "mark"]
    assert calls[0] == ("claim", "run-1", "2026-11-17")
    meta = calls[2][2]
    assert meta["episode"] == "C12" and meta["thumbnail_source"] == "thumb.png"
    assert calls[3] == ("mark", "run-1", "published",
                        {"youtube_video_id": "vid123", "episode_no": 12})


def test_duplicate_blocks_before_upload(fake):
    calls, state = fake
    state["claim_exc"] = ledger.DuplicatePublish("dup")
    with pytest.raises(PublishBlocked, match="중복"):
        publish_cast("x.mp4", PLAN, "live", uploader=make_uploader(calls))
    assert [c[0] for c in calls] == ["claim"]


def test_ledger_unavailable_fail_closed(fake):
    calls, state = fake
    state["claim_exc"] = ledger.CastLedgerUnavailable("down")
    with pytest.raises(PublishBlocked):
        publish_cast("x.mp4", PLAN, "live", uploader=make_uploader(calls))
    assert not any(c[0] == "upload" for c in calls)


def test_no_video_id_keeps_lock(fake):
    """R6-02 #2 — video_id 없음은 실재 불명 → uploading 유지(같은 날 재업로드 차단)."""
    calls, _ = fake
    with pytest.raises(PublishBlocked):
        publish_cast("x.mp4", PLAN, "live", uploader=make_uploader(calls, result=None))
    assert calls[-1] == ("mark", "run-1", "uploading", {"error": "no_video_id"})


def test_uploader_exception_marks_then_reraises(fake):
    calls, _ = fake
    with pytest.raises(ValueError, match="boom"):
        publish_cast("x.mp4", PLAN, "live", uploader=make_uploader(calls, exc=ValueError("boom")))
    assert calls[-1][:3] == ("mark", "run-1", "uploading")    # R6-02 #2 잠금 유지


def test_missing_file_blocked_before_claim(fake):
    """R6-03 #3 — 파일 부재는 claim 전에 차단(원장·업로드 0)."""
    calls, _ = fake
    with pytest.raises(PublishBlocked, match="합성 영상 없음"):
        publish_cast("nope.mp4", PLAN, "live", uploader=make_uploader(calls))
    assert calls == []


def test_upload_filenotfound_after_start_keeps_lock(fake):
    """R6-03 #3 — 업로드 착수 후 FileNotFoundError(썸네일 ffmpeg 등)도 잠금 유지."""
    calls, _ = fake
    with pytest.raises(FileNotFoundError):
        publish_cast("x.mp4", PLAN, "live", uploader=make_uploader(calls, exc=FileNotFoundError("ffmpeg")))
    assert calls[-1][:3] == ("mark", "run-1", "uploading")


def test_on_uploaded_called_before_mark(fake):
    """R6-02 #4 — mark 실패 대비 video_id를 원장 기록 전에 남긴다."""
    calls, _ = fake
    seen = []
    publish_cast("x.mp4", PLAN, "live", uploader=make_uploader(calls),
                 on_uploaded=lambda vid: seen.append(("cb", vid, len(calls))))
    assert seen and seen[0][1] == "vid123"
    assert calls[seen[0][2]][2] == "published"                 # 콜백 다음 호출이 published mark


def test_uploader_exception_wins_over_mark_failure(fake):
    calls, state = fake
    state["mark_exc"] = RuntimeError("ledger down")
    with pytest.raises(ValueError, match="boom"):
        publish_cast("x.mp4", PLAN, "live", uploader=make_uploader(calls, exc=ValueError("boom")))


@pytest.mark.parametrize("env,expected", [
    (None, "private"), ("public", "public"), (" Unlisted ", "unlisted"),
    ("private", "private"), ("everyone", "private"), ("", "private"),
])
def test_privacy_resolution(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("CAST_YOUTUBE_PRIVACY", raising=False)
    else:
        monkeypatch.setenv("CAST_YOUTUBE_PRIVACY", env)
    assert cast_episode_metadata(PLAN, 1, None)["privacy"] == expected


def test_metadata_fields():
    m = cast_episode_metadata({**PLAN, "tier": "T1", "villain": None, "market_snapshot": None}, 3, None)
    assert m["theme"] == "EDT 단독 정비" and m["villain"] == "Debt Titan"
    assert m["episode"] == "C3" and m["track"] == "CAST" and m["line"] == "cast"
    assert m["market_snapshot"] == {}
    assert m["storyboard"][1]["narration"] == "변동성이 커질수록 원칙부터 점검하세요."


def test_hook_picks_largest_abs_change():
    hook = cast_episode_metadata(PLAN, 1, None)["storyboard"][0]["narration"]
    assert hook == "이번 주 나스닥 +2.50% — EDT 유니버스 히어로들이 움직였다."
    snap = {"SPX": {"change_pct": -3.1}, "VIX": {"change_pct": 2.0}}
    assert pub.build_hook(snap) == "이번 주 S&P500 -3.10% — EDT 유니버스 히어로들이 움직였다."
    assert pub.build_hook({"VIX": {"change_pct": 9.87}}).startswith("이번 주 VIX +9.87%")


def test_hook_fallback_without_data():
    assert pub.build_hook({}) == "이번 주 시장 변동 — EDT 유니버스 히어로들이 움직였다."
    assert pub.build_hook({"SPX": {"close": 1}}) == pub.HOOK_FALLBACK


def test_no_investment_solicitation_wording():
    m = cast_episode_metadata(PLAN, 1, None)
    text = " ".join(b["narration"] for b in m["storyboard"])
    for word in ("매수", "매도", "사세요", "파세요", "추천"):
        assert word not in text


@pytest.mark.parametrize("tier", ["T3", "T2", "T1"])
@pytest.mark.parametrize("snapshot", [PLAN["market_snapshot"], {}])
def test_real_build_title(tier, snapshot):
    pytest.importorskip("googleapiclient")
    try:  # src.publisher._primary_signal → pipeline_control → supabase 클라이언트 필요
        from supabase import create_client  # noqa: F401
    except ImportError:
        pytest.skip("supabase 클라이언트 미설치")
    from src.publisher import build_title
    title = build_title(cast_episode_metadata({**PLAN, "tier": tier, "market_snapshot": snapshot}, 7, None))
    assert "Ep.C7" in title and "CAST-T" not in title and "Ep.-" not in title
    assert len(title) <= 100


def test_removed_legacy_api():
    assert not hasattr(pub, "has_published_today_line")
    assert not hasattr(pub, "_YOUTUBE_SERVICE_UNSET")


# ── DN2026_0115 P2: 대본 기반 훅·takeaway, __main__publish 내레이션 연결 ─────────────
SCRIPT = {"version": 1, "source": "t", "ending_type": "x", "beats": [
    {"cut": 1, "role": "ESTABLISH_THREAT", "narration": "부채 거인이 도시를 짓눌렀다.", "situation_en": "a"},
    {"cut": 2, "role": "RESOLVE", "narration": "원칙을 지킨 쪽이 버텼다.", "situation_en": "b"},
    {"cut": 3, "role": "NEXT_OMEN", "narration": "다음 그림자가 다가온다.", "situation_en": "c"}]}


def test_metadata_hook_takeaway_from_script():
    m = cast_episode_metadata({**PLAN, "script": SCRIPT}, 2, None)
    assert m["storyboard"][0]["narration"] == "부채 거인이 도시를 짓눌렀다."
    assert m["storyboard"][1]["narration"] == "원칙을 지킨 쪽이 버텼다."
    assert m["theme"] == "히어로 총출동"


def test_metadata_script_without_resolve_falls_back_takeaway():
    beats = [b for b in SCRIPT["beats"] if b["role"] != "RESOLVE"]
    m = cast_episode_metadata({**PLAN, "script": {**SCRIPT, "beats": beats}}, 2, None)
    assert m["storyboard"][0]["narration"] == "부채 거인이 도시를 짓눌렀다."
    assert m["storyboard"][1]["narration"] == pub.TAKEAWAY


def test_metadata_without_script_unchanged():
    m = cast_episode_metadata(PLAN, 1, None)
    assert m["storyboard"][1]["narration"] == pub.TAKEAWAY
    assert m["storyboard"][0]["narration"].startswith("이번 주 나스닥")


def _publish_main_env(monkeypatch, tmp_path, *, script, narration):
    import importlib
    import json
    pm = importlib.import_module("src.cast.__main__publish")
    monkeypatch.chdir(tmp_path)
    art = tmp_path / "artifacts_cast"
    art.mkdir()
    plan = {**PLAN, "plan": PLAN["plan"][:3], "mode": "dry_run"}
    if script:
        plan["script"] = {**SCRIPT, "beats": list(reversed(SCRIPT["beats"]))}  # 순서 뒤섞여도 컷순 자막
    results = []
    for i in (1, 2, 3):
        p = art / f"cut_{i:02d}.mp4"
        p.write_bytes(b"\x00")
        results.append({"cut": i, "path": str(p), "duration_sec": 6.0})
    led = {"results": results}
    if narration:
        led["narration"] = [{"cut": i, "wav": "w", "sec": 3.0, "tempo": 1.0} for i in (1, 2, 3)]
    (art / "cast_plan.json").write_text(json.dumps(plan, ensure_ascii=False))
    (art / "generate_ledger.json").write_text(json.dumps(led))
    monkeypatch.setenv("CAST_EXECUTION_MODE", "dry_run")
    seen = {}

    def fake_compose(cuts, villain, out, **kw):
        seen.update(kw)
        return {"path": out, "duration_sec": 18.0, "thumbnail": "t.png", "segments": []}
    monkeypatch.setattr(pm, "compose", fake_compose)
    pm.main()
    return seen


def test_publish_main_passes_narration_and_captions(monkeypatch, tmp_path):
    seen = _publish_main_env(monkeypatch, tmp_path, script=True, narration=True)
    assert [n["cut"] for n in seen["narration"]] == [1, 2, 3]
    assert seen["captions"] == [b["narration"] for b in SCRIPT["beats"]]
    assert seen["market_snapshot"] == PLAN["market_snapshot"]


@pytest.mark.parametrize("script,narration", [(False, False), (False, True)])
def test_publish_main_legacy_compose(monkeypatch, tmp_path, script, narration):
    seen = _publish_main_env(monkeypatch, tmp_path, script=script, narration=narration)
    assert seen == {}


def test_publish_main_script_without_narration_blocked(monkeypatch, tmp_path):
    """R6-05 #7 — 대본이 있는데 내레이션이 없으면 무내레이션 발행 차단."""
    from src.cast.publish import PublishBlocked
    with pytest.raises(PublishBlocked, match="무내레이션"):
        _publish_main_env(monkeypatch, tmp_path, script=True, narration=False)



def test_cast_metadata_skips_thumbnail_and_playlist():
    """DN2026_0117 — CAST는 커스텀 썸네일·재생목록을 쓰지 않는다(회장 결정)."""
    m = cast_episode_metadata(PLAN, 1, "thumb.png")
    assert m["skip_thumbnail"] is True and m["skip_playlist"] is True
