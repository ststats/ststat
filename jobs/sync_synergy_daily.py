from __future__ import annotations

import calendar
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from collectors import poonggo as poonggo_source
from collectors import trackify as trackify_source
from models.sync_job import JobResult
from models.synergy_stats import MonthlyLiveStats
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
    load_daily_snapshot_rows,
    load_missing_broadcasts,
    load_snapshot_roster,
    mark_missing_broadcast_applied,
    refresh_sponsor_stats,
    snapshot_dates_between,
    upsert_daily_snapshot,
    upsert_month_confirmation,
    upsert_poonggo_month,
    update_closed_month_numeric_stats,
)

KST = ZoneInfo("Asia/Seoul")
MIN_ROSTER_COUNT = 100
MIN_POONGGO_COVERAGE = 1.0
MAX_CONFIRM_MONTHS_PER_RUN = 12


# 월 누적 출처는 트래키파이, MONTHLY_SOURCE=poonggo면 풍고(뷰어십 없음). poonggo_* 이름은 이 '월 누적 출처'를 뜻한다.
def _trackify_enabled() -> bool:
    return os.getenv("MONTHLY_SOURCE", "").strip().lower() != "poonggo"


def _source_name() -> str:
    return "Trackify" if _trackify_enabled() else "Poonggo"


def fetch_monthly(year: int, month: int, soop_ids: list[str]):
    if not _trackify_enabled():
        return poonggo_source.fetch_monthly(year, month, soop_ids)
    monthly = trackify_source.fetch_monthly(year, month, soop_ids)
    return _add_missing_broadcasts(monthly, load_missing_broadcasts(), date(year, month, 1), _next_month(date(year, month, 1)))


# 트래키파이 요약에서 빠진 방송(missing_broadcasts, 관리자 입력)을 기간에 걸친 몫만큼 더한다. 시각은 KST.
def _missing_overlap(broadcast: dict, start: date, end: date) -> tuple[int, int]:
    begin = max(datetime.fromisoformat(str(broadcast["started_at"])), datetime.combine(start, datetime.min.time()))
    finish = min(datetime.fromisoformat(str(broadcast["ended_at"])), datetime.combine(end, datetime.min.time()))
    seconds = max(0, int((finish - begin).total_seconds()))
    return seconds, seconds * int(broadcast["avg_viewers"])


def _add_missing_broadcasts(monthly: dict, missing: list[dict], start: date, end: date) -> dict:
    for broadcast in missing:
        current = monthly.get(str(broadcast["soop_id"]))
        seconds, viewership = _missing_overlap(broadcast, start, end)
        if current is None or not seconds:
            continue
        monthly[str(broadcast["soop_id"])] = MonthlyLiveStats(
            balloons=current.balloons,
            broadcast_seconds=current.broadcast_seconds + seconds,
            cumulative_viewers=current.cumulative_viewers,
            viewership_seconds=(current.viewership_seconds or 0) + viewership,
        )
    return monthly


def _apply_missing_to_published_days(today: date) -> dict:
    """빠진 방송을 이미 게시된 지난 날(applied_through 다음 날 ~ 어제)에 한 번 더한다. 오늘 행은 fetch_monthly가 더했다."""
    if not _trackify_enabled():
        return {"broadcasts": 0, "rows": 0}
    rows_changed = 0
    applied = 0
    for broadcast in load_missing_broadcasts():
        first = datetime.fromisoformat(str(broadcast["started_at"])).date()
        if broadcast.get("applied_through"):
            first = max(first, date.fromisoformat(str(broadcast["applied_through"])[:10]) + timedelta(days=1))
        if first > today:
            continue
        soop_id = str(broadcast["soop_id"])
        for day in snapshot_dates_between(first.isoformat(), (today - timedelta(days=1)).isoformat()):
            d = date.fromisoformat(day)
            seconds, viewership = _missing_overlap(broadcast, _month_start(d), d + timedelta(days=1))
            if not seconds:
                continue
            rows = load_daily_snapshot_rows(day)
            row = next((r for r in rows if str(r.get("soop_id")) == soop_id), None)
            if row is None:
                continue
            row["broadcast_seconds"] = int(row.get("broadcast_seconds") or 0) + seconds
            row["viewership_seconds"] = int(row.get("viewership_seconds") or 0) + viewership
            upsert_daily_snapshot(day, rows)
            rows_changed += 1
        mark_missing_broadcast_applied(broadcast["id"], today.isoformat())
        applied += 1
    return {"broadcasts": applied, "rows": rows_changed}


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _previous_month(d: date) -> date:
    first = _month_start(d)
    return (first - timedelta(days=1)).replace(day=1)


def _next_month(month_start: date) -> date:
    return (month_start + timedelta(days=32)).replace(day=1)


def _month_end(month_start: date) -> date:
    return date(month_start.year, month_start.month, calendar.monthrange(month_start.year, month_start.month)[1])


def _marker_date(marker: str | None) -> str | None:
    try:
        return date.fromisoformat(str(marker or "")[:10]).isoformat()
    except ValueError:
        return None


# 월 누적은 줄지 않는다. 여러 계정이 크게 줄면 출처 일시 이상으로 보고 게시하지 않는다(한두 계정은 정정일 수 있다).
DROP_RATIO = 0.5
MAX_DROPPED_ACCOUNTS = 3
MAX_DROPPED_SHARE = 0.10


