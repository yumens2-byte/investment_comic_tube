import logging
import os
import subprocess

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

# 토큰 발급 시 요청할 스코프. scripts/issue_youtube_token.py 가 사용한다.
# playlistItems.insert 는 youtube.upload 로는 403 이 나므로 force-ssl 이 필요하다.
#
# 주의: 이 목록을 갱신(refresh) 요청에 그대로 넣으면 안 된다.
# Google 은 요청 스코프가 발급 당시 스코프의 부분집합인지 검사하므로,
# 아직 upload 스코프로만 발급된 토큰에 force-ssl 을 요구하면
# invalid_scope 로 갱신 자체가 거부되어 파이프라인 전체가 죽는다(2026-09-02 사고).
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

PLAYLIST_TITLE = os.getenv("YOUTUBE_PLAYLIST_TITLE", "EDT_UNIVERSE_INVEST_AREA99")

# 지표 표시명. 설명란에 노출되는 순서이기도 하다.
MARKET_LABELS = [
    ("SPX", "S&P 500", ""),
    ("NASDAQ", "나스닥", ""),
    ("TNX", "미 10년물 금리", "%"),
    ("VIX", "공포지수 VIX", ""),
    ("DXY", "달러인덱스", ""),
    ("GOLD", "금", ""),
    ("OIL", "WTI 유가", ""),
]

# 모든 영상에 검색어를 나열하면 주제가 흐려진다. 고정 태그는 브랜드/포맷/분야만
# 남기고, 실제 회차의 시장 데이터와 빌런에 맞는 태그를 아래에서 조합한다.
BASE_HASHTAGS = ["#Shorts", "#EDTUniverse", "#미국증시", "#주식투자"]

VILLAIN_HASHTAGS = {
    "Debt Titan": ["#미국채금리", "#금리", "#긴축"],
    "Chaos Reaper": ["#VIX", "#변동성", "#리스크관리"],
    "Bull Brute": ["#상승장", "#증시랠리", "#시장모멘텀"],
}

MARKET_HASHTAGS = {
    "SPX": ["#SP500"],
    "NASDAQ": ["#나스닥"],
    "TNX": ["#미국채금리"],
    "VIX": ["#VIX"],
    "DXY": ["#달러인덱스"],
    "GOLD": ["#금값"],
    "OIL": ["#국제유가"],
}

MAX_DESCRIPTION_HASHTAGS = 10


class YouTubeAuthenticationError(RuntimeError):
    """Raised when YouTube OAuth credentials need operator action."""


def get_youtube_service():
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        logger.warning("youtube_credentials_missing upload_skipped=true")
        return None

    # scopes 를 넘기지 않는다. 넘기면 갱신 요청에 그대로 실려 나가
    # 토큰이 가지지 않은 스코프를 요구하게 되고 invalid_scope 로 실패한다.
    # 생략하면 Google 이 토큰에 실제로 부여된 스코프를 그대로 돌려준다.
    credentials = Credentials(
        token=None, refresh_token=refresh_token, token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id, client_secret=client_secret,
    )
    try:
        credentials.refresh(Request())
    except RefreshError as exc:
        # 사유를 고정 문자열로 찍으면 원인을 오진한다(invalid_scope 를 만료로 착각한 사례).
        logger.error(
            "youtube_oauth_refresh_failed action=replace_YOUTUBE_REFRESH_TOKEN reason=%s", exc
        )
        raise YouTubeAuthenticationError(
            "YouTube refresh token is expired or revoked. Re-authorize the channel and "
            "replace the YOUTUBE_REFRESH_TOKEN GitHub Actions secret."
        ) from exc
    return build("youtube", "v3", credentials=credentials)


def _format_market_block(market_snapshot: dict | None) -> str:
    """설명란에 넣을 미국 시장 데이터 블록을 만든다."""
    if not market_snapshot:
        return ""

    lines = []
    for key, label, unit in MARKET_LABELS:
        metric = market_snapshot.get(key)
        if not isinstance(metric, dict):
            continue
        close = metric.get("close")
        if close is None:
            continue
        change = metric.get("change_pct")
        change_txt = ""
        if isinstance(change, (int, float)):
            sign = "+" if change > 0 else ""
            change_txt = f" ({sign}{change}%)"
        lines.append(f"· {label} {close:,}{unit}{change_txt}")

    if not lines:
        return ""
    return "📊 오늘의 미국 시장\n" + "\n".join(lines)


def _format_story_block(storyboard: list | None) -> str:
    """설명란에 넣을 줄거리 블록을 만든다."""
    if not storyboard:
        return ""
    lines = [
        str(beat.get("narration", "")).strip()
        for beat in storyboard
        if str(beat.get("narration", "")).strip()
    ]
    if not lines:
        return ""
    return "📖 이번 화 줄거리\n" + "\n".join(lines)


def _rank_market_keys(market_snapshot: dict | None) -> list[str]:
    """등락 폭이 큰 지표부터 반환해 회차마다 메타데이터의 초점을 바꾼다."""
    if not market_snapshot:
        return []
    ranked = []
    for order, (key, _label, _unit) in enumerate(MARKET_LABELS):
        metric = market_snapshot.get(key)
        change = metric.get("change_pct") if isinstance(metric, dict) else None
        if isinstance(change, (int, float)):
            ranked.append((abs(change), -order, key))
    return [key for _change, _order, key in sorted(ranked, reverse=True)]


