import os
from functools import lru_cache
from supabase import Client, create_client


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    return new_supabase()


def new_supabase() -> Client:
    """캐시하지 않은 새 연결. 여러 스레드가 동시에 요청할 때 스레드마다 하나씩 쓴다."""
    url = os.environ.get("SUPABASE_URL")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url:
        raise RuntimeError("SUPABASE_URL is not configured")
    if not service_role_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not configured")

    return create_client(url, service_role_key)
