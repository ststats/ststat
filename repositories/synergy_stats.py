from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone

from models.synergy_stats import MonthlyLiveStats, SponsorStats, SynergyRosterMember
from repositories.supabase import get_supabase

PAGE_SIZE = 1000


def load_roster_for_synergy() -> list[SynergyRosterMember]:
    db = get_supabase()
    rows = (
        db.table("tier_members")
        .select("soop_id,elo_id,nickname,role,affiliation,race,tier,modified_at,source_order")
        .order("source_order")
        .execute()
        .data
        or []
    )
    out: list[SynergyRosterMember] = []
    for row in rows:
        soop_id = str(row.get("soop_id") or "").strip()
        nickname = str(row.get("nickname") or "").strip()
        if not soop_id or not nickname:
            continue
        elo_id = row.get("elo_id")
        try:
            elo_id = int(elo_id) if elo_id is not None else None
        except (TypeError, ValueError):
            elo_id = None
        out.append(SynergyRosterMember(
            soop_id=soop_id,
            elo_id=elo_id,
            nickname=nickname,
            role=str(row.get("role") or ""),
            affiliation=(str(row.get("affiliation") or "").strip() or None),
            race=(str(row.get("race") or "").strip() or None),
            tier=(str(row.get("tier") or "").strip() or None),
            modified_at=(str(row.get("modified_at") or "").strip() or None),
        ))
    return out


def _paged_matches(start_date: str, end_date: str) -> list[dict]:
    db = get_supabase()
    out: list[dict] = []
    start = 0
    while True:
        rows = (
            db.table("elo_matches")
            .select("winner_elo_id,loser_elo_id,match_date")
            .gte("match_date", start_date)
            .lte("match_date", end_date)
            .order("elo_match_id")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        out.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return out


def aggregate_sponsor_stats(start_date: str, end_date: str) -> dict[int, SponsorStats]:
    wins: dict[int, int] = defaultdict(int)
    losses: dict[int, int] = defaultdict(int)
    for row in _paged_matches(start_date, end_date):
        winner = row.get("winner_elo_id")
        loser = row.get("loser_elo_id")
        if winner is not None:
            wins[int(winner)] += 1
        if loser is not None:
            losses[int(loser)] += 1
    ids = set(wins) | set(losses)
    return {elo_id: SponsorStats(wins=wins[elo_id], losses=losses[elo_id]) for elo_id in ids}


def upsert_poonggo_month(month_start: str, data: dict[str, MonthlyLiveStats]) -> int:
    if not data:
        return 0
    db = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    payload = [
        {
            "month_start": month_start,
            "soop_id": soop_id,
            "balloons": stats.balloons,
            "broadcast_seconds": stats.broadcast_seconds,
            "cumulative_viewers": stats.cumulative_viewers,
            "fetched_at": now,
        }
        for soop_id, stats in data.items()
    ]
    for i in range(0, len(payload), 500):
        db.table("poonggo_monthly_stats").upsert(
            payload[i:i + 500], on_conflict="month_start,soop_id"
        ).execute()
    return len(payload)


def upsert_daily_snapshot(stat_date: str, rows: list[dict]) -> int:
    if not rows:
        raise RuntimeError("Refusing to write empty Synergy daily snapshot")
    db = get_supabase()
    for i in range(0, len(rows), 500):
        db.table("daily_member_stats").upsert(
            rows[i:i + 500], on_conflict="stat_date,soop_id"
        ).execute()
    return len(rows)


def existing_snapshot_count(stat_date: str) -> int:
    db = get_supabase()
    result = (
        db.table("daily_member_stats")
        .select("soop_id", count="exact")
        .eq("stat_date", stat_date)
        .limit(1)
        .execute()
    )
    return int(result.count or 0)


def apply_roster_backfill(member: SynergyRosterMember, from_date: str) -> int:
    db = get_supabase()
    payload = {
        "elo_id": member.elo_id,
        "nickname": member.nickname,
        "role": member.role,
        "affiliation": member.affiliation,
        "race": member.race,
        "tier": member.tier,
    }
    result = (
        db.table("daily_member_stats")
        .update(payload)
        .eq("soop_id", member.soop_id)
        .gte("stat_date", from_date)
        .execute()
    )
    return len(result.data or [])


def clear_modified_at(soop_ids: list[str]) -> int:
    if not soop_ids:
        return 0
    db = get_supabase()
    written = 0
    for soop_id in soop_ids:
        db.table("tier_members").update({"modified_at": None}).eq("soop_id", soop_id).execute()
        written += 1
    return written


def get_month_confirmation(month_start: str) -> dict:
    db = get_supabase()
    rows = (
        db.table("synergy_month_confirmations")
        .select("month_start,poonggo_complete,sponsor_complete")
        .eq("month_start", month_start)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else {"month_start": month_start, "poonggo_complete": False, "sponsor_complete": False}


def upsert_month_confirmation(month_start: str, poonggo_complete: bool, sponsor_complete: bool) -> None:
    db = get_supabase()
    db.table("synergy_month_confirmations").upsert({
        "month_start": month_start,
        "poonggo_complete": poonggo_complete,
        "sponsor_complete": sponsor_complete,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="month_start").execute()


def update_closed_month_numeric_stats(
    stat_date: str,
    roster: list[SynergyRosterMember],
    poonggo: dict[str, MonthlyLiveStats],
    sponsor: dict[int, SponsorStats],
) -> int:
    """Update only numeric source fields on an existing archived day.

    Historical roster metadata (team/tier/name/race/role) must stay as it was for that
    date unless a modified_at backfill explicitly changes it.
    """
    db = get_supabase()
    written = 0
    for member in roster:
        live = poonggo.get(member.soop_id)
        sponsor_stats = sponsor.get(member.elo_id) if member.elo_id is not None else None
        payload = {}
        if live is not None:
            payload.update({
                "balloons": live.balloons,
                "broadcast_seconds": live.broadcast_seconds,
                "cumulative_viewers": live.cumulative_viewers,
            })
        # A successful EloBoard DB aggregation means absence == zero for the month.
        payload.update({
            "sponsor_wins": sponsor_stats.wins if sponsor_stats else 0,
            "sponsor_losses": sponsor_stats.losses if sponsor_stats else 0,
            "sponsor_updated_at": datetime.now(timezone.utc).isoformat(),
        })
        if payload:
            result = (
                db.table("daily_member_stats")
                .update(payload)
                .eq("stat_date", stat_date)
                .eq("soop_id", member.soop_id)
                .execute()
            )
            written += len(result.data or [])
    return written
