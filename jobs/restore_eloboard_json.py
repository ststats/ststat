"""staruniv에 남아 있던 EloBoard 경기 백업(JSON)을 elo_matches에 다시 넣는다.

2026-09-24 수집기 삭제 사고(약 9.4만 경기 삭제)를 EloBoard에 다시 요청하지 않고 복구하려고
만든 일회용 작업이다. 백업은 staruniv가 Supabase로 옮기기 직전(2026-09-22) 커밋의
data/eloboard.json이다. 넣기만 하고(upsert) 아무것도 지우지 않는다. 백업 이후 경기와
백업에도 빠져 있던 늦게 등록된 경기는 이어서 도는 일반 수집(sync_eloboard)이 채운다.

백업 형식: {"cats": [형식 코드...], "maps": {map_id: 이름}, "players": {elo_id: [이름, 종족]},
           "rows": [[경기id, 날짜, 승자id, 패자id, map_id, 형식 번호], ...]}
"""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

from models.eloboard import EloMatch, EloParticipant
from models.sync_job import JobResult
from repositories.eloboard import upsert_dimensions, upsert_matches

DEFAULT_URL = "https://raw.githubusercontent.com/ststats/staruniv/b451768/data/eloboard.json"
MIN_ROWS = 300_000


def fetch_backup(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "ststat-restore/1.0"})
    with urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def backup_matches(store: dict) -> tuple[list[EloMatch], int]:
    """백업 행을 EloMatch로 바꾼다. 반환: (경기 목록, 버린 행 수)"""
    cats = store.get("cats") or []
    maps = {str(k): str(v or "").strip() for k, v in (store.get("maps") or {}).items()}
    players = store.get("players") or {}
    out: list[EloMatch] = []
    skipped = 0

    def participant(pid, result: str) -> EloParticipant:
        info = players.get(str(pid)) or []
        name = str(info[0]).strip() if len(info) > 0 and info[0] else str(pid)
        race = str(info[1]).strip() if len(info) > 1 and info[1] else None
        return EloParticipant(elo_id=int(pid), name=name, race=race, result=result)

    for row in store.get("rows") or []:
        try:
            match_id, day, winner, loser, map_id, cat = row[:6]
            if winner is None or loser is None or not day:
                raise ValueError("missing field")
            category = cats[cat] if isinstance(cat, int) and 0 <= cat < len(cats) else ""
            map_key = None if map_id in (None, "") else str(map_id)
            out.append(EloMatch(
                elo_match_id=int(match_id),
                match_date=str(day)[:10],
                winner=participant(winner, "win"),
                loser=participant(loser, "loss"),
                map_id=int(map_key) if map_key and map_key.isdigit() else None,
                map_name=maps.get(map_key) if map_key else None,
                category=category,
            ))
        except (TypeError, ValueError, IndexError):
            skipped += 1
    return out, skipped


def run() -> JobResult:
    url = os.getenv("ELOBOARD_RESTORE_URL", DEFAULT_URL)
    store = fetch_backup(url)
    matches, skipped = backup_matches(store)
    if len(matches) < MIN_ROWS:
        raise RuntimeError(f"Backup has only {len(matches)} matches; refusing restore")
    dimensions = upsert_dimensions(matches)
    written = upsert_matches(matches)
    return JobResult(
        records_read=len(matches) + skipped,
        records_written=written,
        records_skipped=skipped,
        source_cursor=str(store.get("max_id") or ""),
        metadata={
            "source_url": url,
            "backup_synced_at": store.get("synced_at"),
            "backup_count": store.get("count"),
            "matches_upserted": written,
            "players_upserted": dimensions["players"],
            "maps_upserted": dimensions["maps"],
            "deleted": 0,
        },
    )
