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
# 이미 받은 경기도 이번 달 1일부터는 매번 다시 읽는다.
# EloBoard API는 경기를 '날짜 순서'로 준다. 며칠 늦게 등록된 경기는 ID는 크지만 날짜가
# 옛날이라 목록 깊숙이 있어서, 최근 3일만 다시 읽으면 영영 못 받았다(2026-09 실측:
# 9월 1,758판 중 197판 누락 · 먼진 EloBoard 32판 vs 시너지 19판). 예전 시너지 수집기도
# 매번 그달 처음까지 거슬러 읽었다.
# 월초 PREV_MONTH_DAYS일 동안만 지난달 1일부터 읽는다 - 지난달 확정 집계가 이 무렵에
# 돌기 때문이다. 그 뒤로는 이번 달만 읽어 수집 시간을 줄인다(한 달 ≈ 30페이지).
PREV_MONTH_DAYS = int(os.getenv("ELOBOARD_PREV_MONTH_DAYS", "7"))
KST = dt.timezone(dt.timedelta(hours=9))


def rescan_cutoff(today: dt.date, overlap_days: int = OVERLAP_DAYS,
                  prev_month_days: int = PREV_MONTH_DAYS) -> str:
    """다시 읽을 가장 이른 경기 날짜: 이번 달 1일(월초엔 지난달 1일)과 최근 overlap_days 중 이른 쪽."""
    month_start = today.replace(day=1)
    if today.day <= prev_month_days:
        month_start = (month_start - dt.timedelta(days=1)).replace(day=1)
    return min(today - dt.timedelta(days=overlap_days), month_start).isoformat()
MAX_PAGES = int(os.getenv("ELOBOARD_MAX_PAGES", "4000"))
MIN_VALID_RATIO = 0.95
# 전체 다시 받기: 1이면 이미 받은 경기도 처음부터 끝까지 다시 읽어 upsert하고, 지우지 않는다.
# 누락·삭제된 경기를 복구할 때 한 번만 쓴다(.github/workflows/backfill-eloboard.yml).
FULL_BACKFILL = os.getenv("ELOBOARD_BACKFILL", "") == "1"
# 한 번에 이만큼보다 많이(훑은 범위 경기의 비율) 사라졌다고 나오면 지우지 않는다.
# EloBoard가 실제로 경기를 지우는 건 드물다 - 대량 삭제는 수집 쪽 착오일 가능성이 크다.
MAX_DELETE_RATIO = float(os.getenv("ELOBOARD_MAX_DELETE_RATIO", "0.02"))
MAX_DELETE_MIN = int(os.getenv("ELOBOARD_MAX_DELETE_MIN", "50"))
# 다음 실행에 넘길 '이번에 안 보인 경기' 목록의 최대 길이(sync_jobs.metadata 크기 제한)
MAX_PENDING_DELETE = 5000


def run() -> JobResult:
    stop_at = get_latest_match_id()
    today_kst = dt.datetime.now(KST).date()
    cutoff = rescan_cutoff(today_kst) if stop_at and not FULL_BACKFILL else ""

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
            # 목록은 경기 날짜순이라 ID 순서와 다르다: 지난 날짜로 늦게 등록된 경기는 뒤 페이지에
            # 있으면서 ID가 더 크다. 예전엔 첫 페이지 최대 ID보다 큰 ID를 건너뛰어 그런 경기를
            # 저장하지 않았고, 삭제 판정에서는 '사라진 경기'로 보기까지 했다. 본 경기는 모두 센다.
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
    # A normal incremental run can have zero changed parsed rows if nothing happened;
    # dimensions/matches are only written when we actually parsed the overlap/new window.
    dimensions = upsert_dimensions(matches)
    upserted = upsert_matches(matches)
    candidates = stage_unknown_elo_candidates(matches)

    # EloBoard에서 사라진 경기 지우기. 이번에 끝까지 훑은 '날짜 범위'(cutoff ~ 오늘) 안에서만
    # 판정한다. ID 범위(min_seen 이상)로 고르면 날짜 순서 목록에서 훑지 않은 옛 경기까지
    # 지운다. 전체 다시 받기 때와 삭제 후보가 비정상적으로 많을 때는 지우지 않는다.
    # 목록을 위치(offset)로 넘기므로 수집 도중 경기가 끼어들면 한두 건이 한 번 안 보일 수 있다 -
    # 지난번 실행에서도 안 보였던 경기(두 번 연속)만 지우고, 이번에 처음 안 보인 것은 다음으로 넘긴다.
    deleted = 0
    deleted_rows: list[dict] = []
    delete_skipped = None
    pending_delete: list[int] = []
    if FULL_BACKFILL:
        delete_skipped = "full_backfill"
    elif cutoff:
        window_ids = load_match_ids_between(cutoff, today_kst.isoformat())
        gone = window_ids - seen_ids
        limit = max(MAX_DELETE_MIN, int(len(window_ids) * MAX_DELETE_RATIO))
        if len(gone) > limit:
            delete_skipped = f"too_many:{len(gone)}>{limit}"
        else:
            confirmed = gone & load_previous_pending_deletes()
            # 지우기 전에 원본 행을 작업 기록에 남긴다(잘못 지웠을 때 그대로 되살릴 수 있게)
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
            "full_backfill": FULL_BACKFILL,
            "candidate_players_staged": candidates,
            "players_upserted": dimensions["players"],
            "maps_upserted": dimensions["maps"],
            "categories_added": dimensions["categories"],
            "valid_ratio": valid_ratio,
            "h2h_calculated": False,
            "rankings_calculated": False,
        },
    )