def _build_hashtags(
    villain: str | None, market_snapshot: dict | None = None, theme: str | None = None,
) -> list[str]:
    """브랜드 태그와 당일 핵심 검색어만 조합한다(설명란 해시태그 도배 방지)."""
    tags = list(BASE_HASHTAGS)
    tags += VILLAIN_HASHTAGS.get(villain or "", [])
    for key in _rank_market_keys(market_snapshot)[:3]:
        tags += MARKET_HASHTAGS.get(key, [])
    if theme:
        normalized = "".join(ch for ch in str(theme) if ch.isalnum())
        if 2 <= len(normalized) <= 18:
            tags.append(f"#{normalized}")
    # 중복 제거하되 순서는 유지한다
    seen = set()
    unique = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            unique.append(tag)
    return unique[:MAX_DESCRIPTION_HASHTAGS]


def _primary_signal(metadata: dict) -> str:
    """제목과 소셜 본문에서 쓸 가장 큰 당일 시장 변화를 한 줄로 만든다."""
    snapshot = metadata.get("market_snapshot") or {}
    ranked = _rank_market_keys(snapshot)
    if not ranked:
        return str(metadata.get("theme") or "오늘의 미국 증시")
    key = ranked[0]
    metric = snapshot[key]
    label = next(label for item, label, _unit in MARKET_LABELS if item == key)
    change = metric.get("change_pct")
    sign = "+" if change > 0 else ""
    return f"{label} {sign}{change:.2f}%"


def build_title(metadata: dict) -> str:
    """회차의 실제 핵심 지표를 앞에 둔 검색/클릭 친화적 YouTube 제목."""
    signal = _primary_signal(metadata)
    theme = str(metadata.get("theme") or "시장 브리핑").strip()
    episode = metadata.get("episode", "-")
    return f"{signal}, {theme} | EDT 투자코믹 Ep.{episode} #Shorts"[:100]


def build_social_post(metadata: dict, max_length: int = 280) -> str:
    """X 등 짧은 본문용 카피. 줄거리 복사 대신 훅·근거·CTA를 압축한다."""
    storyboard = metadata.get("storyboard") or []
    hook = next(
        (str(beat.get("narration", "")).strip() for beat in storyboard
         if str(beat.get("narration", "")).strip()),
        str(metadata.get("theme") or "오늘 시장의 흐름을 확인하세요."),
    )
    tags = _build_hashtags(
        metadata.get("villain"), metadata.get("market_snapshot"), metadata.get("theme")
    )
    # X에서는 발견성보다 가독성을 우선해 핵심 태그 3개만 쓴다.
    suffix = " ".join(tags[:3])
    body = f"{hook}\n\n핵심 신호: {_primary_signal(metadata)}\n30초 투자 코믹으로 확인하세요.\n\n{suffix}"
    if len(body) <= max_length:
        return body
    budget = max(1, max_length - len(body) + len(hook) - 1)
    return body.replace(hook, hook[:budget].rstrip() + "…", 1)[:max_length]


def build_description(metadata: dict) -> str:
    """첫 화면 요약, 근거 데이터, 교훈 순으로 읽히는 설명을 만든다."""
    episode = metadata.get("episode")
    theme = metadata.get("theme")
    villain = metadata.get("villain")

    storyboard = metadata.get("storyboard") or []
    narrations = [
        str(beat.get("narration", "")).strip() for beat in storyboard
        if str(beat.get("narration", "")).strip()
    ]
    hook = narrations[0] if narrations else f"오늘의 주제: {theme}"
    takeaway = narrations[-1] if len(narrations) > 1 else "시장보다 먼저 원칙을 점검하세요."
    blocks = [
        f"{hook}\n\n오늘의 핵심 신호는 {_primary_signal(metadata)}입니다. "
        "EDT와 함께 30초 안에 시장의 위험과 대응 원칙을 확인하세요.",
    ]

    market = _format_market_block(metadata.get("market_snapshot"))
    if market:
        blocks.append(market)

    blocks.append(f"🎯 오늘의 한 줄\n{takeaway}")

    blocks.append(
        f"🐯 EDT Universe Ep.{episode} · {theme}\n"
        "본 콘텐츠는 정보 제공 및 교육 목적이며, 특정 종목의 매수·매도를 권유하지 않습니다."
    )

    blocks.append(" ".join(_build_hashtags(villain, metadata.get("market_snapshot"), theme)))

    description = "\n\n".join(blocks)
    # YouTube 설명란 상한은 5000자다
    return description[:4900]


def _tag_list(
    villain: str | None, market_snapshot: dict | None = None, theme: str | None = None,
) -> list[str]:
    """YouTube tags 필드용(해시 기호 없이). 총 500자 제한이 있다."""
    tags = [tag.removeprefix("#") for tag in _build_hashtags(villain, market_snapshot, theme)]
    if villain:
        tags.append(villain)
    return tags



