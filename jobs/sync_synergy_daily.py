from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from collectors.poonggo import fetch_monthly
from models.sync_job import JobResult
from processors.synergy_daily import build_daily_rows
from jobs.sync_eloboard import rescan_cutoff
from repositories.synergy_stats import (
    aggregate_sponsor_stats,
    apply_roster_backfill,
    clear_modified_at,
    existing_snapshot_count,
    first_snapshot_date,
    get_month_confirmation,
    load_roster_for_synergy,
    load_poonggo_month,
    load_snapshot_roster,
    refresh_sponsor_stats,
    upsert_daily_snapshot,
    upsert_month_confirmation,
    upsert_poonggo_month,
    update_closed_month_numeric_stats,
)

KST = ZoneInfo("Asia/Seoul")
MIN_ROSTER_COUNT = 100
MIN_POONGGO_COVERAGE = 1.0
MAX_CONFIRM_MONTHS_PER_RUN = 12


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _previous_month(d: date) -> date:
    first = _month_start(d)
    return (first - timedelta(days=1)).replace(day=1)


def _month_end(month_start: date) -> date:
    return date(month_start.year, month_start.month, calendar.monthrange(month_start.year, month_start.month)[1])


def _marker_date(marker: str | None) -> str | None:
    try:
        return date.fromisoformat(str(marker or "")[:10]).isoformat()
    except ValueError:
        return None


# 월 누적치는 달 안에서 줄지 않는다. 여러 계정이 한꺼번에 크게 줄면 Poonggo 쪽 일시 이상일 가능성이
# 커서 이번 실행은 게시하지 않고(이전 값 유지) 다음 실행에서 다시 받는다. 한두 계정의 감소는
# 실제 정정일 수 있어 그대로 받는다.
DROP_RATIO = 0.5
MAX_DROPPED_ACCOUNTS = 3
MAX_DROPPED_SHARE = 0.10


def _check_poonggo_drop(previous: dict, current: dict, label: str) -> int:
    base = [sid for sid, prev in previous.items() if prev.balloons > 0 and sid in current]
    dropped = [sid for sid in base if current[sid].balloons < previous[sid].balloons * DROP_RATIO]
    if len(dropped) > max(MAX_DROPPED_ACCOUNTS, int(len(base) * MAX_DROPPED_SHARE)):
        raise RuntimeError(
            f"Poonggo {label}: balloons dropped by >50% for {len(dropped)}/{len(base)} accounts; "
            f"keeping previous values and retrying next run (sample={sorted(dropped)[:10]})"
        )
    return len(dropped)


def _require_poonggo_coverage(roster, poonggo, label: str) -> None:
    # fetch_monthly는 정상 응답에서 생략된 무방송 ID를 0으로 채운다. 따라서 여기서의
    # 누락은 호출자가 불완전한 결과를 넘겼거나 수집기 계약이 깨진 경우만 뜻한다.
    expected = {m.soop_id for m in roster}
    received = set(poonggo)
    coverage = len(expected & received) / len(expected) if expected else 1.0
    if coverage < MIN_POONGGO_COVERAGE:
        missing = sorted(expected - received)
        raise RuntimeError(
            f"Poonggo {label} coverage too small: {coverage:.1%} "
            f"({len(received)}/{len(expected)}); missing sample={missing[:10]}"
        )


def _confirm_closed_months(today: date) -> dict:
    # We only need to confirm months that already have a last-day snapshot in Supabase.
    # Start with previous month and walk back at most MAX_CONFIRM_MONTHS_PER_RUN; missing snapshots are skipped.
    checked = 0
    confirmed = 0
    poonggo_rows = 0
    missing_month_end: list[str] = []
    # 방송통계를 처음 게시하기 전 달은 월말 스냅샷이 없는 게 정상이라 보지 않는다
    first_day = first_snapshot_date()
    month = _previous_month(today)
    for _ in range(MAX_CONFIRM_MONTHS_PER_RUN):
        last_day = _month_end(month)
        last_day_str = last_day.isoformat()
        if first_day is None or last_day_str < first_day:
            break
        if existing_snapshot_count(last_day_str) == 0:
            # 월말 당일 게시가 빠진 달은 확정할 수 없다. 조용히 넘기지 않고 작업 기록에 남긴다.
            if not get_month_confirmation(month.isoformat()).get("poonggo_complete"):
                missing_month_end.append(last_day_str)
            month = _previous_month(month)
            continue

        roster = load_snapshot_roster(last_day_str)
        if not roster:
            raise RuntimeError(f"Published snapshot roster is empty for {last_day_str}")

        month_start_str = month.isoformat()
        flags = get_month_confirmation(month_start_str)
        if flags.get("poonggo_complete") and flags.get("sponsor_complete"):
            month = _previous_month(month)
            continue

        checked += 1
        poonggo_ok = bool(flags.get("poonggo_complete"))
        sponsor_ok = bool(flags.get("sponsor_complete"))

        # Rebuild the whole last-day snapshot only when both sources are available.
        poonggo = None
        sponsor = None
        if not poonggo_ok:
            poonggo = fetch_monthly(month.year, month.month, [m.soop_id for m in roster])
            _require_poonggo_coverage(roster, poonggo, month_start_str)
            upsert_poonggo_month(month_start_str, poonggo)
            poonggo_rows += len(poonggo)
            poonggo_ok = True

        if not sponsor_ok:
            sponsor = aggregate_sponsor_stats(month_start_str, last_day_str)
            # zero sponsor records can be legitimate; DB query success itself is enough.
            sponsor_ok = True

        if poonggo is None:
            # Fetch again to reconstruct the confirmed last-day snapshot. This is deliberate and rare (once/month).
            poonggo = fetch_monthly(month.year, month.month, [m.soop_id for m in roster])
            _require_poonggo_coverage(roster, poonggo, month_start_str)
        if sponsor is None:
            sponsor = aggregate_sponsor_stats(month_start_str, last_day_str)

        update_closed_month_numeric_stats(last_day_str, roster, poonggo, sponsor)
        upsert_month_confirmation(month_start_str, poonggo_ok, sponsor_ok)
        confirmed += 1
        month = _previous_month(month)

    return {"checked": checked, "confirmed": confirmed, "poonggo_rows": poonggo_rows,
            "missing_month_end": missing_month_end}


