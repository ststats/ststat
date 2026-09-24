from __future__ import annotations

import datetime as dt
import os
import time

from collectors.eloboard import DEFAULT_DELAY, PAGE_LIMIT, PAGE_STEP, fetch_page
from models.eloboard import EloMatch
from models.sync_job import JobResult
from repositories.eloboard import (
    delete_match_ids,
    get_latest_match_id,
    load_match_ids_from,
    stage_unknown_elo_candidates,
    upsert_dimensions,
    upsert_matches,
)

OVERLAP_DAYS = int(os.getenv("ELOBOARD_OVERLAP_DAYS", "3"))
# 이미 받은 경기도 '지난달 1일'까지는 매번 다시 읽는다(달 수로 지정, 1 = 이번 달만).
# EloBoard는 누락됐던 과거 경기를 나중에 복구하거나 뒤늦게 등록하는 일이 있는데,
# 최근 3일만 다시 읽으면 그런 경기를 영영 못 받는다. 시너지 스폰판수가 이번 달
# 경기로 세므로 그대로 적게 나왔다(예: 먼진 EloBoard 30판+ vs 시너지 19판).
# 예전 시너지 수집기도 매번 그달 처음까지 거슬러 읽었다. 지난달까지 포함하는 건
# 월초의 '지난달 확정' 집계도 빠짐없이 하기 위해서다.
RESCAN_MONTHS = max(1, int(os.getenv("ELOBOARD_RESCAN_MONTHS", "2")))
KST = dt.timezone(dt.timedelta(hours=9))


def rescan_cutoff(today: dt.date, overlap_days: int = OVERLAP_DAYS,
                  rescan_months: int = RESCAN_MONTHS) -> str:
    """다시 읽을 가장 이른 경기 날짜: 최근 overlap_days와 (rescan_months-1)달 전 1일 중 이른 쪽."""
    year, month = today.year, today.month - (rescan_months - 1)
    while month < 1:
        year, month = year - 1, month + 12
    month_start = dt.date(year, month, 1)
    return min(today - dt.timedelta(days=overlap_days), month_start).isoformat()
MAX_PAGES = int(os.getenv("ELOBOARD_MAX_PAGES", "4000"))
MIN_VALID_RATIO = 0.95


def run() -> JobResult:
    stop_at = get_latest_match_id()
    today_kst = dt.datetime.now(KST).date()
    cutoff = rescan_cutoff(today_kst) if stop_at else ""

    parsed: dict[int, EloMatch] = {}
    seen_ids: set[int] = set()
    min_seen: int | None = None
    ceiling: int | None = None
    reached = False
    finished = False
    raw_count = 0
    invalid_count = 0

    for page in range(MAX_PAGES):
        offset = page * PAGE_STEP
        batch = fetch_page(offset, delay=DEFAULT_DELAY)
        if not batch:
            finished = True
            break
        raw_count += len(batch)
        if ceiling is None:
            ids = [int(r["id"]) for r in batch if isinstance(r, dict) and str(r.get("id") or "").isdigit()]
            if not ids:
                raise RuntimeError("EloBoard first page contains no valid match ids; refusing to write")
            ceiling = max(ids)

        oldest = ""
        for row in batch:
            if not isinstance(row, dict):
                invalid_count += 1
                continue
            try:
                rid = int(row.get("id"))
            except (TypeError, ValueError):
                invalid_count += 1
                continue
            date = str(row.get("played_on") or "")[:10]
            if date:
                oldest = date if not oldest else min(oldest, date)
            if ceiling is not None and rid > ceiling:
                continue
            seen_ids.add(rid)
            min_seen = rid if min_seen is None else min(min_seen, rid)
            if stop_at and rid <= stop_at:
                reached = True
                if not date or date < cutoff:
                    continue
            match = EloMatch.from_api(row)
            if match is None:
                invalid_count += 1
                continue
            parsed[match.elo_match_id] = match

        if reached and oldest and oldest < cutoff:
            finished = True
            break
        if DEFAULT_DELAY:
            time.sleep(DEFAULT_DELAY)
    else:
        raise RuntimeError(f"EloBoard collection hit max pages ({MAX_PAGES}); refusing partial write")

    if not finished:
        raise RuntimeError("EloBoard collection did not finish the incremental window; refusing partial write")
    if raw_count == 0:
        raise RuntimeError("EloBoard returned zero records; refusing to write")
    valid_ratio = (raw_count - invalid_count) / raw_count
    if valid_ratio < MIN_VALID_RATIO:
        raise RuntimeError(
            f"EloBoard parse validity {valid_ratio:.1%} below {MIN_VALID_RATIO:.0%}; refusing to write"
        )

    matches = list(parsed.values())
    # A normal incremental run can have zero changed parsed rows if nothing happened;
    # dimensions/matches are only written when we actually parsed the overlap/new window.
    dimensions = upsert_dimensions(matches)
    upserted = upsert_matches(matches)
    candidates = stage_unknown_elo_candidates(matches)

    deleted = 0
    if min_seen is not None:
        db_ids = load_match_ids_from(min_seen)
        gone = db_ids - seen_ids
        deleted = delete_match_ids(gone)

    return JobResult(
        records_read=raw_count,
        records_written=upserted + deleted + candidates,
        records_skipped=invalid_count,
        source_cursor=str(max(seen_ids) if seen_ids else stop_at),
        metadata={
            "previous_max_id": stop_at,
            "ceiling": ceiling,
            "min_seen": min_seen,
            "overlap_days": OVERLAP_DAYS,
            "rescan_months": RESCAN_MONTHS,
            "rescan_from": cutoff,
            "matches_upserted": upserted,
            "matches_deleted": deleted,
            "candidate_players_staged": candidates,
            "players_upserted": dimensions["players"],
            "maps_upserted": dimensions["maps"],
            "categories_added": dimensions["categories"],
            "valid_ratio": valid_ratio,
            "h2h_calculated": False,
            "rankings_calculated": False,
        },
    )
