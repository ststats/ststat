"""지난 방송통계의 스폰 승패를 지금 elo_matches 기준으로 다시 맞추는 복구 작업.

평소에는 sync_synergy_daily가 EloBoard 재수집 범위만 다시 맞춘다. 전체 경기 복구(backfill)
뒤나 오래된 달을 정정할 때 이 작업으로 원하는 기간을 다시 센다(바뀐 날만 다시 게시).
  SYNERGY_REFRESH_FROM  시작 날짜(기본: 처음부터)
  SYNERGY_REFRESH_TO    끝 날짜(기본: 오늘, 한국 시간)
"""
from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from models.sync_job import JobResult
from repositories.synergy_stats import refresh_sponsor_stats


def run() -> JobResult:
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    start = os.getenv("SYNERGY_REFRESH_FROM", "").strip() or "2000-01-01"
    end = os.getenv("SYNERGY_REFRESH_TO", "").strip() or today
    date.fromisoformat(start)
    date.fromisoformat(end)
    result = refresh_sponsor_stats(start, end)
    return JobResult(
        records_read=result["days_checked"],
        records_written=result["rows_changed"],
        metadata={"from": start, "to": end, **result},
    )
