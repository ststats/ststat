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
    """빈 값은 0(방송 기록 없음). 숫자가 아니거나 음수면 응답이 깨진 것이라 멈춘다
    (예전엔 "INVALID"도 0으로 바꿔 정상 수치처럼 게시했다)."""
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

    # Poonggo는 정상 응답에서도 해당 월 방송 기록이 없는 ID를 배열에서 생략한다.
    # 누락을 수집 실패로 해석하면 월초나 비활성 선수가 많을 때 정상 실행이 중단되므로,
    # 요청이 성공한 ID는 먼저 0으로 채우고 실제 응답만 덮어쓴다.
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
            # 방송 기록이 없는 계정은 '생략'되는 것이 정상 계약이다. 모양이 틀린 행은 생략과 다르다 -
            # 무시하면 요청한 계정이 모두 0으로 채워진 채 검사를 통과하므로 여기서 멈춘다.
            if not isinstance(entry, dict):
                raise RuntimeError(f"Poonggo returned a malformed row for {date_str}: {str(entry)[:80]}")
            soop_id = str(entry.get("id") or "").strip()
            if not soop_id:
                raise RuntimeError(f"Poonggo returned a row without id for {date_str}")
            soop_id = canonical_ids.get(soop_id.lower())
            if soop_id is None:
                # 요청하지 않은 계정이 섞인 응답은 로스터 통계에 포함하지 않는다.
                continue
            result[soop_id] = MonthlyLiveStats(
                balloons=_to_int(entry.get("amt"), "amt", soop_id),
                broadcast_seconds=_to_int(entry.get("broadTime"), "broadTime", soop_id),
                cumulative_viewers=_to_int(entry.get("cview"), "cview", soop_id),
            )

    return result
