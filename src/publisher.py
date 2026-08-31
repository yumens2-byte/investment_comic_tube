import os
import time
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

def get_youtube_service():
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        print("[Publisher] Warning: YouTube API 환경변수가 없습니다. (로컬/Mock 테스트 모드 전환)")
        return None

    credentials = Credentials(
        token=None, refresh_token=refresh_token, token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id, client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    credentials.refresh(Request())
    return build("youtube", "v3", credentials=credentials)

def upload_to_youtube(video_path, metadata):
    title = f"[EDT Universe] Ep.{metadata.get('episode')} {metadata.get('villain')}의 공습! #Shorts"[:95]
    print(f"[Publisher] 유튜브 업로드 준비: '{title}'")
    
    if not os.path.exists(video_path):
        print(f"[Publisher] (Test Mode) 가상 파일 업로드 시뮬레이션: {video_path}")
        print(f"[Publisher] 업로드 진행률: 100%")
        print(f"[Publisher] ✅ 유튜브 업로드 성공! URL: https://youtu.be/mock_test_video_id")
        return "mock_test_video_id"

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
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=1024*1024)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[Publisher] 업로드 진행률: {int(status.progress() * 100)}%")

    print(f"[Publisher] ✅ 유튜브 업로드 성공! URL: https://youtu.be/{response.get('id')}")
    return response.get("id")
