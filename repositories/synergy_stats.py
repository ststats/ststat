from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone

from models.synergy_stats import MonthlyLiveStats, SponsorStats, SynergyRosterMember
from repositories.supabase import get_supabase

PAGE_SIZE = 1000


def load_roster_for_synergy() -> list[SynergyRosterMember]:
    """Load the complete roster from tier_members.

    PostgREST commonly caps a single SELECT at 1000 rows, so this must page
    explicitly or larger rosters are silently truncated.
    """
    db = get_supabase()
    page_size = 1000
    start = 0
    rows: list[dict] = []

    while True:
        response = (
            db.table("tier_members")
            .select(
                "soop_id,elo_id,nickname,role,affiliation,"
                "race,tier,gender,birth_date,modified_at,source_order,id"
            )
            .order("source_order")
            .order("id")
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size

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

        out.append(
            SynergyRosterMember(
                soop_id=soop_id,
                elo_id=elo_id,
                nickname=nickname,
                role=str(row.get("role") or ""),
                affiliation=(str(row.get("affiliation") or "").strip() or None),
                race=(str(row.get("race") or "").strip() or None),
                tier=(str(row.get("tier") or "").strip() or None),
                modified_at=(str(row.get("modified_at") or "").strip() or None),
                gender=(str(row.get("gender") or "").strip() or None),
                birth_date=(str(row.get("birth_date") or "").strip() or None),
            )
        )
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
    if any(str(row.get("stat_date") or "") != stat_date for row in rows):
        raise RuntimeError("Daily snapshot contains rows for a different date")
    if len({str(row.get("soop_id") or "") for row in rows}) != len(rows):
        raise RuntimeError("Daily snapshot contains duplicate SOOP ids")
    get_supabase().rpc(
        "publish_daily_member_stats",
        {"p_stat_date": stat_date, "p_rows": rows},
    ).execute()
    return len(rows)


def load_daily_snapshot_rows(stat_date: str) -> list[dict]:
    db = get_supabase()
    rows: list[dict] = []
    start = 0
    while True:
        batch = (
            db.table("daily_member_stats")
            .select(
                "stat_date,month_start,soop_id,elo_id,nickname,role,affiliation,"
                "race,tier,gender,birth_date,balloons,broadcast_seconds,"
                "cumulative_viewers,sponsor_wins,sponsor_losses,updated_at,"
                "sponsor_updated_at"
            )
            .eq("stat_date", stat_date)
            .order("soop_id")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows


def load_snapshot_roster(stat_date: str) -> list[SynergyRosterMember]:
    rows = load_daily_snapshot_rows(stat_date)
    return [
        SynergyRosterMember(
            soop_id=str(row["soop_id"]),
            elo_id=int(row["elo_id"]) if row.get("elo_id") is not None else None,
            nickname=str(row.get("nickname") or ""),
            role=str(row.get("role") or ""),
            affiliation=row.get("affiliation"),
            race=row.get("race"),
            tier=row.get("tier"),
            modified_at=None,
            gender=row.get("gender"),
            birth_date=str(row.get("birth_date") or "") or None,
        )
        for row in rows
    ]


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
        "gender": member.gender,
        "birth_date": member.birth_date,
    }
    result = (
        db.table("daily_member_stats")
        .update(payload)
        .eq("soop_id", member.soop_id)
        .gte("stat_date", from_date)
        .execute()
    )
    return len(result.data or [])


def clear_modified_at(markers: dict[str, str]) -> int:
    if not markers:
        return 0
    db = get_supabase()
    written = 0
    for soop_id, marker in markers.items():
        result = (
            db.table("tier_members")
            .update({"modified_at": None})
            .eq("soop_id", soop_id)
            .eq("modified_at", marker)
            .execute()
        )
        written += len(result.data or [])
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
    rows = load_daily_snapshot_rows(stat_date)
    if not rows:
        raise RuntimeError(f"Cannot finalize missing daily snapshot {stat_date}")
    members = {member.soop_id: member for member in roster}
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        member = members.get(str(row.get("soop_id") or ""))
        if member is None:
            raise RuntimeError(f"Snapshot member missing from finalization roster: {row.get('soop_id')}")
        live = poonggo.get(member.soop_id)
        sponsor_stats = sponsor.get(member.elo_id) if member.elo_id is not None else None
        if live is not None:
            row.update({
                "balloons": live.balloons,
                "broadcast_seconds": live.broadcast_seconds,
                "cumulative_viewers": live.cumulative_viewers,
            })
        # A successful EloBoard DB aggregation means absence == zero for the month.
        row.update({
            "sponsor_wins": sponsor_stats.wins if sponsor_stats else 0,
            "sponsor_losses": sponsor_stats.losses if sponsor_stats else 0,
            "updated_at": now,
            "sponsor_updated_at": now,
        })
    return upsert_daily_snapshot(stat_date, rows)


def snapshot_dates_between(from_date: str, to_date: str) -> list[str]:
    """[from_date, to_date] 안에서 이미 게시된 방송통계 날짜(오름차순)."""
    db = get_supabase()
    out: list[str] = []
    start = 0
    while True:
        rows = (
            db.table("synergy_daily_dates")
            .select("stat_date")
            .gte("stat_date", from_date)
            .lte("stat_date", to_date)
            .order("stat_date")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        out.extend(str(r["stat_date"])[:10] for r in rows)
        if len(rows) < PAGE_SIZE:
            return out
        start += PAGE_SIZE


def _cumulative_sponsor(matches: list[dict], days: list[str]) -> dict[str, dict[int, tuple[int, int]]]:
    """한 달 경기로 날짜마다 '월초~그날' 누적 (승, 패)를 만든다(게시 때와 같은 기준)."""
    ordered = sorted(matches, key=lambda r: str(r.get("match_date") or ""))
    wins: dict[int, int] = defaultdict(int)
    losses: dict[int, int] = defaultdict(int)
    out: dict[str, dict[int, tuple[int, int]]] = {}
    i = 0
    for day in sorted(days):
        while i < len(ordered) and str(ordered[i].get("match_date") or "")[:10] <= day:
            w, l = ordered[i].get("winner_elo_id"), ordered[i].get("loser_elo_id")
            if w is not None:
                wins[int(w)] += 1
            if l is not None:
                losses[int(l)] += 1
            i += 1
        out[day] = {pid: (wins[pid], losses[pid]) for pid in set(wins) | set(losses)}
    return out


def refresh_sponsor_stats(from_date: str, to_date: str) -> dict:
    """이미 게시된 날들의 스폰 승패를 지금의 elo_matches 기준으로 다시 맞춘다.

    경기가 늦게 등록·정정되거나 선수의 ELO ID가 소급 수정되면 지난 날의 스폰 승패가
    원본과 어긋난다(월말 확정된 달도 마찬가지). 바뀐 행이 있는 날만 하루 단위로 통째로
    다시 게시한다(publish_daily_member_stats RPC - 원자적). 별풍선 등 다른 값은 그대로 둔다.
    """
    days = snapshot_dates_between(from_date, to_date)
    by_month: dict[str, list[str]] = defaultdict(list)
    for day in days:
        by_month[day[:7]].append(day)
    changed_days: list[str] = []
    changed_rows = 0
    now = datetime.now(timezone.utc).isoformat()
    for ym in sorted(by_month):
        month_days = by_month[ym]
        totals = _cumulative_sponsor(_paged_matches(f"{ym}-01", max(month_days)), month_days)
        for day in month_days:
            rows = load_daily_snapshot_rows(day)
            diff = 0
            for row in rows:
                eid = row.get("elo_id")
                w, l = totals[day].get(int(eid), (0, 0)) if eid is not None else (0, 0)
                if (row.get("sponsor_wins"), row.get("sponsor_losses")) != (w, l):
                    row.update({"sponsor_wins": w, "sponsor_losses": l, "sponsor_updated_at": now})
                    diff += 1
            if diff:
                upsert_daily_snapshot(day, rows)
                changed_days.append(day)
                changed_rows += diff
    return {"days_checked": len(days), "days_changed": changed_days[:60], "rows_changed": changed_rows}


def load_poonggo_month(month_start: str) -> dict[str, MonthlyLiveStats]:
    """이미 저장된 그달 Poonggo 누적치(급감 검사용)."""
    db = get_supabase()
    out: dict[str, MonthlyLiveStats] = {}
    start = 0
    while True:
        rows = (
            db.table("poonggo_monthly_stats")
            .select("soop_id,balloons,broadcast_seconds,cumulative_viewers")
            .eq("month_start", month_start)
            .order("soop_id")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        for r in rows:
            out[str(r["soop_id"])] = MonthlyLiveStats(
                balloons=int(r.get("balloons") or 0),
                broadcast_seconds=int(r.get("broadcast_seconds") or 0),
                cumulative_viewers=int(r.get("cumulative_viewers") or 0),
            )
        if len(rows) < PAGE_SIZE:
            return out
        start += PAGE_SIZE
