from pathlib import Path

from models.sync_job import JobResult
from repositories.schema_stamp import applied_stamp, read_stamp

SQL = Path(__file__).resolve().parents[1] / "supabase" / "ststat.sql"


def run() -> JobResult:
    # DB 스키마 도장이 이 저장소 ststat.sql과 다르면 SQL이 안 돌았거나 최신이 아니다. 뒤 작업을 돌리지 않는다.
    name, stamp, _ = read_stamp(SQL)
    applied = applied_stamp(name)
    if applied != stamp:
        raise RuntimeError(f"Supabase schema check failed (run supabase/ststat.sql): DB {applied} != file {stamp}")
    return JobResult(metadata={"message": "ststat Supabase schema OK", "stamp": stamp})
