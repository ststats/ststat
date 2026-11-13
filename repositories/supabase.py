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


PAGE_SIZE = 1000


def fetch_all(make_query, page_size: int = PAGE_SIZE) -> list[dict]:
    """PostgREST는 한 번에 최대 1000행만 주므로 쪽씩 끝까지 읽는다.
    조회에 order를 꼭 붙인다 - 없으면 쪽 경계에서 행이 빠지거나 겹친다."""
    rows: list[dict] = []
    while True:
        batch = make_query().range(len(rows), len(rows) + page_size - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
