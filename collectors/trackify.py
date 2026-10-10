from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from models.synergy_stats import MonthlyLiveStats

# 트래키파이(trackify.kr): 약관 4조가 무단 수집을 막아 운영자 허락을 받고 쓴다(2026-10, 조건 없음).
TRACKIFY_API = "https://www.trackify.kr/api/v1/p/soop"
# 서버가 ids를 100개까지만 보고 나머지는 말없이 뺀다(2026-10 확인)
IDS_PER_REQUEST = min(int(os.getenv("TRACKIFY_IDS_PER_REQUEST", "100")), 100)
SLEEP_BETWEEN_REQUESTS_SEC = float(os.getenv("TRACKIFY_REQUEST_DELAY", "1.0"))
TIMEOUT = int(os.getenv("TRACKIFY_TIMEOUT", "30"))
RETRIES = int(os.getenv("TRACKIFY_RETRIES", "3"))
# 분당 약 45건에서 429가 온다(2026-10 확인). 429는 Retry-After만큼, 없으면 1분·2분·… 기다린다.
RATE_LIMIT_RETRIES = int(os.getenv("TRACKIFY_RATE_LIMIT_RETRIES", "6"))
RATE_LIMIT_WAIT_SEC = float(os.getenv("TRACKIFY_RATE_LIMIT_WAIT", "60"))
USER_AGENT = "ststat/1.0 (+https://ststats.github.io/synergy)"


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _to_int(value, field: str, soop_id: str) -> int:
    """빈 값은 0. 숫자가 아니거나 음수면 깨진 응답이라 멈춘다."""
    if value is None or value == "":
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise RuntimeError(f"Trackify {field} is not a number for {soop_id}: {value!r}")
    if number != number or number < 0 or number == float("inf"):
        raise RuntimeError(f"Trackify {field} is invalid for {soop_id}: {value!r}")
    return int(round(number))


def _retry_after(exc: HTTPError, waited: int) -> float:
    try:
        return max(float(exc.headers.get("Retry-After") or ""), 1.0)
    except (TypeError, ValueError):
        return RATE_LIMIT_WAIT_SEC * (waited + 1)


def _fetch_json(url: str):
    last_error: Exception | None = None
    attempt = rate_limited = 0
    while attempt < RETRIES:
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(req, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = exc
            if exc.code == 429 and rate_limited < RATE_LIMIT_RETRIES:
                time.sleep(_retry_after(exc, rate_limited))
                rate_limited += 1
                continue
        except Exception as exc:
            last_error = exc
        attempt += 1
        if attempt < RETRIES:
            time.sleep(min(2 ** (attempt - 1), 5))
    raise RuntimeError(f"Trackify request failed after {RETRIES} attempts: {last_error}")


def _fetch_summary(range_: str, period: str, soop_ids: list[str]) -> dict[str, MonthlyLiveStats]:
    """요약 한 기간(monthly: YYYY-MM, daily: YYYY-MM-DD). 방송 기록이 없는 아이디는 빠지는 게 정상이라 0으로 채운다."""
    if not soop_ids:
        return {}

    canonical_ids = {str(value).lower(): str(value) for value in soop_ids}
    result: dict[str, MonthlyLiveStats] = {
        value: MonthlyLiveStats(viewership_seconds=0) for value in canonical_ids.values()
    }

    for idx, chunk in enumerate(_chunks(list(canonical_ids.values()), IDS_PER_REQUEST)):
        if idx:
            time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)
        parsed = _fetch_json(
            f"{TRACKIFY_API}/ranking/summary?sortKey=balloon&order=desc&range={range_}&date={period}"
            f"&page=1&size={len(chunk)}&ids={quote(','.join(chunk), safe=',')}"
        )
        items = parsed.get("items") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            raise RuntimeError(f"Unexpected Trackify response shape for {period}")
        if parsed.get("more"):
            # 나머지를 0으로 채우면 틀린 값이 되므로 멈춘다
            raise RuntimeError(f"Trackify returned more than one page for {period}")

        for entry in items:
            if not isinstance(entry, dict):
                raise RuntimeError(f"Trackify returned a malformed row for {period}: {str(entry)[:80]}")
            soop_id = canonical_ids.get(str(entry.get("broadUserId") or "").strip().lower())
            if soop_id is None:
                continue
            result[soop_id] = MonthlyLiveStats(
                balloons=_to_int(entry.get("balloon"), "balloon", soop_id),
                broadcast_seconds=_to_int(entry.get("broadTimeSec"), "broadTimeSec", soop_id),
                cumulative_viewers=_to_int(entry.get("uniqueViewers"), "uniqueViewers", soop_id),
                viewership_seconds=_to_int(entry.get("viewership"), "viewership", soop_id),
            )

    return result


def fetch_monthly(year: int, month: int, soop_ids: list[str]) -> dict[str, MonthlyLiveStats]:
    """그 달 누적(collectors/poonggo.fetch_monthly와 같은 계약)."""
    return _fetch_summary("monthly", f"{year:04d}-{month:02d}", soop_ids)