def _check_poonggo_drop(previous: dict, current: dict, label: str) -> int:
    base = [sid for sid, prev in previous.items() if prev.balloons > 0 and sid in current]
    dropped = [sid for sid in base if current[sid].balloons < previous[sid].balloons * DROP_RATIO]
    if len(dropped) > max(MAX_DROPPED_ACCOUNTS, int(len(base) * MAX_DROPPED_SHARE)):
        raise RuntimeError(
            f"{_source_name()} {label}: balloons dropped by >50% for {len(dropped)}/{len(base)} accounts; "
            f"keeping previous values and retrying next run (sample={sorted(dropped)[:10]})"
        )
    return len(dropped)


def _require_poonggo_coverage(roster, poonggo, label: str) -> None:
    # 무방송 ID는 fetch_monthly가 0으로 채우므로 누락은 수집기 계약이 깨졌다는 뜻이다.
    expected = {m.soop_id for m in roster}
    received = set(poonggo)
    coverage = len(expected & received) / len(expected) if expected else 1.0
    if coverage < MIN_POONGGO_COVERAGE:
        missing = sorted(expected - received)
        raise RuntimeError(
            f"{_source_name()} {label} coverage too small: {coverage:.1%} "
            f"({len(received)}/{len(expected)}); missing sample={missing[:10]}"
        )


def _fetch_closed_month(month: date, roster) -> dict:
    # 빈 응답은 0으로 채워져 coverage를 통과하고, 확정하면 다시 보지 않으므로 급감 검사를 거친다.
    month_start_str = month.isoformat()
    poonggo = fetch_monthly(month.year, month.month, [m.soop_id for m in roster])
    _require_poonggo_coverage(roster, poonggo, month_start_str)
    _check_poonggo_drop(load_poonggo_month(month_start_str), poonggo, month_start_str)
    return poonggo


def _confirm_closed_months(today: date) -> dict:
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

        # 이미 확정된 달은 월말 전체 행을 받지 않는다
        month_start_str = month.isoformat()
        flags = get_month_confirmation(month_start_str)
        if flags.get("poonggo_complete") and flags.get("sponsor_complete"):
            month = _previous_month(month)
            continue

        roster = load_snapshot_roster(last_day_str)
        if not roster:
            raise RuntimeError(f"Published snapshot roster is empty for {last_day_str}")

        checked += 1
        poonggo_ok = bool(flags.get("poonggo_complete"))
        sponsor_ok = bool(flags.get("sponsor_complete"))

        poonggo = None
        sponsor = None
        if not poonggo_ok:
            poonggo = _fetch_closed_month(month, roster)
            upsert_poonggo_month(month_start_str, poonggo)
            poonggo_rows += len(poonggo)
            poonggo_ok = True

        if not sponsor_ok:
            sponsor = aggregate_sponsor_stats(month_start_str, last_day_str)
            # 스폰 기록 0건도 정상이다
            sponsor_ok = True

        if poonggo is None:
            # 월말 스냅샷을 다시 만들려고 한 번 더 받는다(달마다 한 번)
            poonggo = _fetch_closed_month(month, roster)
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

    corrected_members = {}
    corrected_rows = 0
    for member in roster:
        if not member.modified_at:
            continue
        try:
            modified_date = date.fromisoformat(member.modified_at[:10]).isoformat()
        except ValueError:
            # 잘못된 표시는 어드민이 보게 지우지 않는다
            continue
        corrected_rows += apply_roster_backfill(member, modified_date)
        corrected_members[member.soop_id] = member.modified_at

    # 재수집 범위에는 늦게 등록·정정된 경기가, 소급 수정한 ELO ID는 그 날짜부터 바뀌므로 스폰 승패를 다시 센다.
    refresh_from = rescan_cutoff(today)
    backfill_dates = [d for d in (_marker_date(m.modified_at) for m in roster if m.modified_at) if d]
    if backfill_dates:
        refresh_from = min([refresh_from, *backfill_dates])
    sponsor_refresh = refresh_sponsor_stats(refresh_from, (today - timedelta(days=1)).isoformat())

    missing_applied = _apply_missing_to_published_days(today)

    # 소급 반영이 모두 성공한 뒤에만 표시를 지운다
    cleared = clear_modified_at(corrected_members) if corrected_members else 0

    # 경기 수집이 실패한 실행(ELOBOARD_OK=false)은 지난달 확정만 미룬다(확정하면 다시 보지 않는다).
    eloboard_ok = os.getenv("ELOBOARD_OK", "true").lower() != "false"
    if eloboard_ok:
        confirmations = _confirm_closed_months(today)
    else:
        confirmations = {"checked": 0, "confirmed": 0, "poonggo_rows": 0, "missing_month_end": []}

    return JobResult(
        records_read=len(roster) + len(poonggo),
        records_written=poonggo_written + daily_written + corrected_rows + confirmations["confirmed"],
        records_skipped=max(0, len(roster) - len(poonggo)),
        source_cursor=stat_date,
        metadata={
            "stat_date": stat_date,
            "month_start": month_start_str,
            "roster_count": len(roster),
            "monthly_source": _source_name(),
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
            "eloboard_ok": eloboard_ok,
            "missing_broadcasts_applied": missing_applied,
        },
    )
