"""EloBoard 수집을 오늘(KST) 이미 성공했는지 보고 GitHub Actions 출력 due=true|false를 쓴다.

Pipeline은 4시간마다 돌지만 EloBoard 수집은 하루 한 번만 한다(KST 자정 뒤 첫 실행).
실패한 날은 성공 기록이 없으니 다음 실행이 다시 시도한다. DB를 못 읽으면 수집한다(멈추기보다 낫다).
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

KST = timezone(timedelta(hours=9))
JOB_NAME = "sync_eloboard"


def kst_day_start(now: datetime) -> datetime:
    """now가 속한 KST 날짜의 0시(UTC 시각으로)."""
    local = now.astimezone(KST)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def is_due(now: datetime, db) -> bool:
    since = kst_day_start(now).isoformat()
    rows = (
        db.table("sync_jobs")
        .select("id")
        .eq("job_name", JOB_NAME)
        .eq("status", "success")
        .gte("started_at", since)
        .limit(1)
        .execute()
    ).data
    return not rows


def main() -> None:
    try:
        from repositories.supabase import get_supabase

        due = is_due(datetime.now(UTC), get_supabase())
    except Exception as exc:  # 확인 실패로 수집을 막지 않는다
        print(f"::warning::EloBoard 오늘 수집 여부를 확인하지 못해 수집합니다: {exc}", file=sys.stderr)
        due = True
    print(f"EloBoard sync due: {due}")
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"due={'true' if due else 'false'}\n")


if __name__ == "__main__":
    main()
