"""CAST-LINE 발행 — 원장 선기록(fail-closed) → YouTube 업로드 (DN2026_0114).

- 중복 게이트는 CAST 원장(src.cast.ledger.claim_upload)으로 일원화한다.
  episodes.line 기반 has_published_today_line(003)과 "unset" 센티널 방식은 폐기.
- 업로드는 src.publisher.upload_to_youtube 재사용(함수 내부 지연 import).
"""
from __future__ import annotations

import logging
import os

from src.cast import ledger

try:
    from src.cast import VERSION
except ImportError:
    VERSION = "1.1.0"

logger = logging.getLogger("cast.publish")

# DN2026_0114 제목 theme — "CAST-T3" 노출 금지
TIER_THEME = {"T3": "히어로 총출동", "T2": "히어로 출동", "T1": "EDT 단독 정비"}
PRIVACY_VALUES = ("private", "unlisted", "public")
HOOK_LABELS = {"SPX": "S&P500", "NASDAQ": "나스닥", "VIX": "VIX"}
HOOK_FALLBACK = "이번 주 시장 변동 — EDT 유니버스 히어로들이 움직였다."
TAKEAWAY = "변동성이 커질수록 원칙부터 점검하세요."
DEFAULT_VILLAIN = "Debt Titan"


class PublishBlocked(RuntimeError):
    """발행 차단 — 모드 게이트·중복 게이트·원장 오류·업로드 결과 없음."""


def resolve_privacy() -> str:
    """CAST_YOUTUBE_PRIVACY ∈ {private, unlisted, public}, 그 외는 private 강등.  # DN2026_0114"""
    value = (os.getenv("CAST_YOUTUBE_PRIVACY") or "").strip().lower()
    return value if value in PRIVACY_VALUES else "private"


def build_hook(market_snapshot: dict | None) -> str:
    """SPX/NASDAQ/VIX 중 |change_pct| 최대 지표로 훅 문장.  # DN2026_0114"""
    best = None
    for key in ("SPX", "NASDAQ", "VIX"):
        metric = (market_snapshot or {}).get(key)
        change = metric.get("change_pct") if isinstance(metric, dict) else None
        if isinstance(change, bool) or not isinstance(change, (int, float)):
            continue
        if best is None or abs(change) > abs(best[1]):
            best = (key, float(change))
    if best is None:
        return HOOK_FALLBACK
    key, change = best
    return f"이번 주 {HOOK_LABELS[key]} {change:+.2f}% — EDT 유니버스 히어로들이 움직였다."


def cast_episode_metadata(plan_doc: dict, episode_no: int, thumbnail: str | None) -> dict:
    """src.publisher.build_title/build_description 이 읽는 키로 메타데이터 구성.  # DN2026_0114"""
    tier = plan_doc.get("tier")
    if tier not in TIER_THEME:
        raise PublishBlocked(f"알 수 없는 tier={tier!r}")
    snapshot = plan_doc.get("market_snapshot") or {}
    return {
        "theme": TIER_THEME[tier],
        "villain": plan_doc.get("villain") or DEFAULT_VILLAIN,
        "market_snapshot": snapshot,
        "episode": f"C{episode_no}",
        "track": "CAST",
        "line": "cast",
        "tier": tier,
        "privacy": resolve_privacy(),
        "thumbnail_source": thumbnail,
        "storyboard": [{"narration": build_hook(snapshot)}, {"narration": TAKEAWAY}],
    }


