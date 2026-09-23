from models.sync_job import JobResult
from repositories.supabase import get_supabase


def run() -> JobResult:
    db = get_supabase()
    db.table("sync_jobs").select("id").limit(1).execute()
    return JobResult(metadata={"message": "ststat Supabase connection OK"})
