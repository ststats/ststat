from models.sync_job import JobResult
from repositories.supabase import get_supabase

# 뒤 작업들이 쓰는 표·뷰. 하나라도 없으면 SQL(supabase/ststat.sql)이 안 돌았거나 옛 버전이다.
REQUIRED_TABLES = (
    "sync_jobs", "tier_members", "elo_players", "elo_matches", "elo_derived_snapshots",
    "elo_h2h_stats", "elo_player_ratings", "daily_member_stats", "poonggo_monthly_stats",
    "synergy_month_confirmations", "synergy_daily_dates", "videos", "video_channels",
)
# 최신 ststat.sql에서 생긴 함수. 호출이 되면 스키마가 최신이라는 뜻이다.
REQUIRED_RPCS = ("active_elo_snapshot_id",)


def run() -> JobResult:
    db = get_supabase()
    missing = []
    for table in REQUIRED_TABLES:
        try:
            db.table(table).select("*").limit(1).execute()
        except Exception as exc:
            missing.append(f"{table}: {exc}"[:200])
    for fn in REQUIRED_RPCS:
        try:
            db.rpc(fn, {}).execute()
        except Exception as exc:
            missing.append(f"rpc {fn}: {exc}"[:200])
    if missing:
        raise RuntimeError("Supabase schema check failed (run supabase/ststat.sql): " + " | ".join(missing))
    return JobResult(metadata={"message": "ststat Supabase schema OK",
                               "tables": len(REQUIRED_TABLES), "rpcs": len(REQUIRED_RPCS)})
