"""Supabase client for the production pipeline.

service_role 키를 사용한다 (RLS 우회). anon 키를 쓰지 않는 이유:
투자-os 계열 레포에서 RLS 활성 + 정책 0건 테이블에 anon 키로 접근해
쓰기가 조용히 차단된 사고가 반복됐다 (2026-08-31 RLS 사일런트 실패).
service_role + RLS 유지 조합은 이 결함 클래스 자체를 원천 차단한다.
"""

from __future__ import annotations

import os

from supabase import Client, create_client

_client: Client | None = None


def get_client() -> Client:
    """싱글턴 Supabase 클라이언트를 반환한다."""
    global _client
    if _client is not None:
        return _client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 환경변수가 없다. "
            "GitHub Secrets 등록 여부를 확인하라."
        )

    _client = create_client(url, key)
    return _client


def reset_client_for_tests() -> None:
    """테스트 간 싱글턴 격리를 위한 헬퍼. 프로덕션 코드에서는 호출하지 않는다."""
    global _client
    _client = None
