from __future__ import annotations

import datetime as dt
import os
import time

from collectors.eloboard import DEFAULT_DELAY, PAGE_STEP, fetch_page
from models.eloboard import EloMatch
from models.sync_job import JobResult
from repositories.eloboard import (
    delete_match_ids,
    get_latest_match_id,
    load_match_ids_between,
    load_match_rows,
    save_deletion_backup,
    load_previous_pending_deletes,
    stage_unknown_elo_candidates,
    upsert_dimensions,
    upsert_matches,
)

OVERLAP_DAYS = int(os.getenv("ELOBOARD_OVERLAP_DAYS", "3"))
# 목록이 날짜순이라 늦게 등록된 경기는 목록 깊숙이 있다. 그래서 이번 달 1일부터 매번 다시 읽고,
# 지난달 확정이 도는 월초 PREV_MONTH_DAYS일 동안은 지난달 1일부터 읽는다.
PREV_MONTH_DAYS = int(os.getenv("ELOBOARD_PREV_MONTH_DAYS", "7"))
KST = dt.timezone(dt.timedelta(hours=9))
MAX_PAGES = int(os.getenv("ELOBOARD_MAX_PAGES", "4000"))
MIN_VALID_RATIO = 0.95
# 훑은 범위의 이 비율보다 많이 사라지면 수집 착오로 보고 지우지 않는다.
MAX_DELETE_RATIO = float(os.getenv("ELOBOARD_MAX_DELETE_RATIO", "0.02"))
MAX_DELETE_MIN = int(os.getenv("ELOBOARD_MAX_DELETE_MIN", "50"))
# 다음 실행에 넘길 '이번에 안 보인 경기' 목록의 최대 길이(sync_jobs.metadata 크기 제한)
MAX_PENDING_DELETE = 5000


def rescan_cutoff(today: dt.date, overlap_days: int = OVERLAP_DAYS,
                  prev_month_days: int = PREV_MONTH_DAYS) -> str:
    """다시 읽을 가장 이른 경기 날짜: 이번 달 1일(월초엔 지난달 1일)과 최근 overlap_days 중 이른 쪽."""
    month_start = today.replace(day=1)
    if today.day <= prev_month_days:
        month_start = (month_start - dt.timedelta(days=1)).replace(day=1)
    return min(today - dt.timedelta(days=overlap_days), month_start).isoformat()


def run() -> JobResult:
    stop_at = get_latest_match_id()
    today_kst = dt.datetime.now(KST).date()
    cutoff = rescan_cutoff(today_kst) if stop_at else ""

    parsed: dict[int, EloMatch] = {}
    seen_ids: set[int] = set()
    min_seen: int | None = None
    first_page_checked = False
    reached = False
    finished = False
    raw_count = 0
    invalid_count = 0
    invalid_samples: list[dict] = []
    # 미래 날짜 경기는 랭킹 기준일을 앞으로 밀어 최근 가중치까지 바꾼다. 시차 여유로 하루만 허용.
    max_match_date = (today_kst + dt.timedelta(days=1)).isoformat()

    for page in range(MAX_PAGES):
        offset = page * PAGE_STEP
        batch = fetch_page(offset, delay=DEFAULT_DELAY)
        if not batch:
            finished = True
            break
        raw_count += len(batch)
        if not first_page_checked:
            ids = [int(r["id"]) for r in batch if isinstance(r, dict) and str(r.get("id") or "").isdigit()]
            if not ids:
                raise RuntimeError("EloBoard first page contains no valid match ids; refusing to write")
            first_page_checked = True

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
            # 목록은 날짜순이라 뒤 페이지에 ID가 더 큰 경기가 있다. ID로 자르지 않고 본 경기는 모두 센다.
            seen_ids.add(rid)
            min_seen = rid if min_seen is None else min(min_seen, rid)
            if stop_at and rid <= stop_at:
                reached = True
                if not date or date < cutoff:
                    continue
            match, reason = EloMatch.parse(row, max_date=max_match_date)
            if match is None:
                invalid_count += 1
                if len(invalid_samples) < 30:
                    invalid_samples.append({"id": rid, "reason": reason})
                continue
            parsed[match.elo_match_id] = match

        if reached and oldest and oldest < cutoff:
            finished = True
            break
        time.sleep(DEFAULT_DELAY)  # EloBoard 요청: 페이지 사이 최소 2초(collectors/eloboard.py MIN_DELAY)
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
    dimensions = upsert_dimensions(matches)
    upserted = upsert_matches(matches)
    candidates = stage_unknown_elo_candidates(matches)

    # 사라진 경기는 훑은 날짜 범위(cutoff ~ 오늘) 안에서만 판정한다(ID 범위로 고르면 훑지 않은 옛 경기까지 지운다).
    # offset 목록이라 한 번은 밀려 빠질 수 있어, 두 번 연속 안 보인 경기만 지운다.
    deleted = 0
    deleted_rows: list[dict] = []
    delete_skipped = None
    pending_delete: list[int] = []
    if cutoff:
        window_ids = load_match_ids_between(cutoff, today_kst.isoformat())
        gone = window_ids - seen_ids
        limit = max(MAX_DELETE_MIN, int(len(window_ids) * MAX_DELETE_RATIO))
        if len(gone) > limit:
            delete_skipped = f"too_many:{len(gone)}>{limit}"
        else:
            confirmed = gone & load_previous_pending_deletes()
            # 잘못 지웠을 때 되살릴 수 있게 원본 행을 남긴다
            deleted_rows = load_match_rows(confirmed)
            if len(deleted_rows) != len(confirmed):
                raise RuntimeError("Could not load every match to back up before deletion; refusing to delete")
            save_deletion_backup(deleted_rows, os.getenv("GITHUB_RUN_ID"))
            deleted = delete_match_ids(confirmed)
            pending_delete = sorted(gone - confirmed)[:MAX_PENDING_DELETE]

    return JobResult(
        records_read=raw_count,
        records_written=upserted + deleted + candidates,
        records_skipped=invalid_count,
        source_cursor=str(max(seen_ids) if seen_ids else stop_at),
        metadata={
            "previous_max_id": stop_at,
            "min_seen": min_seen,
            "overlap_days": OVERLAP_DAYS,
            "prev_month_days": PREV_MONTH_DAYS,
            "rescan_from": cutoff,
            "matches_upserted": upserted,
            "matches_deleted": deleted,
            "deleted_rows": deleted_rows,
            "invalid_samples": invalid_samples,
            "delete_skipped": delete_skipped,
            "pending_delete": pending_delete,
            "candidate_players_staged": candidates,
            "players_upserted": dimensions["players"],
            "maps_upserted": dimensions["maps"],
            "categories_added": dimensions["categories"],
            "valid_ratio": valid_ratio,
        },
    )
