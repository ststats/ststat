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
