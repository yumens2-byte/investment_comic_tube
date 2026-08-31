import logging
import os
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError


logger = logging.getLogger(__name__)


class YouTubeAuthenticationError(RuntimeError):
    """Raised when YouTube OAuth credentials need operator action."""


def get_youtube_service():
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        logger.warning("youtube_credentials_missing upload_skipped=true")
        return None

    credentials = Credentials(
        token=None, refresh_token=refresh_token, token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id, client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    try:
        credentials.refresh(Request())
    except RefreshError as exc:
        logger.error(
            "youtube_oauth_refresh_failed action=replace_YOUTUBE_REFRESH_TOKEN reason=invalid_grant"
        )
        raise YouTubeAuthenticationError(
            "YouTube refresh token is expired or revoked. Re-authorize the channel and "
            "replace the YOUTUBE_REFRESH_TOKEN GitHub Actions secret."
        ) from exc
    return build("youtube", "v3", credentials=credentials)

def upload_to_youtube(video_path, metadata):
    title = f"[EDT Universe] Ep.{metadata.get('episode')} {metadata.get('villain')}의 공습! #Shorts"[:95]
    logger.info("youtube_upload_preparing title=%r", title)
    
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file does not exist: {video_path}")

    youtube = get_youtube_service()
    if not youtube:
        return None

    body = {
        "snippet": {
            "title": title,
            "description": f"EDT Universe Episode {metadata.get('episode')}\nTheme: {metadata.get('theme')}\n#EDT #Shorts",
            "tags": ["EDT", "Shorts", "미국증시", metadata.get("villain")],
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

    logger.info("youtube_upload_finished video_id=%s", response.get("id"))
    return response.get("id")
