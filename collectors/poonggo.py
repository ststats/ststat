from __future__ import annotations

import json
import os
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

from models.synergy_stats import MonthlyLiveStats

POONGGO_MONTHLY_URL = "https://poonggo.com/api/monthly"
IDS_PER_REQUEST = int(os.getenv("POONGGO_IDS_PER_REQUEST", "300"))
SLEEP_BETWEEN_CHUNKS_SEC = float(os.getenv("POONGGO_CHUNK_DELAY", "0.5"))
TIMEOUT = int(os.getenv("POONGGO_TIMEOUT", "30"))
RETRIES = int(os.getenv("POONGGO_RETRIES", "3"))


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _to_int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _fetch_json(url: str):
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            req = Request(url, headers={"User-Agent": "ststat/1.0"})
            with urlopen(req, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # external API boundary
            last_error = exc
            if attempt + 1 < RETRIES:
                time.sleep(min(2 ** attempt, 5))
    raise RuntimeError(f"Poonggo request failed after {RETRIES} attempts: {last_error}")


def _extract_entries(parsed) -> list[dict] | None:
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("data", "list", "result", "items"):
            value = parsed.get(key)
            if isinstance(value, list):
                return value
    return None


def fetch_monthly(year: int, month: int, soop_ids: list[str]) -> dict[str, MonthlyLiveStats]:
    if not soop_ids:
        return {}

    result: dict[str, MonthlyLiveStats] = {}
    chunks = list(_chunks([str(x) for x in soop_ids], IDS_PER_REQUEST))
    date_str = f"{year:04d}-{month:02d}-01"

    for idx, chunk in enumerate(chunks):
        if idx:
            time.sleep(SLEEP_BETWEEN_CHUNKS_SEC)
        ids_param = quote(",".join(chunk), safe=",")
        parsed = _fetch_json(f"{POONGGO_MONTHLY_URL}?date={date_str}&ids={ids_param}")
        entries = _extract_entries(parsed)
        if entries is None:
            raise RuntimeError(f"Unexpected Poonggo response shape for {date_str}")

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            soop_id = str(entry.get("id") or "").strip()
            if not soop_id:
                continue
            result[soop_id] = MonthlyLiveStats(
                balloons=_to_int(entry.get("amt")),
                broadcast_seconds=_to_int(entry.get("broadTime")),
                cumulative_viewers=_to_int(entry.get("cview")),
            )

    return result
