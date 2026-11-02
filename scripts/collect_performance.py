"""DN2026_0092 M1 성과 측정 수집기 — 발행 영상 YouTube 통계를 일별 스냅샷으로 남긴다.

모드 (env RUN_MODE → src.performance.resolve_mode)
    dry_run : 조회 + 행 구성 + 로그 + artifacts/performance_rows.json. DB 쓰기 0.
    shadow  : dry_run + 직전 수집일 video_stats 읽기 → 조회수 증감 요약. DB 쓰기 0.
    live    : video_stats upsert(on_conflict=video_id,collected_date) + JSON 저장.

종료 코드 (STD-OBS-02)
    0 : 정상 (응답 누락 영상은 경고만)
    1 : YouTube 서비스 생성 실패 / 조회·쓰기 예외

로그·요약에 토큰·키·채널ID·영상ID 를 쓰지 않는다. 회차는 episode_no 로 표기한다.
실행: 레포 루트에서 `python scripts/collect_performance.py`
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import performance
from src.performance import VERSION

logger = logging.getLogger("collect_performance")

OUTPUT_PATH = Path("artifacts") / "performance_rows.json"
TOP_N = 5


def _start_line(mode: str) -> str:
    run_id = os.environ.get("GITHUB_RUN_ID") or "local"
    sha = (os.environ.get("GITHUB_SHA") or "")[:7] or "local"
    return f"[Performance] v{VERSION} 시작 | mode={mode} | run_id={run_id} | sha={sha}"


def _error_label(exc: BaseException) -> str:
    """예외 유형과 HTTP 상태만 남긴다(메시지에 요청 URL·ID 가 섞일 수 있다)."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    return f"{type(exc).__name__}" + (f" status={status}" if status else "")


def _write_json(payload: dict) -> Path:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return OUTPUT_PATH


def _shadow_lines(client, rows, collected_date) -> list[str]:
    """직전 수집일 대비 조회수 증감 요약 (읽기 전용, 실패 시 경고 후 계속)."""
    try:
        previous_date, previous = performance.load_previous_views(client, collected_date)
    except Exception as exc:  # noqa: BLE001 — 테이블 미생성 포함, 경고 후 계속
        logger.warning("shadow_previous_read_failed reason=%s continue=true", _error_label(exc))
        return ["- shadow 비교: 직전 수집 읽기 실패(경고, 계속)"]
    if previous_date is None:
        return ["- shadow 비교: 직전 수집일 없음"]
    matched = [r for r in rows if r["video_id"] in previous
               and previous[r["video_id"]] is not None and r["views"] is not None]
    delta = sum(r["views"] - previous[r["video_id"]] for r in matched)
    ranked = sorted(matched, key=lambda r: r["views"] - previous[r["video_id"]], reverse=True)[:TOP_N]
    lines = [f"- shadow 비교: 직전 수집일 {previous_date} | 비교 영상 {len(matched)} | 조회수 증감 합계 {delta:+d}"]
    for r in ranked:
        lines.append(f"  - 회차 {r['episode_no']}: {r['views'] - previous[r['video_id']]:+d}")
    return lines


def _summary_lines(mode, collected_date, rows, missing_episode_nos) -> list[str]:
    total_views = sum(r["views"] for r in rows if r["views"] is not None)
    top = sorted((r for r in rows if r["views"] is not None),
                 key=lambda r: r["views"], reverse=True)[:TOP_N]
    lines = [
        f"### YouTube Performance v{VERSION} ({mode})",
        f"- 수집일(KST): {collected_date}",
        f"- 행 수: {len(rows)}",
        f"- 응답 누락 영상 수: {len(missing_episode_nos)}",
        f"- 합계 조회수: {total_views}",
        f"- 조회수 상위 {TOP_N}회차:",
    ]
    lines += [f"  - 회차 {r['episode_no']}: {r['views']}" for r in top] or ["  - 없음"]
    return lines


def _emit_summary(lines: list[str]) -> None:
    for line in lines:
        logger.info(line)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def main() -> int:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
    mode = performance.resolve_mode(os.environ.get("RUN_MODE"))
    logger.info(_start_line(mode))

    from src.db_client import get_client
    from src.publisher import get_youtube_service

    try:
        youtube = get_youtube_service()
    except Exception as exc:  # noqa: BLE001 — STD-OBS-02 exit 1 계약
        logger.error("youtube_service_failed reason=%s", _error_label(exc))
        return 1
    if youtube is None:
        logger.error("youtube_service_unavailable reason=credentials_missing "
                     "(YOUTUBE_CLIENT_ID/YOUTUBE_CLIENT_SECRET/YOUTUBE_REFRESH_TOKEN)")
        return 1

    collected_date = performance.kst_today()
    try:
        client = get_client()
        videos = performance.list_published_videos(client)
        stats = performance.fetch_stats(youtube, [v["youtube_video_id"] for v in videos])
    except Exception as exc:  # noqa: BLE001 — STD-OBS-02 exit 1 계약
        logger.error("performance_fetch_failed reason=%s", _error_label(exc))
        if getattr(getattr(exc, "resp", None), "status", None) == 403:
            # 운영 토큰이 youtube.upload 단독이면 조회 API 가 403 이다(11-02 재생목록 추가도 같은 사유로 실패)
            logger.error("performance_scope_insufficient action=reissue_token "
                         "hint=scripts/issue_youtube_token.py 로 재동의 후 YOUTUBE_REFRESH_TOKEN 교체")
        return 1

    rows = performance.build_rows(videos, stats, collected_date, mode)
    missing = [v.get("episode_no") for v in videos if v["youtube_video_id"] not in stats]
    if missing:
        logger.warning("performance_missing_videos count=%d episode_nos=%s", len(missing), missing)

    if mode == "live":
        try:
            performance.save_rows(client, rows)
        except Exception as exc:  # noqa: BLE001 — STD-OBS-02 exit 1 계약
            logger.error("performance_save_failed reason=%s", _error_label(exc))
            return 1
        logger.info("performance_saved table=%s rows=%d", performance.TABLE, len(rows))
    else:
        logger.info("performance_db_write_skipped mode=%s rows=%d", mode, len(rows))

    path = _write_json({
        "version": VERSION, "mode": mode, "collected_date": collected_date,
        "rows": rows, "missing_episode_nos": missing,
    })
    logger.info("performance_rows_written path=%s", path)

    lines = _summary_lines(mode, collected_date, rows, missing)
    if mode == "shadow":
        lines += _shadow_lines(client, rows, collected_date)
    _emit_summary(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
