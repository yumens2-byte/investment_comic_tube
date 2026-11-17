"""CAST-LINE 전용 원장 — Supabase public.cast_runs (migration 004). # DN2026_0114

원칙
- dry_run에서는 이 모듈을 호출하지 않는다(호출부 __main__generate가 보장).
- DB 선기록: 생성 전 open_run, 각 fal 제출 직전 add_cost — 과금 추정액이 먼저 남는다.
- fail-closed: DB 예외는 CastLedgerUnavailable로 감싸 raise(원장 없이 과금 진행 금지).
- 중복 발행 차단: claim_upload — 같은 run_date_kst에 uploading/published 행이 있으면 DuplicatePublish.
- 키 값·URL은 로그에 남기지 않는다(예외 메시지는 유형명만 기록).
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timezone

logger = logging.getLogger("cast.ledger")

TABLE = "cast_runs"  # DN2026_0114
PUBLISH_LOCK_STATUSES = ("uploading", "published")


class CastLedgerUnavailable(RuntimeError):
    """원장 접근 실패 — fail-closed(과금·발행 진행 금지)."""


class DuplicatePublish(RuntimeError):
    """같은 KST 날짜에 이미 업로드 중이거나 발행된 회차가 있다."""


def _table():
    try:
        from src.db_client import get_client  # 지연 import — dry_run·테스트에서 supabase 미요구
        return get_client().table(TABLE)
    except Exception as e:  # 환경변수 부재 포함
        logger.error("ledger_unavailable op=client err=%s", type(e).__name__)
        raise CastLedgerUnavailable(f"cast_runs 접근 불가({type(e).__name__})") from e


def _wrap(op: str, exc: Exception) -> CastLedgerUnavailable:
    logger.error("ledger_unavailable op=%s err=%s", op, type(exc).__name__)
    return CastLedgerUnavailable(f"cast_runs {op} 실패({type(exc).__name__})")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_run(run_id: str, run_date_kst: str, tier: str, mode: str, market_snapshot) -> float:
    """생성 시작 기록. 반환값 = 이 run_id의 기존 누적 비용(재실행 시 회당 상한에 합산).

    DN2026_0114 (R6-01 #2): 같은 run_id 재실행(Re-run all jobs)이 이미 uploading·published인
    행을 generating으로 되돌리면 날짜 잠금이 풀려 중복 발행된다 → 그런 행이면 DuplicatePublish.
    기존 행이 있으면 run_date_kst·cost_usd는 덮어쓰지 않는다(최초 실행 기준 유지).
    """
    tbl = _table()
    try:
        res = tbl.select("run_id,status,cost_usd").eq("run_id", run_id).limit(1).execute()
        rows = res.data or []
    except Exception as e:
        raise _wrap("open_run_check", e) from e
    if rows and rows[0].get("status") in PUBLISH_LOCK_STATUSES:
        raise DuplicatePublish(f"run_id 이미 {rows[0].get('status')} — 재생성 차단")
    try:
        if rows:
            tbl.update({"status": "generating", "tier": tier, "mode": mode,
                        "updated_at": _now_iso()}).eq("run_id", run_id).execute()
            prior = round(float(rows[0].get("cost_usd") or 0), 2)
        else:
            tbl.upsert({"run_id": run_id, "run_date_kst": run_date_kst, "tier": tier, "mode": mode,
                        "status": "generating", "market_snapshot": market_snapshot,
                        "updated_at": _now_iso()}, on_conflict="run_id").execute()
            prior = 0.0
    except Exception as e:
        raise _wrap("open_run", e) from e
    logger.info("ledger_open tier=%s mode=%s prior=$%.2f", tier, mode, prior)
    return prior


def add_cost(run_id: str, usd: float) -> float:
    """현재 cost_usd + usd로 갱신하고 새 합계를 반환한다(제출 직전 선기록)."""
    tbl = _table()
    try:
        res = tbl.select("cost_usd").eq("run_id", run_id).limit(1).execute()
        rows = res.data or []
        if not rows:
            raise LookupError("run_id 행 없음")
        new_total = round(float(rows[0].get("cost_usd") or 0) + float(usd), 2)
        tbl.update({"cost_usd": new_total, "updated_at": _now_iso()}).eq("run_id", run_id).execute()
    except Exception as e:
        raise _wrap("add_cost", e) from e
    logger.info("ledger_cost run_id=%s add=$%.2f total=$%.2f", run_id, usd, new_total)
    return new_total


def _month_bounds(run_date_kst: str) -> tuple[str, str]:
    d = date.fromisoformat(str(run_date_kst)[:10])
    last = calendar.monthrange(d.year, d.month)[1]
    return d.replace(day=1).isoformat(), d.replace(day=last).isoformat()


def month_spent_usd(run_date_kst: str) -> float:
    """같은 달(1일~말일) run_date_kst 행들의 cost_usd 합."""
    start, end = _month_bounds(run_date_kst)
    tbl = _table()
    try:
        res = tbl.select("cost_usd").gte("run_date_kst", start).lte("run_date_kst", end).execute()
        total = sum(float(r.get("cost_usd") or 0) for r in (res.data or []))
    except Exception as e:
        raise _wrap("month_spent", e) from e
    return round(total, 2)


def mark(run_id: str, status: str, **fields) -> None:
    """status·updated_at·추가 필드 갱신."""
    payload = {"status": status, "updated_at": _now_iso(), **fields}
    tbl = _table()
    try:
        tbl.update(payload).eq("run_id", run_id).execute()
    except Exception as e:
        raise _wrap("mark", e) from e
    logger.info("ledger_mark run_id=%s status=%s", run_id, status)


def _is_unique_violation(exc: Exception) -> bool:
    msg = f"{exc} {getattr(exc, 'code', '')}".lower()
    return "duplicate" in msg or "23505" in msg


def claim_upload(run_id: str, run_date_kst: str) -> None:
    """같은 KST 날짜 1회 발행 잠금. 이미 잠긴 날짜면 DuplicatePublish."""
    tbl = _table()
    try:
        # DN2026_0114 (R6-02 #3): 잠금 날짜는 원장에 저장된 자기 행 날짜(최초 실행 기준)를 쓴다
        own = tbl.select("run_date_kst").eq("run_id", run_id).limit(1).execute().data or []
    except Exception as e:
        raise _wrap("claim_own", e) from e
    # DN2026_0114 (R6-03 #1): 자기 행이 없으면 잠금이 기록되지 않는다 → 업로드 전 차단(fail-closed)
    if not own:
        raise CastLedgerUnavailable("claim: run_id 원장 행 없음 — generate(open_run) 미실행")
    if own[0].get("run_date_kst"):
        run_date_kst = str(own[0]["run_date_kst"])[:10]
    try:
        res = (tbl.select("run_id,status").eq("run_date_kst", run_date_kst)
               .in_("status", list(PUBLISH_LOCK_STATUSES)).execute())
        # DN2026_0114 (R6-01 #1): 자기 행도 잠금 판정에 포함 — 업로드 후 mark 실패로 'uploading'이
        # 남은 상태에서 같은 run_id 재실행(Re-run failed jobs)이 재업로드하지 못하게 한다.
        rows = res.data or []
    except Exception as e:
        raise _wrap("claim_check", e) from e
    if rows:
        raise DuplicatePublish(f"run_date_kst={run_date_kst} 이미 {rows[0].get('status')} 행 존재")
    try:
        tbl.update({"status": "uploading", "updated_at": _now_iso()}).eq("run_id", run_id).execute()
    except Exception as e:
        if _is_unique_violation(e):   # cast_runs_one_publish_per_day 부분 unique 인덱스 위반
            raise DuplicatePublish(f"run_date_kst={run_date_kst} 동시 잠금 충돌(23505)") from e
        raise _wrap("claim_upload", e) from e
    logger.info("ledger_mark run_id=%s status=uploading", run_id)


def next_episode_no() -> int:
    """status='published' 행 수 + 1."""
    tbl = _table()
    try:
        res = tbl.select("run_id", count="exact").eq("status", "published").execute()
        n = res.count if getattr(res, "count", None) is not None else len(res.data or [])
    except Exception as e:
        raise _wrap("next_episode_no", e) from e
    return int(n) + 1


# ---------- DN2026_0115 P2 스토리 레이어 (migration 005: villain·story_state·script) ----------
STORY_STATUSES = ("published",)  # DN2026_0115
STORY_STATUSES_WITH_GENERATED = ("published", "generated")  # DN2026_0115 — shadow 연속성 관찰용


def record_story(run_id: str, villain: str, story_state: dict, script: dict) -> None:
    """cast_runs의 villain·story_state·script 갱신. 실패 시 CastLedgerUnavailable. # DN2026_0115"""
    tbl = _table()
    try:
        tbl.update({"villain": villain, "story_state": story_state, "script": script,
                    "updated_at": _now_iso()}).eq("run_id", run_id).execute()
    except Exception as e:
        raise _wrap("record_story", e) from e
    logger.info("ledger_story run_id=%s villain=%s", run_id, villain)


def fetch_last_story(include_generated: bool, exclude_run_id: str | None = None) -> dict | None:
    """story_state가 있는 최신 1행(run_date_kst desc, created_at desc). 없으면 None. # DN2026_0115

    include_generated=False → published만, True → published+generated(shadow 연속성).
    DB 오류는 CastLedgerUnavailable(fail-closed — 연속성 추측 진행 금지).
    """
    statuses = STORY_STATUSES_WITH_GENERATED if include_generated else STORY_STATUSES
    tbl = _table()
    try:
        res = (tbl.select("run_id,run_date_kst,villain,story_state,created_at")
               .in_("status", list(statuses))
               .not_.is_("story_state", "null")
               # DN2026_0115 통합: 같은 run 재실행이 자기 행을 '직전 회차'로 잡지 않게 제외
               .neq("run_id", exclude_run_id or "")
               .order("run_date_kst", desc=True)
               .order("created_at", desc=True)
               .limit(1).execute())
        rows = res.data or []
    except Exception as e:
        raise _wrap("fetch_last_story", e) from e
    if not rows:
        logger.info("ledger_story_prev none include_generated=%s", include_generated)
        return None
    row = rows[0]
    state = row.get("story_state") or {}
    out = {"run_id": row.get("run_id"), "run_date_kst": str(row.get("run_date_kst") or "")[:10] or None,
           "villain": row.get("villain") or state.get("villain"), "story_state": state}
    logger.info("ledger_story_prev run_id=%s villain=%s", out["run_id"], out["villain"])
    return out
