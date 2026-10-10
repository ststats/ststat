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


def _to_int(value, field: str, soop_id: str) -> int:
    """빈 값은 0(방송 기록 없음). 숫자가 아니거나 음수면 깨진 응답이라 멈춘다."""
    if value is None or value == "":
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise RuntimeError(f"Poonggo {field} is not a number for {soop_id}: {value!r}")
    if number != number or number < 0 or number == float("inf"):
        raise RuntimeError(f"Poonggo {field} is invalid for {soop_id}: {value!r}")
    return int(number)


def _fetch_json(url: str):
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            req = Request(url, headers={"User-Agent": "ststat/1.0"})
            with urlopen(req, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
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

    # 그 달 방송 기록이 없는 ID는 응답에서 빠지는 게 정상이라, 요청한 ID를 먼저 0으로 채운다.
    canonical_ids = {str(value).lower(): str(value) for value in soop_ids}
    result: dict[str, MonthlyLiveStats] = {
        value: MonthlyLiveStats() for value in canonical_ids.values()
    }
    chunks = list(_chunks(list(canonical_ids.values()), IDS_PER_REQUEST))
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
            # 모양이 틀린 행을 무시하면 0으로 채운 값이 검사를 통과하므로 멈춘다
            if not isinstance(entry, dict):
                raise RuntimeError(f"Poonggo returned a malformed row for {date_str}: {str(entry)[:80]}")
            soop_id = str(entry.get("id") or "").strip()
            if not soop_id:
                raise RuntimeError(f"Poonggo returned a row without id for {date_str}")
            soop_id = canonical_ids.get(soop_id.lower())
            if soop_id is None:
                continue
            result[soop_id] = MonthlyLiveStats(
                balloons=_to_int(entry.get("amt"), "amt", soop_id),
                broadcast_seconds=_to_int(entry.get("broadTime"), "broadTime", soop_id),
                cumulative_viewers=_to_int(entry.get("cview"), "cview", soop_id),
            )

    return result
