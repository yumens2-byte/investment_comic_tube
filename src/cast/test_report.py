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
