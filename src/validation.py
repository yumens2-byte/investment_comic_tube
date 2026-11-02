"""파이프라인 입력 사전 검증 (fail-fast).

원칙:
  스토리라인은 시장 지표를 근거로 만들어진다. 지표가 비어 있으면 "확인불가"가 섞인
  근거 없는 콘텐츠가 발행되고, 그게 DB와 YouTube에 영구히 남는다. 품질 저하를
  degraded 로 기록만 하고 발행하는 것보다, 애초에 발행하지 않는 편이 낫다.

  따라서 필수 지표가 하나라도 없으면 에피소드 생성 전에 중단한다.
  검증은 start_episode() 이전에 수행되므로 실패 시 DB에 고아 row가 남지 않는다.
"""

from __future__ import annotations

import logging
import math
import os

logger = logging.getLogger(__name__)

VERSION = "1.1.0"  # DN2026_0092: 휴장 스킵·세션 지연 구분 게이트

# 빌런 판정(market_regime)에 실제로 사용되는 지표. 하나라도 없으면 판정 근거가 무너진다.
REQUIRED_INDICATORS = ["TNX", "VIX", "NASDAQ", "SPX", "DXY"]

# 내레이션 색채용. 없어도 판정에는 지장이 없으므로 경고만 남긴다.
OPTIONAL_INDICATORS = ["GOLD", "OIL"]

# 지표별 필수 필드. sma20/dev_pct 는 이력이 짧으면 없을 수 있으므로 필수에서 제외한다
# (market_regime 이 절대값으로 폴백하도록 설계돼 있다).
REQUIRED_FIELDS = ["close", "change_pct"]

EXPECTED_BEAT_COUNT = 6


class ValidationError(Exception):
    """검증 실패. 파이프라인을 중단시킨다."""


class MarketDataIncomplete(ValidationError):
    pass


class StoryboardIncomplete(ValidationError):
    pass


class RenderEnvironmentInvalid(ValidationError):
    pass


class DuplicatePublish(ValidationError):
    pass


class NoNewMarketSession(ValidationError):
    """DN2026_0092: 직전 발행 회차 이후 새로 마감된 미국 거래 세션이 없다(휴장·주말)."""


class MarketSessionLagging(ValidationError):
    """DN2026_0092 R6-01 W1: 새 세션이 마감됐는데 수집값이 그 이전 세션이다(폴백 지연). 스킵이 아니라 중단."""


def _strict_mode() -> bool:
    """기본은 엄격 모드. STRICT_VALIDATION=false 로만 완화할 수 있다."""
    return os.getenv("STRICT_VALIDATION", "true").strip().lower() != "false"


def validate_market_data(market_data: dict) -> None:
    """필수 시장 지표가 모두 수집됐는지 검증한다.

    누락 시 MarketDataIncomplete 를 발생시켜 파이프라인을 중단한다.
    """
    logger.info("market_validation_started required=%s", REQUIRED_INDICATORS)

    if not market_data:
        raise MarketDataIncomplete("시장 데이터가 비어 있다")

    problems: list[str] = []
    nonfinite: list[str] = []
    for name in REQUIRED_INDICATORS:
        metric = market_data.get(name)
        if not isinstance(metric, dict):
            problems.append(f"{name}: 지표 자체가 없음")
            continue
        for field in REQUIRED_FIELDS:
            value = metric.get(field)
            if value is None:
                problems.append(f"{name}.{field}=None")
            elif isinstance(value, bool):
                nonfinite.append(f"{name}.{field}(bool)")
            elif not isinstance(value, (int, float)):
                problems.append(f"{name}.{field} 타입 이상({type(value).__name__})")
            elif not math.isfinite(value):
                nonfinite.append(f"{name}.{field}")

    # NaN/Inf/bool은 비교·반올림·대본 생성에서 위험하므로 완화 모드에도 차단한다.
    if nonfinite:
        raise MarketDataIncomplete(f"유한하지 않은 시장 지표 -- 발행 중단: {', '.join(nonfinite)}")

    missing_optional = [
        name
        for name in OPTIONAL_INDICATORS
        if not isinstance(market_data.get(name), dict)
        or market_data[name].get("close") is None
    ]
    if missing_optional:
        logger.warning("market_validation_optional_missing indicators=%s", missing_optional)

    if problems:
        detail = "; ".join(problems)
        if _strict_mode():
            logger.error("market_validation_failed problems=%s", detail)
            raise MarketDataIncomplete(f"필수 시장 지표 누락 -- 발행 중단: {detail}")
        logger.warning("market_validation_bypassed problems=%s", detail)
        return

    logger.info(
        "market_validation_passed required=%s optional_missing=%s",
        len(REQUIRED_INDICATORS), len(missing_optional),
    )


