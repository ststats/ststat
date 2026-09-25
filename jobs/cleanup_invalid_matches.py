"""일회성: 예전 파서가 걸러내지 않고 저장한 잘못된 경기를 지운다(승자=패자, 선수 번호 없음·0 이하).

지우기 전에 원본을 sync_jobs(sync_eloboard_deleted_backup)에 남긴다. 대상이 비정상적으로 많으면 멈춘다.
"""
from __future__ import annotations

import os

from models.sync_job import JobResult
from repositories.derived_stats import load_matches
from repositories.eloboard import delete_match_ids, save_deletion_backup

MAX_DELETE = 500


def is_invalid(row: dict) -> bool:
    w, l = row.get("winner_elo_id"), row.get("loser_elo_id")
    return w is None or l is None or int(w) <= 0 or int(l) <= 0 or int(w) == int(l)


def run() -> JobResult:
    rows = load_matches()
    bad = [r for r in rows if is_invalid(r)]
    if len(bad) > MAX_DELETE:
        raise RuntimeError(f"Too many invalid matches ({len(bad)} > {MAX_DELETE}); refusing to delete")
    save_deletion_backup(bad, os.getenv("GITHUB_RUN_ID"))
    deleted = delete_match_ids({int(r["elo_match_id"]) for r in bad})
    return JobResult(
        records_read=len(rows),
        records_written=deleted,
        metadata={
            "deleted": deleted,
            "same_player": sum(1 for r in bad if r.get("winner_elo_id") is not None
                               and r.get("winner_elo_id") == r.get("loser_elo_id")),
            "missing_player": sum(1 for r in bad if r.get("winner_elo_id") is None or r.get("loser_elo_id") is None),
            "ids": sorted(int(r["elo_match_id"]) for r in bad),
        },
    )
