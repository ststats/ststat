from __future__ import annotations

from datetime import datetime, timezone

from collections import Counter, defaultdict

from models.eloboard import EloMatch
from repositories.roster import load_linked_elo_ids
from repositories.supabase import fetch_all, get_supabase


def get_latest_match_id() -> int:
    db = get_supabase()
    rows = db.table("elo_matches").select("elo_match_id").order("elo_match_id", desc=True).limit(1).execute().data or []
    return int(rows[0]["elo_match_id"]) if rows else 0


def load_categories() -> dict[str, int]:
    db = get_supabase()
    rows = db.table("elo_categories").select("category_id,name").order("category_id").execute().data or []
    return {str(r.get("name") or ""): int(r["category_id"]) for r in rows}


def ensure_categories(names: set[str], current: dict[str, int] | None = None) -> dict[str, int]:
    db = get_supabase()
    current = dict(current) if current is not None else load_categories()
    next_id = max(current.values(), default=-1) + 1
    new_rows = []
    for name in sorted(names):
        if name in current:
            continue
        current[name] = next_id
        new_rows.append({"category_id": next_id, "name": name})
        next_id += 1
    if new_rows:
        db.table("elo_categories").upsert(new_rows, on_conflict="category_id", returning="minimal").execute()
    return current


# 한 번 실행(sync_eloboard) 안에서 형식 목록을 여러 번 읽지 않게 upsert_dimensions가 채워 두고 upsert_matches가 쓴다
_categories: dict[str, int] | None = None
MATCH_CHUNK = 2000


def _upsert_batch(players=(), maps=(), matches=()) -> dict:
    """바뀐 행만 쓰는 DB 함수(upsert_elo_batch, ststat.sql 3절). 바뀐 수 {"players","maps","matches"}를 돌려준다."""
    return get_supabase().rpc("upsert_elo_batch", {
        "p_players": list(players), "p_maps": list(maps), "p_matches": list(matches),
    }).execute().data or {}


def upsert_dimensions(matches: list[EloMatch]) -> dict[str, int]:
    global _categories
    if not matches:
        return {"players": 0, "maps": 0, "categories": 0}
    race_counts: dict[int, Counter] = defaultdict(Counter)
    names: dict[int, str] = {}
    maps: dict[int, str] = {}
    categories = set()
    for match in matches:
        categories.add(match.category)
        if match.map_id is not None and match.map_name:
            maps[match.map_id] = match.map_name
        for p in (match.winner, match.loser):
            names[p.elo_id] = p.name
            if p.race:
                race_counts[p.elo_id][p.race] += 1

    players = []
    for elo_id, name in names.items():
        race = race_counts[elo_id].most_common(1)[0][0] if race_counts[elo_id] else None
        players.append({"elo_id": elo_id, "name": name, "race": race})
    map_rows = [{"map_id": k, "name": v} for k, v in maps.items()]
    changed = _upsert_batch(players, map_rows)
    before = load_categories()
    _categories = ensure_categories(categories, before)
    return {"players": int(changed.get("players") or 0), "maps": int(changed.get("maps") or 0),
            "categories": max(0, len(categories - set(before)))}


def upsert_matches(matches: list[EloMatch]) -> int:
    """경기를 저장하고 실제로 바뀐(새로 넣거나 고친) 행 수를 돌려준다."""
    if not matches:
        return 0
    names = {m.category for m in matches}
    categories = _categories if _categories is not None and names <= set(_categories) else ensure_categories(names, _categories)
    payload = [
        {
            "elo_match_id": m.elo_match_id,
            "match_date": m.match_date,
            "winner_elo_id": m.winner.elo_id,
            "loser_elo_id": m.loser.elo_id,
            "map_id": m.map_id,
            "category_id": categories[m.category],
        }
        for m in matches
    ]
    return sum(int(_upsert_batch(matches=payload[start:start + MATCH_CHUNK]).get("matches") or 0)
               for start in range(0, len(payload), MATCH_CHUNK))


