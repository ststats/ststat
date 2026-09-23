from __future__ import annotations

from datetime import date

from models.roster import ExistingRosterMember, RosterCandidate
from repositories.supabase import get_supabase


# Part 2 ownership rule:
# Existing tier_members rows: ststat may update ONLY the EloBoard source name (`name`).
# All admin-managed fields remain untouched.
AUTO_OWNED_EXISTING_COLUMNS = {"name"}


def load_roster() -> dict[str, ExistingRosterMember]:
    db = get_supabase()
    rows = []
    start = 0
    while True:
        batch = (
            db.table("tier_members")
            .select("id,soop_id,name,elo_id,modified_at")
            .order("source_order")
            .order("id")
            .range(start, start + 999)
            .execute()
            .data
            or []
        )
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000

    out: dict[str, ExistingRosterMember] = {}
    for row in rows:
        soop_id = str(row.get("soop_id") or "").strip()
        if not soop_id:
            continue
        out[soop_id.lower()] = ExistingRosterMember(
            soop_id=soop_id,
            elo_name=(str(row.get("name") or "").strip() or None),
            elo_id=row.get("elo_id"),
            modified_at=(str(row.get("modified_at") or "").strip() or None),
        )
    return out


def load_pending_ids() -> set[str]:
    db = get_supabase()
    rows = []
    start = 0
    while True:
        batch = (
            db.table("tier_member_candidates")
            .select("id")
            .order("id")
            .range(start, start + 999)
            .execute()
            .data
            or []
        )
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000
    return {
        str(row.get("id") or "").strip().lower()
        for row in rows
        if str(row.get("id") or "").strip()
    }


def update_elo_names(updates: dict[str, str]) -> int:
    """Update only the auto-owned EloBoard name on existing roster rows."""
    if not updates:
        return 0

    db = get_supabase()
    written = 0
    for canonical_soop_id, elo_name in updates.items():
        payload = {"name": elo_name}
        unknown = set(payload) - AUTO_OWNED_EXISTING_COLUMNS
        if unknown:
            raise RuntimeError(f"Refusing to update non-owned roster fields: {sorted(unknown)}")
        db.table("tier_members").update(payload).eq("soop_id", canonical_soop_id).execute()
        written += 1
    return written


def upsert_candidates(candidates: list[RosterCandidate]) -> int:
    if not candidates:
        return 0

    today = date.today().isoformat()
    payload = [
        {
            "id": c.id,
            "nickname": c.nickname,
            "elo_id": c.elo_id,
            "gender": c.gender,
            "race": c.race,
            "tier": c.tier,
            "affiliation": c.affiliation,
            "source": c.source,
            "found_at": today,
            "last_seen_at": today,
            "status": "pending",
        }
        for c in candidates
    ]

    db = get_supabase()
    db.table("tier_member_candidates").upsert(payload, on_conflict="id").execute()
    return len(payload)
