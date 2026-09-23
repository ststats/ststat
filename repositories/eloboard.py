from __future__ import annotations

from collections import Counter, defaultdict

from models.eloboard import EloMatch
from repositories.supabase import get_supabase


def get_latest_match_id() -> int:
    db = get_supabase()
    rows = db.table("elo_matches").select("elo_match_id").order("elo_match_id", desc=True).limit(1).execute().data or []
    return int(rows[0]["elo_match_id"]) if rows else 0


def load_categories() -> dict[str, int]:
    db = get_supabase()
    rows = db.table("elo_categories").select("category_id,name").order("category_id").execute().data or []
    return {str(r.get("name") or ""): int(r["category_id"]) for r in rows}


def ensure_categories(names: set[str]) -> dict[str, int]:
    db = get_supabase()
    current = load_categories()
    next_id = max(current.values(), default=-1) + 1
    new_rows = []
    for name in sorted(names):
        if name in current:
            continue
        current[name] = next_id
        new_rows.append({"category_id": next_id, "name": name})
        next_id += 1
    if new_rows:
        db.table("elo_categories").upsert(new_rows, on_conflict="category_id").execute()
    return current


def upsert_dimensions(matches: list[EloMatch]) -> dict[str, int]:
    if not matches:
        return {"players": 0, "maps": 0, "categories": 0}
    db = get_supabase()
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
    if players:
        db.table("elo_players").upsert(players, on_conflict="elo_id").execute()
    if maps:
        db.table("elo_maps").upsert([{"map_id": k, "name": v} for k, v in maps.items()], on_conflict="map_id").execute()
    before = load_categories()
    ensure_categories(categories)
    return {"players": len(players), "maps": len(maps), "categories": max(0, len(categories - set(before)))}


def upsert_matches(matches: list[EloMatch]) -> int:
    if not matches:
        return 0
    db = get_supabase()
    categories = ensure_categories({m.category for m in matches})
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
    # Keep request payloads moderate for PostgREST.
    for start in range(0, len(payload), 500):
        db.table("elo_matches").upsert(payload[start:start + 500], on_conflict="elo_match_id").execute()
    return len(payload)


def load_match_ids_from(min_id: int) -> set[int]:
    if min_id <= 0:
        return set()
    db = get_supabase()
    out: set[int] = set()
    start = 0
    page = 1000
    while True:
        rows = (
            db.table("elo_matches")
            .select("elo_match_id")
            .gte("elo_match_id", min_id)
            .order("elo_match_id")
            .range(start, start + page - 1)
            .execute()
            .data
            or []
        )
        out.update(int(r["elo_match_id"]) for r in rows)
        if len(rows) < page:
            break
        start += page
    return out


def delete_match_ids(ids: set[int]) -> int:
    if not ids:
        return 0
    db = get_supabase()
    # PostgREST in_ is safer in moderate chunks.
    values = sorted(ids)
    for start in range(0, len(values), 200):
        db.table("elo_matches").delete().in_("elo_match_id", values[start:start + 200]).execute()
    return len(values)


def stage_unknown_elo_candidates(matches: list[EloMatch]) -> int:
    """Stage match-only unknown players without inventing a SOOP id.

    Match API exposes only elo_id. We use a synthetic id (`elo:<id>`) until the
    tier API discovers the actual SOOP id. Nothing is inserted into tier_members.
    """
    if not matches:
        return 0
    db = get_supabase()
    def paged_ids(table: str) -> list[dict]:
        out = []
        start = 0
        while True:
            batch = (
                db.table(table)
                .select("id,elo_id")
                .order("id")
                .range(start, start + 999)
                .execute()
                .data
                or []
            )
            out.extend(batch)
            if len(batch) < 1000:
                return out
            start += 1000

    roster_rows = paged_ids("tier_members")
    known = {int(r["elo_id"]) for r in roster_rows if r.get("elo_id") is not None}
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
