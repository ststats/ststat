from __future__ import annotations

import datetime as dt
import os
import time

from collectors.eloboard import DEFAULT_DELAY, PAGE_STEP, fetch_page
from collectors.eloboard_html import collect
from models.eloboard import EloMatch
from models.sync_job import JobResult
from repositories.eloboard import (
    delete_match_ids,
    get_latest_match_id,
    load_match_ids_between,
    load_match_rows,
    save_deletion_backup,
    load_previous_metadata,
    load_previous_pending_deletes,
    stage_unknown_elo_candidates,
    upsert_dimensions,
    upsert_matches,
)

# html: 공개 페이지에서 읽는다(collectors/eloboard_html.py). api: 2026-11-06부터 EloBoard가 /api/를 막았다
SOURCE = os.getenv("ELOBOARD_SOURCE", "html")
# EloBoard 전적 목록(/matches 탭)이 보여 주는 최근 날 수
LIST_DAYS = 30
OVERLAP_DAYS = int(os.getenv("ELOBOARD_OVERLAP_DAYS", "3"))
# API 방식(run_api): 목록이 날짜순이라 늦게 등록된 경기는 목록 깊숙이 있다. 그래서 이번 달 1일부터 매번 다시 읽고,
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


def rescan_cutoff(today: dt.date, overlap_days: int = OVERLAP_DAYS, prev_month_days: int = PREV_MONTH_DAYS) -> str:
    """다시 읽을 가장 이른 경기 날짜: 이번 달 1일(월초엔 지난달 1일)과 최근 overlap_days 중 이른 쪽."""
    month_start = today.replace(day=1)
    if today.day <= prev_month_days:
        month_start = (month_start - dt.timedelta(days=1)).replace(day=1)
    return min(today - dt.timedelta(days=overlap_days), month_start).isoformat()


def run() -> JobResult:
    return run_html() if SOURCE == "html" else run_api()


def run_api() -> JobResult:
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
    valid_ratio = _check_validity(raw_count, invalid_count)
    saved = _save(list(parsed.values()))
    deletion = _delete_missing(cutoff, today_kst.isoformat(), seen_ids) if cutoff else _no_deletion([], None)

    return JobResult(
        records_read=raw_count,
        records_written=saved["matches_upserted"] + deletion["matches_deleted"] + saved["candidate_players_staged"],
        records_skipped=invalid_count,
        source_cursor=str(max(seen_ids) if seen_ids else stop_at),
        metadata={
            "previous_max_id": stop_at,
            "min_seen": min_seen,
            "overlap_days": OVERLAP_DAYS,
            "prev_month_days": PREV_MONTH_DAYS,
            "rescan_from": cutoff,
            **saved,
            **deletion,
            "invalid_samples": invalid_samples,
            "valid_ratio": valid_ratio,
        },
    )


