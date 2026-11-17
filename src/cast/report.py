"""CAST-LINE 실행 리포트 — 텔레그램 능동 통지 (watchdog 계약 준용).

키는 GitHub Secrets(TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID)로만 공급하고
로그에 남기지 않는다. 키 부재는 실패가 아니라 스킵으로 처리한다(리포트 유실이
파이프라인을 막지 않는다 — run 결론 자체는 Actions 이력이 원장).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from src.cast import VERSION

logger = logging.getLogger("cast.report")

ARTIFACT_DIR = "artifacts_cast"   # DN2026_0114 — report job이 다운로드한 plan·cuts artifact


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
    except urllib.error.HTTPError as e:   # 토큰 만료·chat_id 오류(404/401 등)
        logger.warning("report_send_failed status=%d reason=%s", e.code, e.reason)
        return False
    except Exception as e:                # URLError·timeout·SSL 등 나머지 전부 — 원칙 완수(부록 Z)
        logger.warning("report_send_failed err=%s msg=%s", type(e).__name__, e)
        return False
    logger.info("report_sent ok=%s", ok)
    return ok


def build_text(run_conclusion: str, tier: str | None, total_cost: float | None,
               cuts_ok: int | None = None, mode: str | None = None) -> str:
    """실행 요약 텍스트 1건 — 값 없는 항목은 미확정으로 표기."""
    lines = [f"<b>CAST-LINE 리포트</b> — {run_conclusion}"]
    lines.append(f"tier: {tier or '미확정'}")
    if mode is not None:   # DN2026_0114
        lines.append(f"mode: {mode or '미확정'}")
    if total_cost is not None:
        lines.append(f"fal 비용: ${total_cost:.2f}")
    if cuts_ok is not None:
        lines.append(f"컷 완주: {cuts_ok}컷")
    return "\n".join(lines)


def _read_json(name: str) -> dict | None:
    """artifact 파일 1개 — 없거나 깨졌으면 None(미확정). # DN2026_0114"""
    path = os.path.join(ARTIFACT_DIR, name)
    try:
        with open(path) as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError) as e:
        logger.info("report_artifact_missing file=%s err=%s", name, type(e).__name__)
        return None


def build_conclusion() -> str:
    """plan/generate/publish job 결과 3개 표기 — env CAST_RESULT_*(needs.<job>.result). # DN2026_0114"""
    parts = []
    for job in ("plan", "generate", "publish"):
        parts.append(f"{job}={os.environ.get('CAST_RESULT_' + job.upper()) or '미확정'}")
    return " · ".join(parts)


def main() -> None:
    # 리포트는 파이프라인 결론을 바꿀 수 없다 — 어떤 예외도 스텝 실패로 전파하지 않는다(부록 Z-2).
    try:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
        plan_doc = _read_json("cast_plan.json") or {}
        gen_doc = _read_json("generate_ledger.json") or {}
        mode = plan_doc.get("mode") or gen_doc.get("mode")
        logger.info("[CAST] v%s 시작 stage=report mode=%s", VERSION, mode or "미확정")  # DN2026_0114
        cost = gen_doc.get("total_cost_usd")
        results = gen_doc.get("results")
        text = build_text(
            build_conclusion(),
            plan_doc.get("tier") or gen_doc.get("tier"),
            float(cost) if cost is not None else None,
            len(results) if isinstance(results, list) else None,
            mode=mode or "",
        )
        if gen_doc.get("error"):
            text += f"\n오류: {gen_doc['error']}"
        send_report(text)
    except Exception as e:
        logger.warning("report_main_failed err=%s msg=%s", type(e).__name__, e)


if __name__ == "__main__":
    main()