def publish_cast(video_path: str, plan_doc: dict, mode: str, thumbnail: str | None = None,
                 *, uploader=None, on_uploaded=None) -> str | None:
    """발행 진입점 — dry_run/shadow는 원장·업로드 0, live만 진행.  # DN2026_0114

    순서: claim_upload(선기록, fail-closed) → next_episode_no → metadata → uploader
          → mark('published') / 업로드 착수 후 예외·video_id 없음은 'uploading' 유지(잠금, R6-02·03).
    주의: 업로드 성공 후 mark('published') 자체가 실패하면 원장 행은 'uploading'으로
          남고, 같은 날 재실행은 claim_upload 에서 DuplicatePublish 로 차단된다.
          이는 의도된 동작이다 — 원장에 없는 고아 업로드(중복 발행) 방지를 우선한다.
          이 경우 운영자가 YouTube 실재를 확인하고 원장을 수동 정정한다.
    반환: video_id (live 성공) 또는 None (dry_run/shadow).
    """
    if mode in ("dry_run", "shadow"):
        logger.info("[CAST] publish skipped mode=%s — 원장·업로드 0", mode)
        return None
    if mode != "live":
        raise PublishBlocked(f"알 수 없는 mode={mode!r}")

    run_id = plan_doc.get("run_id")
    run_date = plan_doc.get("run_date_kst")
    if not run_id or not run_date:
        raise PublishBlocked("run_id/run_date_kst 누락 — 원장 선기록 불가")
    # DN2026_0114 (R6-03 #3): 파일 부재는 claim 전에 확정한다 — 업로드 착수 후 예외는 전부 잠금 유지
    if not os.path.exists(video_path):
        raise PublishBlocked(f"합성 영상 없음 — 업로드 전 차단: {video_path}")
    try:
        ledger.claim_upload(run_id, run_date)
    except ledger.DuplicatePublish as e:
        raise PublishBlocked(f"같은 날 CAST 발행 이력 — 중복 차단: {e}") from e
    except ledger.CastLedgerUnavailable as e:
        raise PublishBlocked(f"원장 사용 불가 — 업로드 전 차단: {e}") from e

    episode_no = ledger.next_episode_no()
    metadata = cast_episode_metadata(plan_doc, episode_no, thumbnail)
    if uploader is None:
        from src.publisher import upload_to_youtube as uploader
    logger.info("[CAST] publish upload start run_id=%s episode=C%s privacy=%s",
                run_id, episode_no, metadata["privacy"])
    try:
        video_id = uploader(video_path, metadata)
    except Exception as e:
        # DN2026_0114 (R6-02 #2): 업로드 착수 후 예외는 서버 측 완료 여부를 알 수 없다 →
        # 'uploading' 유지(같은 날 재업로드 차단, fail-closed) + error만 기록. Studio 실재 확인 후 수동 해제.
        # (R6-03 #3) 파일 부재는 claim 전에 차단하므로 여기서는 예외 종류와 무관하게 잠금을 유지한다.
        status = "uploading"
        try:
            ledger.mark(run_id, status, error=type(e).__name__)
        except Exception as mark_err:  # noqa: BLE001 — 원 예외 우선
            logger.error("[CAST] publish mark_failed status=%s err=%s", status, type(mark_err).__name__)
        raise
    if not video_id:
        # publisher는 서비스 미구성(업로드 미착수) 또는 응답 id 부재 시 None — 후자도 실재 불명이므로 잠금 유지
        ledger.mark(run_id, "uploading", error="no_video_id")
        raise PublishBlocked("업로드 결과 video_id 없음 — 잠금 유지, YouTube Studio 확인 후 수동 해제")
    if on_uploaded is not None:   # DN2026_0114 (R6-02 #4): 원장 mark 전에 video_id를 결과 파일에 남긴다
        try:
            on_uploaded(video_id)
        except Exception as cb_err:  # noqa: BLE001 — 기록 실패가 발행 결과를 바꾸지 않는다
            logger.warning("[CAST] on_uploaded_failed err=%s", type(cb_err).__name__)
    ledger.mark(run_id, "published", youtube_video_id=video_id, episode_no=episode_no)
    # DN2026_0114 (R6-01 #8): 공개 로그에 video_id 전체를 남기지 않는다(원장·artifact에만 기록)
    logger.info("[CAST] publish done run_id=%s episode=C%s video_id=%s***", run_id, episode_no, str(video_id)[:3])
    return video_id