def run_html() -> JobResult:
    stop_at = get_latest_match_id()
    today_kst = dt.datetime.now(KST).date()
    previous = load_previous_metadata()
    # 목록이 보여 주는 최근 LIST_DAYS일을 다 다시 읽기(약 280번 요청, 10분)는 하루 첫 실행만 한다.
    # 그 밖엔 최근 OVERLAP_DAYS일만(약 45번). 경계 날은 시각에 따라 일부만 보여 하루 안쪽부터 읽는다
    deep = previous.get("deep_on") != today_kst.isoformat()
    days = LIST_DAYS - 1 if deep else OVERLAP_DAYS
    cutoff = (today_kst - dt.timedelta(days=days)).isoformat()
    got = collect(cutoff, listed_from=(today_kst - dt.timedelta(days=LIST_DAYS)).isoformat())

    parsed: dict[int, EloMatch] = {}
    invalid_count = 0
    invalid_samples: list[dict] = []
    max_match_date = (today_kst + dt.timedelta(days=1)).isoformat()
    for row in got.rows.values():
        match, reason = EloMatch.parse(row, max_date=max_match_date)
        if match is None:
            invalid_count += 1
            if len(invalid_samples) < 30:
                invalid_samples.append({"id": row.get("id"), "reason": reason})
            continue
        parsed[match.elo_match_id] = match

    raw_count = len(got.rows)
    valid_ratio = _check_validity(raw_count, invalid_count)
    saved = _save(list(parsed.values()))
    seen_ids = set(got.rows)
    # 사라진 경기는 기간 처음까지 다 읽은 다시 읽기에서만 판정한다. 그 밖엔 앞 판정의 '안 보인 경기'를 그대로 넘긴다
    if deep and got.complete:
        deletion = _delete_missing(cutoff, today_kst.isoformat(), seen_ids)
    else:
        carried = sorted(load_previous_pending_deletes())[:MAX_PENDING_DELETE]
        deletion = _no_deletion(carried, "incomplete" if deep else "recent_only")

    return JobResult(
        records_read=raw_count,
        records_written=saved["matches_upserted"] + deletion["matches_deleted"] + saved["candidate_players_staged"],
        records_skipped=invalid_count,
        source_cursor=str(max(seen_ids) if seen_ids else stop_at),
        metadata={
            "source": "html",
            "previous_max_id": stop_at,
            "deep": deep,
            "deep_on": today_kst.isoformat() if deep else previous.get("deep_on"),
            "rescan_from": cutoff,
            "requests": got.requests,
            "refetched": got.refetched,
            "complete": got.complete,
            "notes": got.notes[:30],
            "unsupported_skipped": got.skipped,
            **saved,
            **deletion,
            "invalid_samples": invalid_samples,
            "valid_ratio": valid_ratio,
        },
    )


def _check_validity(raw_count: int, invalid_count: int) -> float:
    if raw_count == 0:
        raise RuntimeError("EloBoard returned zero records; refusing to write")
    valid_ratio = (raw_count - invalid_count) / raw_count
    if valid_ratio < MIN_VALID_RATIO:
        raise RuntimeError(f"EloBoard parse validity {valid_ratio:.1%} below {MIN_VALID_RATIO:.0%}; refusing to write")
    return valid_ratio


def _save(matches: list[EloMatch]) -> dict:
    dimensions = upsert_dimensions(matches)
    upserted = upsert_matches(matches)
    candidates = stage_unknown_elo_candidates(matches)
    return {
        "matches_upserted": upserted,
        "candidate_players_staged": candidates,
        "players_upserted": dimensions["players"],
        "maps_upserted": dimensions["maps"],
        "categories_added": dimensions["categories"],
    }


def _no_deletion(pending_delete: list[int], skipped: str | None) -> dict:
    return {"matches_deleted": 0, "deleted_rows": [], "delete_skipped": skipped, "pending_delete": pending_delete}


def _delete_missing(cutoff: str, today: str, seen_ids: set[int]) -> dict:
    """사라진 경기는 훑은 날짜 범위(cutoff ~ 오늘) 안에서만 판정한다(ID 범위로 고르면 훑지 않은 옛 경기까지 지운다).
    한 번은 밀려 빠질 수 있어, 두 번 연속 안 보인 경기만 지운다."""
    window_ids = load_match_ids_between(cutoff, today)
    gone = window_ids - seen_ids
    limit = max(MAX_DELETE_MIN, int(len(window_ids) * MAX_DELETE_RATIO))
    if len(gone) > limit:
        return _no_deletion([], f"too_many:{len(gone)}>{limit}")
    confirmed = gone & load_previous_pending_deletes()
    # 잘못 지웠을 때 되살릴 수 있게 원본 행을 남긴다
    deleted_rows = load_match_rows(confirmed)
    if len(deleted_rows) != len(confirmed):
        raise RuntimeError("Could not load every match to back up before deletion; refusing to delete")
    save_deletion_backup(deleted_rows, os.getenv("GITHUB_RUN_ID"))
    return {
        "matches_deleted": delete_match_ids(confirmed),
        "deleted_rows": deleted_rows,
        "delete_skipped": None,
        "pending_delete": sorted(gone - confirmed)[:MAX_PENDING_DELETE],
    }
