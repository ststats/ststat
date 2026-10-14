from pathlib import Path
import yaml


def _ownership():
    return yaml.safe_load(Path("config/ownership.yml").read_text(encoding="utf-8"))


def test_ownership_rules_exist():
    data = _ownership()
    assert data["realtime"]["owner"] == "ststat"
    assert any("never overwrite realtime" in rule.lower() for rule in data["rules"])


def test_realtime_executor_points_at_existing_function():
    data = _ownership()
    assert data["realtime"]["executor"] == "supabase_edge_function"
    assert Path("supabase/functions/live-status/index.ts").exists()


def test_roster_sync_does_not_own_existing_member_columns():
    # jobs/sync_roster.py는 명단에 있는 선수를 고치지 않고 새 선수만 후보로 올린다
    assert _ownership()["roster"]["existing_tier_members_auto_columns"] == []


def test_pipeline_retries_idempotent_jobs_once_but_not_eloboard():
    from pathlib import Path
    wf = Path(".github/workflows/pipeline.yml").read_text(encoding="utf-8")
    for job in ("healthcheck", "calculate_eloboard_stats", "sync_synergy_daily", "sync_videos", "audit_match_rounds"):
        assert f"run: bash scripts/retry_job.sh {job}" in wf
    assert "run: python scripts/run_job.py sync_eloboard" in wf
    assert "retry_job.sh sync_eloboard" not in wf
    script = Path("scripts/retry_job.sh").read_text(encoding="utf-8")
    assert 'python scripts/run_job.py "$1" && exit 0' in script and "sleep 60" in script
