from unittest.mock import patch

from jobs import calculate_eloboard_stats


def test_cleanup_failure_does_not_mark_active_snapshot_failed():
    payload = {
        "ranking_meta": {"as_of": "2026-09-23"},
    }
    with (
            patch.object(calculate_eloboard_stats, "load_source_signature", return_value=None),
            patch.object(calculate_eloboard_stats, "load_source_data", return_value={"matches": [{}]}),
            patch.object(calculate_eloboard_stats, "load_active_history_cache", return_value=None),
        patch.object(calculate_eloboard_stats, "build_payload", return_value=payload),
        patch.object(calculate_eloboard_stats, "create_snapshot", return_value="snapshot"),
        patch.object(calculate_eloboard_stats, "write_snapshot", return_value={
            "player_stats": 1, "h2h": 1, "race": 1,
            "rankings": 1, "player_ratings": 1, "history": 1, "meta": 1,
        }),
        patch.object(calculate_eloboard_stats, "activate_snapshot") as activate,
        patch.object(calculate_eloboard_stats, "cleanup_old_snapshots", side_effect=RuntimeError("cleanup")),
        patch.object(calculate_eloboard_stats, "mark_failed") as mark_failed,
    ):
        result = calculate_eloboard_stats.run()

    activate.assert_called_once_with("snapshot")
    mark_failed.assert_not_called()
    assert result.metadata["cleanup_warning"] == "RuntimeError: cleanup"


def test_skips_when_source_and_code_unchanged():
    code = calculate_eloboard_stats._code_fingerprint()
    active = {"snapshot_id": "s1", "as_of": "2026-09-27", "metadata": {"source_signature": f"db1#{code}"}}
    with (
        patch.object(calculate_eloboard_stats, "cleanup_old_snapshots"),
        patch.object(calculate_eloboard_stats, "load_source_signature", return_value="db1"),
        patch.object(calculate_eloboard_stats, "load_active_snapshot", return_value=active),
        patch.object(calculate_eloboard_stats, "snapshot_has_data", return_value=True),
        patch.object(calculate_eloboard_stats, "load_source_data") as load,
    ):
        result = calculate_eloboard_stats.run()
    load.assert_not_called()
    assert result.metadata["skipped"] == "source unchanged"
    assert result.source_cursor == "2026-09-27"


def test_recalculates_when_active_snapshot_has_no_stats():
    """백업 복구 뒤: 스냅샷 목록(지문 포함)은 남고 통계 표는 비었다 - 지문이 같아도 다시 계산한다."""
    code = calculate_eloboard_stats._code_fingerprint()
    active = {"snapshot_id": "s1", "as_of": "2026-09-27", "metadata": {"source_signature": f"db1#{code}"}}
    payload = {"ranking_meta": {"as_of": "2026-09-27"}}
    counts = {"player_stats": 1, "h2h": 1, "race": 1, "rankings": 1, "player_ratings": 1, "history": 1, "meta": 1}
    with (
        patch.object(calculate_eloboard_stats, "cleanup_old_snapshots"),
        patch.object(calculate_eloboard_stats, "load_source_signature", return_value="db1"),
        patch.object(calculate_eloboard_stats, "load_active_snapshot", return_value=active),
        patch.object(calculate_eloboard_stats, "snapshot_has_data", return_value=False),
        patch.object(calculate_eloboard_stats, "load_source_data", return_value={"matches": [{}]}) as load,
        patch.object(calculate_eloboard_stats, "history_cache_metadata", return_value={}),
        patch.object(calculate_eloboard_stats, "load_active_history_cache", return_value=None),
        patch.object(calculate_eloboard_stats, "build_payload", return_value=payload),
        patch.object(calculate_eloboard_stats, "create_snapshot", return_value="s2"),
        patch.object(calculate_eloboard_stats, "write_snapshot", return_value=counts),
        patch.object(calculate_eloboard_stats, "activate_snapshot") as activate,
    ):
        result = calculate_eloboard_stats.run()
    load.assert_called_once()
    activate.assert_called_once_with("s2")
    assert "skipped" not in result.metadata


def test_recalculates_and_stores_signature_when_source_changed():
    active = {"snapshot_id": "s1", "as_of": "2026-09-27", "metadata": {"source_signature": "db0#old"}}
    payload = {"ranking_meta": {"as_of": "2026-09-27"}}
    counts = {"player_stats": 1, "h2h": 1, "race": 1, "rankings": 1, "player_ratings": 1, "history": 1, "meta": 1}
    with (
        patch.object(calculate_eloboard_stats, "cleanup_old_snapshots"),
        patch.object(calculate_eloboard_stats, "load_source_signature", return_value="db1"),
        patch.object(calculate_eloboard_stats, "load_active_snapshot", return_value=active),
        patch.object(calculate_eloboard_stats, "load_source_data", return_value={"matches": [{}]}),
        patch.object(calculate_eloboard_stats, "history_cache_metadata", return_value={}),
        patch.object(calculate_eloboard_stats, "load_active_history_cache", return_value=None),
        patch.object(calculate_eloboard_stats, "build_payload", return_value=payload),
        patch.object(calculate_eloboard_stats, "create_snapshot", return_value="s2") as create,
        patch.object(calculate_eloboard_stats, "write_snapshot", return_value=counts),
        patch.object(calculate_eloboard_stats, "activate_snapshot"),
    ):
        calculate_eloboard_stats.run()
    stored = create.call_args.args[2]["source_signature"]
    assert stored == f"db1#{calculate_eloboard_stats._code_fingerprint()}"
