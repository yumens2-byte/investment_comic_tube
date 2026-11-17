"""CAST-LINE 실행 리포트 — 텔레그램 능동 통지 (watchdog 계약 준용).

키는 GitHub Secrets(TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID)로만 공급하고
로그에 남기지 않는다. 키 부재는 실패가 아니라 스킵으로 처리한다(리포트 유실이
파이프라인을 막지 않는다 — run 결론 자체는 Actions 이력이 원장).
"""
from __future__ import annotations

import logging
import os
import urllib.parse
import urllib.request

logger = logging.getLogger("cast.report")


def send_report(text: str) -> bool:
    """텔레그램 sendMessage 1건. 키 부재 시 스킵(False), 전송 실패는 예외 외부 처리."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        logger.warning("report_skipped reason=missing_credentials")
        return False
    payload = urllib.parse.urlencode({
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=payload)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ok = r.status == 200
    except urllib.error.HTTPError as e:   # 토큰 만료·chat_id 오류 등 — 리포트 유실이 파이프라인을 막지 않는다
        logger.warning("report_send_failed status=%d reason=%s", e.code, e.reason)
        return False
    logger.info("report_sent ok=%s", ok)
    return ok


def build_text(run_conclusion: str, tier: str | None, total_cost: float | None,
               cuts_ok: int | None = None) -> str:
    """실행 요약 텍스트 1건 — 값 없는 항목은 미확정으로 표기."""
    lines = [f"<b>CAST-LINE 리포트</b> — {run_conclusion}"]
    lines.append(f"tier: {tier or '미확정'}")
    if total_cost is not None:
        lines.append(f"fal 비용: ${total_cost:.2f}")
    if cuts_ok is not None:
        lines.append(f"컷 완주: {cuts_ok}컷")
    return "\n".join(lines)


def main() -> None:
    conclusion = os.environ.get("CAST_RUN_CONCLUSION", "unknown")
    tier = os.environ.get("CAST_RUN_TIER")
    cost = os.environ.get("CAST_RUN_COST")
    cuts = os.environ.get("CAST_RUN_CUTS")
    text = build_text(
        conclusion, tier,
        float(cost) if cost else None,
        int(cuts) if cuts else None,
    )
    send_report(text)


if __name__ == "__main__":
    main()
