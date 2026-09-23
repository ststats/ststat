import os
import sys
import uuid
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from repositories.supabase import get_supabase


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/run_job.py <job_name>")

    job_name = sys.argv[1]
    run_id = os.getenv("GITHUB_RUN_ID") or str(uuid.uuid4())

    db = get_supabase()

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

    try:
        module = import_module(f"jobs.{job_name}")
        result = module.run()

        db.table("sync_jobs").update({
            "status": "success",
            "finished_at": utc_now(),
            "records_read": result.records_read,
            "records_written": result.records_written,
            "records_skipped": result.records_skipped,
            "source_cursor": result.source_cursor,
            "metadata": result.metadata,
        }).eq("id", sync_job_id).execute()

    except Exception as exc:
        db.table("sync_jobs").update({
            "status": "failed",
            "finished_at": utc_now(),
            "error_message": str(exc)[:5000],
        }).eq("id", sync_job_id).execute()

        raise


if __name__ == "__main__":
    main()