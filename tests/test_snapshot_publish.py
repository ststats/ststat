from unittest.mock import patch

from jobs import calculate_eloboard_stats


def test_cleanup_failure_does_not_mark_active_snapshot_failed():
    payload = {
        "ranking_meta": {"as_of": "2026-09-23"},
    }
    with (
        patch.object(calculate_eloboard_stats, "load_source_data", return_value={"matches": [{}]}),
        patch.object(calculate_eloboard_stats, "build_payload", return_value=payload),
        patch.object(calculate_eloboard_stats, "create_snapshot", return_value="snapshot"),
        patch.object(calculate_eloboard_stats, "write_snapshot", return_value={
            "player_stats": 1, "h2h": 1, "race": 1,
            "rankings": 1, "history": 1, "meta": 1,
        }),
        patch.object(calculate_eloboard_stats, "activate_snapshot") as activate,
        patch.object(calculate_eloboard_stats, "cleanup_old_snapshots", side_effect=RuntimeError("cleanup")),
        patch.object(calculate_eloboard_stats, "mark_failed") as mark_failed,
    ):
        result = calculate_eloboard_stats.run()

    activate.assert_called_once_with("snapshot")
    mark_failed.assert_not_called()
    assert result.metadata["cleanup_warning"] == "RuntimeError: cleanup"
