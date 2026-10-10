from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from models.synergy_stats import MonthlyLiveStats, SponsorStats, SynergyRosterMember
from repositories.supabase import fetch_all, get_supabase


def load_roster_for_synergy() -> list[SynergyRosterMember]:
    """tier_members 전체(1000행이 넘어도 fetch_all이 끝까지 읽는다)."""
    db = get_supabase()
    rows = fetch_all(lambda: db.table("tier_members").select(
        "soop_id,elo_id,nickname,role,affiliation,race,tier,gender,birth_date,modified_at,source_order,id"
    ).order("source_order").order("id"))

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
    return fetch_all(lambda: db.table("elo_matches").select("winner_elo_id,loser_elo_id,match_date")
                     .gte("match_date", start_date).lte("match_date", end_date).order("elo_match_id"))


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
            "viewership_seconds": stats.viewership_seconds,
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


DAILY_SNAPSHOT_COLUMNS = (
    "stat_date,month_start,soop_id,elo_id,nickname,role,affiliation,"
    "race,tier,gender,birth_date,balloons,broadcast_seconds,"
    "cumulative_viewers,viewership_seconds,sponsor_wins,sponsor_losses,updated_at,"
    "sponsor_updated_at"
)
# 스폰 승패 비교에 필요한 칸만(바뀐 날만 위의 전체 칸을 다시 받아 게시한다 - egress 절약)
DAILY_SPONSOR_COLUMNS = "soop_id,elo_id,sponsor_wins,sponsor_losses"


def load_daily_snapshot_rows(stat_date: str, columns: str = DAILY_SNAPSHOT_COLUMNS) -> list[dict]:
    db = get_supabase()
    return fetch_all(lambda: db.table("daily_member_stats").select(columns).eq("stat_date", stat_date).order("soop_id"))


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
    """저장된 날의 수치 칸만 고친다. 그날의 명단 정보(소속·티어·이름·종족·역할)는 그대로 둔다."""
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
                "viewership_seconds": live.viewership_seconds,
            })
        # 집계가 성공했으니 없는 선수는 그달 0승 0패다
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
    rows = fetch_all(lambda: db.table("synergy_daily_dates").select("stat_date")
                     .gte("stat_date", from_date).lte("stat_date", to_date).order("stat_date"))
    return [str(r["stat_date"])[:10] for r in rows]


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
    """게시된 날들의 스폰 승패를 지금의 elo_matches로 다시 맞춘다(늦은 등록·정정·ELO ID 소급 수정 대비).
    바뀐 날만 publish_daily_member_stats로 하루 통째로 원자적으로 다시 게시하고, 다른 값은 그대로 둔다."""
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
            def expected(row):
                eid = row.get("elo_id")
                return totals[day].get(int(eid), (0, 0)) if eid is not None else (0, 0)
            # 먼저 승패 칸만 받아 비교하고, 바뀐 날만 전체 행을 받아 통째로 다시 게시한다
            light = load_daily_snapshot_rows(day, DAILY_SPONSOR_COLUMNS)
            if all((r.get("sponsor_wins"), r.get("sponsor_losses")) == expected(r) for r in light):
                continue
            rows = load_daily_snapshot_rows(day)
            diff = 0
            for row in rows:
                w, l = expected(row)
                if (row.get("sponsor_wins"), row.get("sponsor_losses")) != (w, l):
                    row.update({"sponsor_wins": w, "sponsor_losses": l, "sponsor_updated_at": now})
                    diff += 1
            if diff:
                upsert_daily_snapshot(day, rows)
                changed_days.append(day)
                changed_rows += diff
    return {"days_checked": len(days), "days_changed": changed_days[:60], "rows_changed": changed_rows}


def load_poonggo_month(month_start: str) -> dict[str, MonthlyLiveStats]:
    """이미 저장된 그달 월 누적치(풍고 또는 트래키파이, 급감 검사용)."""
    db = get_supabase()
    rows = fetch_all(lambda: db.table("poonggo_monthly_stats").select("soop_id,balloons,broadcast_seconds,cumulative_viewers")
                     .eq("month_start", month_start).order("soop_id"))
    return {
        str(r["soop_id"]): MonthlyLiveStats(
            balloons=int(r.get("balloons") or 0),
            broadcast_seconds=int(r.get("broadcast_seconds") or 0),
            cumulative_viewers=int(r.get("cumulative_viewers") or 0),
        )
        for r in rows
    }


def first_snapshot_date() -> str | None:
    """방송통계가 처음 게시된 날짜(그 전 달은 월말 스냅샷이 없는 게 정상)."""
    rows = (
        get_supabase().table("synergy_daily_dates")
        .select("stat_date").order("stat_date").limit(1).execute().data
        or []
    )
    return str(rows[0]["stat_date"])[:10] if rows else None


def load_missing_broadcasts() -> list[dict]:
    """트래키파이 월간 요약에서 빠져 직접 더할 방송(missing_broadcasts)."""
    db = get_supabase()
    return fetch_all(lambda: db.table("missing_broadcasts")
                     .select("id,soop_id,started_at,ended_at,avg_viewers,applied_through").order("id"))


def mark_missing_broadcast_applied(row_id: int, through: str) -> None:
    get_supabase().table("missing_broadcasts").update({"applied_through": through}).eq("id", row_id).execute()