def run() -> JobResult:
    now_kst = datetime.now(KST)
    today = now_kst.date()
    stat_date = today.isoformat()
    month_start = _month_start(today)
    month_start_str = month_start.isoformat()

    roster = load_roster_for_synergy()
    if len(roster) < MIN_ROSTER_COUNT:
        raise RuntimeError(f"Roster unexpectedly small ({len(roster)}); refusing Synergy write")

    soop_ids = [m.soop_id for m in roster]
    poonggo = fetch_monthly(today.year, today.month, soop_ids)
    _require_poonggo_coverage(roster, poonggo, stat_date)
    poonggo_dropped = _check_poonggo_drop(load_poonggo_month(month_start_str), poonggo, stat_date)

    sponsor = aggregate_sponsor_stats(month_start_str, stat_date)
    rows = build_daily_rows(stat_date, month_start_str, roster, poonggo, sponsor)
    if len(rows) != len(roster):
        raise RuntimeError("Daily snapshot row count differs from roster count")

    poonggo_written = upsert_poonggo_month(month_start_str, poonggo)
    daily_written = upsert_daily_snapshot(stat_date, rows)

    # Apply manual roster metadata corrections to existing historical snapshots.
    corrected_members = {}
    corrected_rows = 0
    for member in roster:
        if not member.modified_at:
            continue
        try:
            modified_date = date.fromisoformat(member.modified_at[:10]).isoformat()
        except ValueError:
            # Preserve malformed marker for admin review; never clear it automatically.
            continue
        corrected_rows += apply_roster_backfill(member, modified_date)
        corrected_members[member.soop_id] = member.modified_at

    # 지난 날의 스폰 승패를 지금 경기 기록에 다시 맞춘다: EloBoard가 다시 읽는 범위(이번 달, 월초엔
    # 지난달부터)에서는 늦게 등록·정정된 경기가 들어올 수 있고, ELO ID를 소급 수정한 선수는 그
    # 날짜부터 새 ID 기준으로 다시 세야 한다. 바뀐 날만 다시 게시한다.
    refresh_from = rescan_cutoff(today)
    backfill_dates = [d for d in (_marker_date(m.modified_at) for m in roster if m.modified_at) if d]
    if backfill_dates:
        refresh_from = min([refresh_from, *backfill_dates])
    sponsor_refresh = refresh_sponsor_stats(refresh_from, (today - timedelta(days=1)).isoformat())

    # Clear markers only after all affected member backfills have succeeded.
    cleared = clear_modified_at(corrected_members) if corrected_members else 0

    confirmations = _confirm_closed_months(today)

    return JobResult(
        records_read=len(roster) + len(poonggo),
        records_written=poonggo_written + daily_written + corrected_rows + confirmations["confirmed"],
        records_skipped=max(0, len(roster) - len(poonggo)),
        source_cursor=stat_date,
        metadata={
            "stat_date": stat_date,
            "month_start": month_start_str,
            "roster_count": len(roster),
            "poonggo_count": len(poonggo),
            "poonggo_dropped_accounts": poonggo_dropped,
            "sponsor_players": len(sponsor),
            "daily_rows": daily_written,
            "modified_members_backfilled": len(corrected_members),
            "historical_rows_corrected": corrected_rows,
            "modified_at_cleared": cleared,
            "closed_months_checked": confirmations["checked"],
            "closed_months_confirmed": confirmations["confirmed"],
            "missing_month_end_snapshots": confirmations["missing_month_end"],
            "sponsor_refresh_from": refresh_from,
            "sponsor_refresh": sponsor_refresh,
            "atomic_daily_publish": True,
        },
    )
