"""report 테스트 — 키 부재 스킵·전송 계약 (네트워크 모킹)."""
import urllib.parse
import urllib.request

import pytest

import src.cast.report as rep


def test_send_report_skips_without_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert rep.send_report("t") is False


def test_send_report_posts_message(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    captured = {}

    class R:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["data"] = req.data
        return R()

    monkeypatch.setattr(rep.urllib.request, "urlopen", fake_urlopen)
    assert rep.send_report("hello") is True
    assert "api.telegram.org/bot" + "tok/" in captured["url"]
    assert urllib.parse.parse_qs(captured["data"].decode())["text"] == ["hello"]


def test_build_text_handles_missing_values():
    t = rep.build_text("success", None, None)
    assert "미확정" in t and "success" in t
    t2 = rep.build_text("success", "T3", 4.20, 7)
    assert "T3" in t2 and "$4.20" in t2 and "7컷" in t2


def test_send_report_swallows_http_error(monkeypatch):
    """부록 Y — 토큰·chat 오류(HTTP 404/401)도 리포트 유실은 파이프라인을 막지 않는다."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "badtok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "badchat")

    def fake_urlopen(req, timeout=30):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)

    monkeypatch.setattr(rep.urllib.request, "urlopen", fake_urlopen)
    assert rep.send_report("x") is False


def test_send_report_swallows_any_network_error(monkeypatch):
    """부록 Z — URLError·timeout 등 HTTPError 외 예외도 파이프라인을 막지 않는다."""
    import urllib.error as ue

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setattr(rep.urllib.request, "urlopen",
                        lambda req, timeout=30: (_ for _ in ()).throw(ue.URLError("dns fail")))
    assert rep.send_report("x") is False
    monkeypatch.setattr(rep.urllib.request, "urlopen",
                        lambda req, timeout=30: (_ for _ in ()).throw(TimeoutError("timed out")))
    assert rep.send_report("x") is False


def test_main_never_raises(monkeypatch):
    """부록 Z-2 — env 이상·전송 오류 등 어떤 경우에도 main은 예외를 던지지 않는다."""
    monkeypatch.setenv("CAST_RUN_CONCLUSION", "x")
    monkeypatch.setenv("CAST_RUN_TIER", "T3")
    monkeypatch.setenv("CAST_RUN_COST", "not-a-number")   # float 변환 실패 유도
    monkeypatch.setenv("CAST_RUN_CUTS", "7")
    monkeypatch.setattr(rep, "send_report", lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
    rep.main()   # 예외가 전파되지 않으면 통과


# ── DN2026_0114: 파일 기반 리포트 구성 ─────────────────────────────────────
import json


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(rep, "send_report", lambda t: sent.append(t) or True)
    return sent


def test_main_builds_text_from_artifacts(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "artifacts_cast"
    d.mkdir()
    (d / "cast_plan.json").write_text(json.dumps({"tier": "T3", "mode": "shadow", "plan": []}))
    (d / "generate_ledger.json").write_text(json.dumps(
        {"tier": "T3", "mode": "shadow", "total_cost_usd": 2.52, "results": [{}] * 7, "error": None}))
    monkeypatch.setenv("CAST_RESULT_PLAN", "success")
    monkeypatch.setenv("CAST_RESULT_GENERATE", "success")
    monkeypatch.setenv("CAST_RESULT_PUBLISH", "skipped")
    sent = _capture(monkeypatch)
    rep.main()
    t = sent[0]
    assert "plan=success · generate=success · publish=skipped" in t
    assert "tier: T3" in t and "mode: shadow" in t and "$2.52" in t and "7컷" in t


def test_main_without_artifacts_marks_unknown(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAST_RESULT_PLAN", "failure")
    monkeypatch.delenv("CAST_RESULT_GENERATE", raising=False)
    monkeypatch.setenv("CAST_RESULT_PUBLISH", "skipped")
    sent = _capture(monkeypatch)
    rep.main()
    t = sent[0]
    assert "plan=failure · generate=미확정 · publish=skipped" in t
    assert "tier: 미확정" in t and "mode: 미확정" in t and "fal 비용" not in t


def test_main_reports_generate_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "artifacts_cast"
    d.mkdir()
    (d / "generate_ledger.json").write_text(json.dumps(
        {"tier": "T1", "mode": "live", "total_cost_usd": 0.0, "results": [],
         "error": "GenerationError:cost_cap_month"}))
    (d / "cast_plan.json").write_text("{broken")
    sent = _capture(monkeypatch)
    rep.main()
    assert "cost_cap_month" in sent[0] and "tier: T1" in sent[0] and "0컷" in sent[0]