def _prepare_thumbnail(source: str, work_dir: str = "artifacts") -> str | None:
    """썸네일 규격에 맞게 이미지를 준비한다.

    YouTube 요구: 최소 1280x720, 2MB 이하, JPG/PNG/GIF.
    생성 이미지는 768x1376 이라 가로가 미달이므로 업스케일한다.
    """
    if not os.path.exists(source):
        logger.warning("thumbnail_source_missing path=%s", source)
        return None

    target = os.path.join(work_dir, "thumbnail.jpg")
    os.makedirs(work_dir, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", source,
        "-vf", "scale=1280:-2:flags=lanczos",
        "-q:v", "3",
        target,
        "-loglevel", "error",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not os.path.exists(target):
        logger.warning("thumbnail_prepare_failed stderr=%s", result.stderr[-300:])
        return None

    size = os.path.getsize(target)
    if size > 2 * 1024 * 1024:
        logger.warning("thumbnail_too_large bytes=%s -- skipped", size)
        return None
    return target


def set_thumbnail(youtube, video_id: str, image_path: str) -> bool:
    """업로드한 영상에 커스텀 썸네일을 설정한다.

    실패해도 발행은 유지한다. 채널 미인증이거나 스코프가 부족하면 403 이 난다.
    """
    prepared = _prepare_thumbnail(image_path)
    if not prepared:
        return False
    try:
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(prepared, mimetype="image/jpeg"),
        ).execute()
    except HttpError as e:
        logger.warning(
            "thumbnail_set_failed video_id=%s status=%s reason=%s "
            "(채널 인증 또는 OAuth 스코프 확인 필요)",
            video_id, getattr(e, "status_code", "?"), e,
        )
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("thumbnail_set_failed video_id=%s reason=%s: %s", video_id, type(e).__name__, e)
        return False

    logger.info("thumbnail_set video_id=%s source=%s", video_id, image_path)
    return True


def _find_playlist(youtube, title: str) -> str | None:
    """내 채널에서 제목이 일치하는 재생목록 ID를 찾는다."""
    page_token = None
    while True:
        resp = youtube.playlists().list(
            part="snippet", mine=True, maxResults=50, pageToken=page_token
        ).execute()
        for item in resp.get("items", []):
            if item.get("snippet", {}).get("title") == title:
                return item.get("id")
        page_token = resp.get("nextPageToken")
        if not page_token:
            return None


def add_to_playlist(youtube, video_id: str, title: str = PLAYLIST_TITLE) -> str | None:
    """재생목록에 영상을 추가한다. 없으면 재생목록부터 만든다.

    실패해도 발행은 유지한다. playlistItems.insert 는 youtube.force-ssl(또는 youtube)
    스코프를 요구하므로, upload 스코프만 있는 토큰이면 403 이 난다.
    """
    try:
        playlist_id = _find_playlist(youtube, title)
        if not playlist_id:
            created = youtube.playlists().insert(
                part="snippet,status",
                body={
                    "snippet": {"title": title, "description": "EDT Universe 미국 시장 히어로 시리즈"},
                    "status": {"privacyStatus": "public"},
                },
            ).execute()
            playlist_id = created.get("id")
            logger.info("playlist_created title=%s id=%s", title, playlist_id)

        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
    except HttpError as e:
        logger.warning(
            "playlist_add_failed video_id=%s reason=%s "
            "(youtube.force-ssl 스코프로 refresh token 재발급이 필요할 수 있음)",
            video_id, e,
        )
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("playlist_add_failed video_id=%s reason=%s: %s", video_id, type(e).__name__, e)
        return None

    logger.info("playlist_added video_id=%s playlist=%s id=%s", video_id, title, playlist_id)
    return playlist_id


def upload_to_youtube(video_path, metadata):
    title = build_title(metadata)
    logger.info("youtube_upload_preparing title=%r", title)
    
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file does not exist: {video_path}")

    youtube = get_youtube_service()
    if not youtube:
        return None

    body = {
        "snippet": {
            "title": title,
            "description": build_description(metadata),
            "tags": _tag_list(
                metadata.get("villain"), metadata.get("market_snapshot"), metadata.get("theme")
            ),
            "categoryId": "27"
        },
        "status": {
            "privacyStatus": os.getenv("YOUTUBE_DEFAULT_PRIVACY", "private"),
            "selfDeclaredMadeForKids": False,
        }
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=1024*1024)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    
    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info("youtube_upload_progress percent=%s", int(status.progress() * 100))
    except HttpError:
        logger.exception("youtube_upload_failed")
        raise

    video_id = response.get("id")
    logger.info("youtube_upload_finished video_id=%s", video_id)

    if not video_id:
        return None

    # 썸네일: 영상 첫 장면(훅 클로즈업)을 사용한다. 실패해도 발행은 유지한다.
    thumbnail_source = metadata.get("thumbnail_source") or "artifacts/images/scene_0.png"
    set_thumbnail(youtube, video_id, thumbnail_source)

    # 재생목록: 없으면 생성 후 추가. 실패해도 발행은 유지한다.
    add_to_playlist(youtube, video_id)

    return video_id
