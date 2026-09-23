from __future__ import annotations

from datetime import datetime, timezone

from models.synergy_stats import MonthlyLiveStats, SponsorStats, SynergyRosterMember


def build_daily_rows(
    stat_date: str,
    month_start: str,
    roster: list[SynergyRosterMember],
    poonggo: dict[str, MonthlyLiveStats],
    sponsor: dict[int, SponsorStats],
) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for member in roster:
        live = poonggo.get(member.soop_id, MonthlyLiveStats())
        sponsor_stats = sponsor.get(member.elo_id, SponsorStats()) if member.elo_id is not None else SponsorStats()
        rows.append({
            "stat_date": stat_date,
            "month_start": month_start,
            "soop_id": member.soop_id,
            "elo_id": member.elo_id,
            "nickname": member.nickname,
            "role": member.role,
            "affiliation": member.affiliation,
            "race": member.race,
            "tier": member.tier,
            "gender": member.gender,
            "birth_date": member.birth_date,
            "balloons": live.balloons,
            "broadcast_seconds": live.broadcast_seconds,
            "cumulative_viewers": live.cumulative_viewers,
            "sponsor_wins": sponsor_stats.wins,
            "sponsor_losses": sponsor_stats.losses,
            "updated_at": now,
            "sponsor_updated_at": now,
        })
    return rows