def validate_storyboard(storyboard: list[dict]) -> None:
    """스토리보드가 완전한지 검증한다.

    비트 수 부족이나 빈 내레이션은 영상에 빈 장면/무음 구간을 만들므로 중단한다.
    """
    logger.info("storyboard_validation_started")

    if not storyboard:
        raise StoryboardIncomplete("스토리보드가 비어 있다")

    problems: list[str] = []
    if len(storyboard) != EXPECTED_BEAT_COUNT:
        problems.append(f"비트 수 {len(storyboard)} != 기대 {EXPECTED_BEAT_COUNT}")

    for idx, beat in enumerate(storyboard):
        if not isinstance(beat, dict):
            problems.append(f"beat[{idx}] 형식 이상")
            continue
        for field in ("beat", "scene", "narration"):
            value = beat.get(field)
            if not value or not str(value).strip():
                problems.append(f"beat[{idx}].{field} 비어있음")

    if problems:
        detail = "; ".join(problems)
        if _strict_mode():
            logger.error("storyboard_validation_failed problems=%s", detail)
            raise StoryboardIncomplete(f"스토리보드 불완전 -- 발행 중단: {detail}")
        logger.warning("storyboard_validation_bypassed problems=%s", detail)
        return

    logger.info("storyboard_validation_passed beats=%s", len(storyboard))


def validate_render_environment() -> None:
    """렌더링 환경이 정상인지 검증한다.

    한글 폰트가 없으면 훅 자막이 전부 두부(□)로 렌더링된다. 그 영상이 발행되면
    YouTube에 영구히 남으므로, 지표 누락과 동일하게 사전에 중단한다.
    """
    logger.info("render_env_validation_started")

    from src.renderer import find_kr_font

    font = find_kr_font()
    if font:
        logger.info("render_env_validation_passed kr_font=%s", font)
        return

    detail = (
        "한글 글리프 폰트를 찾을 수 없다 -- 자막이 두부(□)로 렌더링된다. "
        "워크플로우의 'apt-get install fonts-noto-cjk' 설치 여부 또는 "
        "KR_FONT_PATH 환경변수를 확인하라."
    )
    if _strict_mode():
        logger.error("render_env_validation_failed reason=%s", detail)
        raise RenderEnvironmentInvalid(detail)
    logger.warning("render_env_validation_bypassed reason=%s", detail)


def validate_not_published_today() -> None:
    """오늘 이미 발행됐으면 중단한다.

    같은 날 재실행은 동일 시세를 받아 사실상 같은 이야기를 두 번 만든다.
    STRICT_VALIDATION=false 면 경고만 남기고 진행한다(의도적 재발행용).
    """
    from src.drive_manager import has_published_today

    if not has_published_today():
        return

    detail = "오늘 이미 발행된 회차가 있다 -- 같은 시세로 중복 발행 방지"
    if _strict_mode():
        logger.error("duplicate_publish_blocked reason=%s", detail)
        raise DuplicatePublish(detail)
    logger.warning("duplicate_publish_bypassed reason=%s", detail)


# DN2026_0092: 세션 날짜는 거래소 마감 지표(SPX → NASDAQ 순)의 observed_date 로 판정한다.
SESSION_KEYS = ("SPX", "NASDAQ")


def market_session_date(snapshot: dict | None) -> str | None:
    """시장 스냅숏이 어느 미국 거래 세션의 마감값인지(YYYY-MM-DD). 판정 불가면 None."""
    for key in SESSION_KEYS:
        metric = (snapshot or {}).get(key)
        if isinstance(metric, dict) and metric.get("observed_date"):
            return str(metric["observed_date"])[:10]
    return None


def validate_new_market_session(market_data: dict, *, enforce: bool = True) -> None:
    """직전 발행 회차와 같은 거래 세션이면 유료 생성 전에 발행을 건너뛴다.

    DN2026_0092: 주말·미국 휴장일에는 같은 금요일 종가로 회차가 반복 발행됐다
    (Ep.40~42·45~46·49~50·53~54). 회장 결정 '미장 휴장 시 발행 스킵'.
    판정 불가(세션 날짜 누락)는 기존 검증(provenance)에 맡기고 진행한다.
    enforce=False(파일럿)는 판정만 기록하고 진행한다.
    """
    from src.drive_manager import fetch_latest_episode_state

    current = market_session_date(market_data)
    previous = market_session_date(fetch_latest_episode_state().get("market_snapshot"))
    if current is None or previous is None:
        logger.warning("market_session_unknown current=%s previous=%s -- skip 판정 생략", current, previous)
        return
    if current > previous:
        logger.info("market_session_new current=%s previous=%s", current, previous)
        return
    # R6-01 W1: 거래소 달력상 직전 회차 이후 세션이 이미 마감됐다면 휴장이 아니라 데이터 지연이다
    from src.collector import _last_completed_session
    completed = _last_completed_session()
    completed = completed.isoformat() if completed else None
    if completed and completed > previous:
        logger.error("market_session_lagging current=%s previous=%s completed=%s", current, previous, completed)
        raise MarketSessionLagging(
            f"market_session_lagging current={current} previous={previous} completed={completed}")
    if not enforce:
        logger.warning("market_session_repeat_pilot current=%s previous=%s -- 운영이면 발행 스킵", current, previous)
        return
    logger.warning("market_session_repeat current=%s previous=%s completed=%s", current, previous, completed)
    raise NoNewMarketSession(f"no_new_market_session current={current} previous={previous}")
