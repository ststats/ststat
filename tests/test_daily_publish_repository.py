from unittest.mock import MagicMock, patch

import pytest

from repositories.synergy_stats import upsert_daily_snapshot


def test_daily_snapshot_is_published_with_one_rpc():
    db = MagicMock()
    rows = [{"stat_date": "2026-09-23", "soop_id": "a"}]
    with patch("repositories.synergy_stats.get_supabase", return_value=db):
        assert upsert_daily_snapshot("2026-09-23", rows) == 1
    db.rpc.assert_called_once_with(
        "publish_daily_member_stats",
        {"p_stat_date": "2026-09-23", "p_rows": rows},
    )


def test_daily_snapshot_rejects_duplicate_member_ids_before_database_call():
    rows = [
        {"stat_date": "2026-09-23", "soop_id": "a"},
        {"stat_date": "2026-09-23", "soop_id": "a"},
    ]
    with pytest.raises(RuntimeError, match="duplicate"):
        upsert_daily_snapshot("2026-09-23", rows)
