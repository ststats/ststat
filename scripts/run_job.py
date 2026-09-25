import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from repositories.supabase import get_supabase


# 가장 긴 작업 제한 시간(90분)보다 넉넉히 길게. 이보다 오래 'running'인 행은
# 러너가 취소·강제 종료돼 끝을 기록하지 못한 것이다.
STALE_RUNNING_AFTER = timedelta(hours=3)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def close_stale_runs(db, job_name: str):
    """끝을 기록하지 못한 채 남은 같은 작업의 'running' 행을 실패로 닫는다."""
    cutoff = (datetime.now(timezone.utc) - STALE_RUNNING_AFTER).isoformat()
    try:
        (
            db.table("sync_jobs")
            .update({
                "status": "failed",
                "finished_at": utc_now(),
                "error_message": "러너가 끝을 기록하지 못했습니다(취소 또는 강제 종료).",
            })
            .eq("job_name", job_name)
            .eq("status", "running")
            .lt("started_at", cutoff)
            .execute()
        )
    except Exception as exc:  # 정리 실패로 본 작업을 막지 않는다
        print(f"stale sync_jobs cleanup skipped: {exc}", file=sys.stderr)


def record(db, sync_job_id, fields: dict, attempts: int = 3) -> bool:
    """sync_jobs 기록. 일시 오류는 몇 번 다시 시도하고, 끝내 실패하면 False(예외를 내지 않는다)."""
    for attempt in range(attempts):
        try:
            db.table("sync_jobs").update(fields).eq("id", sync_job_id).execute()
            return True
        except Exception as exc:
            print(f"sync_jobs record failed ({attempt + 1}/{attempts}): {exc}", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    return False


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/run_job.py <job_name>")

    job_name = sys.argv[1]
    run_id = os.getenv("GITHUB_RUN_ID") or str(uuid.uuid4())

    db = get_supabase()
    close_stale_runs(db, job_name)

    inserted = (
        db.table("sync_jobs")
        .insert({
            "job_name": job_name,
            "run_id": run_id,
            "status": "running",
        })
        .execute()
    )

    sync_job_id = inserted.data[0]["id"]

    # 작업 실행과 기록을 나눈다: 게시까지 끝난 작업이 '성공 기록' 실패 때문에 실패로 남지 않게,
    # 실패 기록이 또 실패해도 원래 오류가 가려지지 않게 한다.
    try:
        module = import_module(f"jobs.{job_name}")
        result = module.run()
    except Exception as exc:
        record(db, sync_job_id, {
            "status": "failed",
            "finished_at": utc_now(),
            "error_message": str(exc)[:5000],
        })
        raise

    ok = record(db, sync_job_id, {
        "status": "success",
        "finished_at": utc_now(),
        "records_read": result.records_read,
        "records_written": result.records_written,
        "records_skipped": result.records_skipped,
        "source_cursor": result.source_cursor,
        "metadata": result.metadata,
    })
    if not ok:
        # 데이터는 이미 반영됐다. 행은 'running'으로 남고 다음 실행에서 3시간 뒤 실패로 닫힌다.
        # (sync_eloboard의 pending_delete도 이 기록에 실리므로, 이 경우 삭제 판정이 한 번 늦어질 뿐이다)
        print(f"::warning::{job_name} finished but its success record could not be saved", file=sys.stderr)


if __name__ == "__main__":
    main()