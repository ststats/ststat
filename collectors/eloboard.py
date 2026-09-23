from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://eloboard.co.kr/api/matches"
PAGE_LIMIT = 200
PAGE_STEP = PAGE_LIMIT - 10
MAX_RETRY = 4
DEFAULT_DELAY = float(os.getenv("ELOBOARD_DELAY", "2.0"))
USER_AGENT = os.getenv("ELOBOARD_UA", "ststat/1.0 (+https://github.com/ststats/ststat)").encode("ascii", "ignore").decode("ascii")


def fetch_page(offset: int, *, delay: float = DEFAULT_DELAY) -> list[dict]:
    url = f"{API_URL}?limit={PAGE_LIMIT}&offset={offset}"
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_error: Exception | None = None
    for attempt in range(MAX_RETRY):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise ValueError(f"Expected list, got {type(payload).__name__}")
            return payload
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"EloBoard HTTP {exc.code} at offset={offset}") from exc
            last_error = exc
        except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
        if attempt < MAX_RETRY - 1:
            time.sleep(max(delay, 1.0) * (2 ** attempt))
    raise RuntimeError(f"EloBoard fetch failed after {MAX_RETRY} attempts at offset={offset}: {last_error}")
