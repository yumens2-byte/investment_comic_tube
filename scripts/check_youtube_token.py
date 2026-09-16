"""YouTube refresh token 유효성 사전 점검.

발행 파이프라인이 실패하기 전에 토큰 만료를 미리 감지하기 위한 독립 스크립트.
repo 내 다른 모듈을 import 하지 않는다(파이프라인 장애와 격리).

판정 기준
    credentials.refresh() 성공 여부 단일 기준.
    YouTube API 실호출은 하지 않는다. youtube.upload 는 write-only 스코프라
    channels.list 등 조회 API 호출 시 403 이 발생해 정상 토큰을 오탐하게 된다.

종료 코드
    0 : 정상
    1 : 토큰 만료/폐기 (invalid_grant) — 재발급 필요
    2 : 환경변수 누락 등 설정 오류
    3 : 네트워크/일시 오류 — 재시도 대상
"""

from __future__ import annotations

import os
import sys

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

VERSION = "1.0.0"

TOKEN_URI = "https://oauth2.googleapis.com/token"

# ⚠️ 확인 필요: src/publisher.py 의 os.environ 조회 키와 일치해야 함.
#    REFRESH_TOKEN 키는 파이프라인 에러 메시지로 확인됨.
#    CLIENT_ID / CLIENT_SECRET 키는 미확인 상태이므로 대조 후 수정할 것.
ENV_CLIENT_ID = "YOUTUBE_CLIENT_ID"
ENV_CLIENT_SECRET = "YOUTUBE_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "YOUTUBE_REFRESH_TOKEN"

EXIT_OK = 0
EXIT_TOKEN_DEAD = 1
EXIT_CONFIG_ERROR = 2
EXIT_TRANSIENT = 3


def _read_env() -> tuple[str, str, str] | None:
    missing = [
        key
        for key in (ENV_CLIENT_ID, ENV_CLIENT_SECRET, ENV_REFRESH_TOKEN)
        if not os.environ.get(key)
    ]
    if missing:
        print(f"[config_error] 환경변수 누락: {', '.join(missing)}")
        return None
    return (
        os.environ[ENV_CLIENT_ID],
        os.environ[ENV_CLIENT_SECRET],
        os.environ[ENV_REFRESH_TOKEN],
    )


def main() -> int:
    print(f"[check_youtube_token] v{VERSION} 시작")

    env = _read_env()
    if env is None:
        return EXIT_CONFIG_ERROR

    client_id, client_secret, refresh_token = env

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
    )

    try:
        credentials.refresh(Request())
    except RefreshError as exc:
        # invalid_grant 는 토큰이 만료/폐기된 확정 상태. 재시도 무의미.
        print(f"[token_dead] refresh 실패: {exc}")
        print("[action] 로컬에서 재인증 후 YOUTUBE_REFRESH_TOKEN Secret 교체 필요")
        return EXIT_TOKEN_DEAD
    except Exception as exc:  # noqa: BLE001 - 네트워크 등 일시 오류 전부 재시도 대상
        print(f"[transient_error] {type(exc).__name__}: {exc}")
        return EXIT_TRANSIENT

    if not credentials.token:
        print("[token_dead] refresh 는 성공했으나 access token 이 비어 있음")
        return EXIT_TOKEN_DEAD

    expiry = credentials.expiry.isoformat() if credentials.expiry else "unknown"
    print(f"[ok] access token 발급 성공 expiry={expiry}")
    print(f"[ok] granted_scopes={credentials.scopes or 'not_returned'}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