def load_match_ids_between(start_date: str, end_date: str) -> set[int]:
    """경기 날짜가 [start_date, end_date]인 저장된 경기 ID.
    '사라진 경기'는 훑은 날짜 범위로만 판정한다 - ID는 날짜와 따로 놀아 ID 범위로 고르면 훑지 않은 경기까지 지운다."""
    db = get_supabase()
    rows = fetch_all(lambda: db.table("elo_matches").select("elo_match_id")
                     .gte("match_date", start_date).lte("match_date", end_date).order("elo_match_id"))
    return {int(r["elo_match_id"]) for r in rows}


def load_match_rows(ids: set[int]) -> list[dict]:
    """지우기 직전 원본 행(되살리기용 기록)."""
    if not ids:
        return []
    db = get_supabase()
    values = sorted(ids)
    out: list[dict] = []
    for start in range(0, len(values), 200):
        out.extend(
            db.table("elo_matches")
            .select("elo_match_id,match_date,winner_elo_id,loser_elo_id,map_id,category_id")
            .in_("elo_match_id", values[start:start + 200])
            .execute()
            .data
            or []
        )
    return out


def save_deletion_backup(rows: list[dict], run_id: str | None = None) -> None:
    """지우기 전에 원본 행을 sync_jobs에 한 행으로 남긴다(metadata.rows를 upsert하면 되살린다).
    저장에 실패하면 예외가 올라가 삭제하지 않는다."""
    if not rows:
        return
    now = datetime.now(timezone.utc).isoformat()
    get_supabase().table("sync_jobs").insert({
        "job_name": "sync_eloboard_deleted_backup",
        "run_id": run_id,
        "status": "success",
        "finished_at": now,
        "records_read": len(rows),
        "metadata": {"rows": rows},
    }).execute()


def delete_match_ids(ids: set[int]) -> int:
    if not ids:
        return 0
    db = get_supabase()
    # PostgREST in_은 URL 길이 때문에 적당히 나눠 보낸다
    values = sorted(ids)
    for start in range(0, len(values), 200):
        db.table("elo_matches").delete().in_("elo_match_id", values[start:start + 200]).execute()
    return len(values)


def stage_unknown_elo_candidates(matches: list[EloMatch]) -> int:
    """경기에만 나온 모르는 선수를 대기 명단에 'elo:<id>'로 올린다(경기 API엔 SOOP ID가 없다).
    tier_members에는 넣지 않는다."""
    if not matches:
        return 0
    db = get_supabase()

    def paged_ids(table: str) -> list[dict]:
        return fetch_all(lambda: db.table(table).select("id,elo_id").order("id"))

    roster_rows = paged_ids("tier_members")
    known = {int(r["elo_id"]) for r in roster_rows if r.get("elo_id") is not None}
    known |= load_linked_elo_ids()   # 선수에 연결된 다른 계정(종족 변경 등)
    pending_rows = paged_ids("tier_member_candidates")
    pending = {int(r["elo_id"]) for r in pending_rows if r.get("elo_id") is not None}

    players = {}
    for m in matches:
        for p in (m.winner, m.loser):
            players[p.elo_id] = p
    unknown = [p for elo_id, p in players.items() if elo_id not in known and elo_id not in pending]
    if not unknown:
        return 0
    payload = [
        {
            "id": f"elo:{p.elo_id}",
            "nickname": p.name,
            "elo_id": p.elo_id,
            "race": p.race,
            "source": "ststat_sync_eloboard",
            "status": "pending",
        }
        for p in unknown
    ]
    db.table("tier_member_candidates").upsert(payload, on_conflict="id").execute()
    return len(payload)


def load_previous_pending_deletes() -> set[int]:
    """지난번 성공한 sync_eloboard가 '안 보였다'고 적어 둔 경기 ID.
    목록이 offset으로 넘어가 수집 중 끼어든 경기 때문에 한 번 안 보일 수 있어, 두 번 연속 안 보인 것만 지운다."""
    db = get_supabase()
    rows = (
        db.table("sync_jobs")
        .select("metadata")
        .eq("job_name", "sync_eloboard")
        .eq("status", "success")
        .order("started_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return set()
    pending = (rows[0].get("metadata") or {}).get("pending_delete") or []
    out = set()
    for value in pending:
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return out
