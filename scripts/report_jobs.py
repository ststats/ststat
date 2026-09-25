"""최근 작업 결과를 Actions 로그로 출력한다(읽기만 함). 복구·점검 결과를 DB를 열지 않고 확인하는 용도.

사용: python scripts/report_jobs.py [job_name ...]   (기본: 전체 복구·재계산·삭제 백업·정기 작업)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repositories.supabase import get_supabase  # noqa: E402

DEFAULT_JOBS = [
    "sync_eloboard", "refresh_synergy_sponsor", "sync_eloboard_deleted_backup",
    "sync_synergy_daily", "calculate_eloboard_stats", "sync_videos", "healthcheck",
]
# 너무 긴 값(삭제 원본 전체 등)은 개수만 보여 준다
LIST_PREVIEW = 20


def shorten(value):
    if isinstance(value, list) and len(value) > LIST_PREVIEW:
        return {"count": len(value), "first": value[:LIST_PREVIEW]}
    if isinstance(value, dict):
        return {k: shorten(v) for k, v in value.items()}
    return value


def main():
    jobs = sys.argv[1:] or DEFAULT_JOBS
    db = get_supabase()
    for job in jobs:
        rows = (
            db.table("sync_jobs")
            .select("id,job_name,run_id,status,started_at,finished_at,records_read,records_written,"
                    "records_skipped,error_message,metadata")
            .eq("job_name", job)
            .order("started_at", desc=True)
            .limit(3)
            .execute()
            .data
            or []
        )
        print(f"===== {job} (최근 {len(rows)}건) =====")
        for row in rows:
            row["metadata"] = shorten(row.get("metadata") or {})
            print(json.dumps(row, ensure_ascii=False, default=str))
    # 경기 수와 날짜 범위(복구 전후 비교용)
    first = db.table("elo_matches").select("elo_match_id", count="exact").limit(1).execute()
    print(f"===== elo_matches 총 {first.count}건 =====")


if __name__ == "__main__":
    main()
