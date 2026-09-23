import os
from functools import lru_cache
from supabase import Client, create_client


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url:
        raise RuntimeError("SUPABASE_URL is not configured")
    if not service_role_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not configured")

    return create_client(url, service_role_key)
