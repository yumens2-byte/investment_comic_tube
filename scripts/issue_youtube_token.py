"""
scripts/issue_youtube_token.py
YouTube OAuth Refresh Token (재)발급 스크립트 — 로컬 1회 실행 전용 (운영자 PC).

목적:
  GitHub Actions 비대화식 업로드(src/publisher.py)에 필요한 YOUTUBE_REFRESH_TOKEN 을
  로컬 브라우저 승인 1회로 (재)발급한다.
  invalid_grant(만료/폐기) 대응과 최초 발급 모두 이 스크립트로 처리한다.

사전 준비 (GCP Console):
  1. YouTube Data API v3 활성화
  2. OAuth 동의 화면 구성 — 게시 상태를 "프로덕션"으로 전환 권장
     (테스트 상태 유지 시 발급된 refresh_token이 7일 후 자동 만료되어 동일 장애 재발)
  3. OAuth 클라이언트 ID 생성 — 유형: "데스크톱 앱" -> CLIENT_ID / CLIENT_SECRET 확보

사용법 (로컬 PC):
  pip install google-auth-oauthlib google-api-python-client
  python scripts/issue_youtube_token.py --client-id <ID> --client-secret <SECRET>
  (또는 env YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET 설정 후 인자 생략)

  -> 브라우저가 열리고 구글 계정 승인 화면이 매번 강제로 노출된다
     (재발급 시에도 refresh_token 이 보장되도록 access_type=offline + prompt=consent 사용)
  -> 발급 즉시 channels.list 로 실제 채널을 조회하여 토큰 동작을 검증한다
  -> 콘솔에 출력된 REFRESH_TOKEN 값을 GitHub Secrets(YOUTUBE_REFRESH_TOKEN)에 등록한다

주의:
  - scope 는 업로드 최소 권한만 요청한다.
  - 발급된 토큰은 절대 코드/로그 파일/커밋에 남기지 않는다 (콘솔 1회 출력만).
  - 기존에 이 앱을 승인한 이력이 있는데 refresh_token 이 비어서 돌아온다면,
    https://myaccount.google.com/permissions 에서 기존 앱 액세스를 철회한 뒤 재시도한다.
"""

from __future__ import annotations

import argparse
import os
import sys

VERSION = "1.0.0"

# 업로드 외에 재생목록 추가/썸네일 설정까지 하려면 스코프를 넓혀야 한다.
# playlistItems.insert 는 youtube.upload 로는 403 이 난다.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def build_client_config(client_id: str, client_secret: str) -> dict:
    """설치형(Desktop) OAuth 클라이언트 설정 딕셔너리를 구성한다."""
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def verify_channel(creds) -> None:
    """발급된 자격증명으로 channels.list 를 호출해 즉시 동작을 검증한다."""
    from googleapiclient.discovery import build

    try:
        youtube = build("youtube", "v3", credentials=creds)
        resp = youtube.channels().list(part="snippet,status", mine=True).execute()
    except Exception as e:  # noqa: BLE001 - 발급 후 검증 실패는 경고로만 처리
        print(f"WARN: 발급 후 검증 호출 실패 (토큰 자체는 발급됨): {type(e).__name__}: {e}", file=sys.stderr)
        return

    items = resp.get("items", [])
    if not items:
        print("WARN: 채널 조회 0건 — 토큰은 발급되었으나 계정에 채널이 없을 수 있음", file=sys.stderr)
        return

    snippet = items[0].get("snippet", {})
    status = items[0].get("status", {})
    print(f"OK: 채널 확인 — title='{snippet.get('title', '?')}' privacyStatus={status.get('privacyStatus', '?')}")


def main() -> int:
    print(f"[issue_youtube_token] v{VERSION} 시작")

    parser = argparse.ArgumentParser(description="YouTube OAuth refresh token (재)발급 (로컬 1회)")
    parser.add_argument("--client-id", default=os.environ.get("YOUTUBE_CLIENT_ID", ""))
    parser.add_argument("--client-secret", default=os.environ.get("YOUTUBE_CLIENT_SECRET", ""))
    parser.add_argument("--port", type=int, default=8765, help="로컬 콜백 서버 포트 (기본 8765)")
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        print(
            "ERROR: client-id / client-secret 누락. "
            "--client-id/--client-secret 인자 또는 "
            "YOUTUBE_CLIENT_ID/YOUTUBE_CLIENT_SECRET env 를 설정하라.",
            file=sys.stderr,
        )
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "ERROR: google-auth-oauthlib 미설치. "
            "'pip install google-auth-oauthlib' 실행 후 재시도하라.",
            file=sys.stderr,
        )
        return 1

    client_config = build_client_config(args.client_id, args.client_secret)
    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)

    try:
        creds = flow.run_local_server(
            port=args.port,
            access_type="offline",
            prompt="consent",
        )
    except Exception as e:  # noqa: BLE001 - 브라우저/네트워크 오류 원인 그대로 노출 필요
        print(f"ERROR: 브라우저 승인 흐름 실패: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if not creds.refresh_token:
        print(
            "ERROR: refresh_token 이 발급되지 않았다. "
            "https://myaccount.google.com/permissions 에서 기존 앱 액세스를 철회한 뒤 재시도하라.",
            file=sys.stderr,
        )
        return 1

    print("OK: 브라우저 승인 완료, refresh_token 발급됨")
    verify_channel(creds)

    print("\n" + "=" * 60)
    print("아래 값을 GitHub Secrets 'YOUTUBE_REFRESH_TOKEN' 에 그대로 등록하라:")
    print("=" * 60)
    print(creds.refresh_token)
    print("=" * 60)
    print("[issue_youtube_token] 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
