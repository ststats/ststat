import importlib.util
from pathlib import Path


def _load_importer_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "import_synergy_archives.py"
    spec = importlib.util.spec_from_file_location("import_synergy_archives", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_merge_duplicate_archive_rows_preserves_split_metrics():
    mod = _load_importer_module()
    rows = [
        {
            "soop_id": "j4141h",
            "nickname": "요괴버스",
            "elo_id": None,
            "balloons": 12345,
            "broadcast_seconds": 2222,
            "cumulative_viewers": 3333,
            "sponsor_wins": 0,
            "sponsor_losses": 0,
            "tier": "",
        },
        {
            "soop_id": "j4141h",
            "nickname": "요괴버스",
            "elo_id": 999,
            "balloons": 0,
            "broadcast_seconds": 0,
            "cumulative_viewers": 0,
            "sponsor_wins": 2,
            "sponsor_losses": 7,
            "tier": "5",
        },
    ]

    merged = mod.merge_duplicate_archive_rows(rows)
    assert len(merged) == 1
    row = merged[0]
    assert row["balloons"] == 12345
    assert row["broadcast_seconds"] == 2222
    assert row["cumulative_viewers"] == 3333
    assert row["sponsor_wins"] == 2
    assert row["sponsor_losses"] == 7
    assert row["elo_id"] == 999
    assert row["tier"] == "5"


def test_merge_duplicate_archive_rows_does_not_sum_cumulative_values():
    mod = _load_importer_module()
    rows = [
        {"soop_id": "abc", "nickname": "A", "balloons": 100, "sponsor_wins": 3},
        {"soop_id": "abc", "nickname": "A", "balloons": 80, "sponsor_wins": 1},
    ]
    merged = mod.merge_duplicate_archive_rows(rows)
    assert merged[0]["balloons"] == 100
    assert merged[0]["sponsor_wins"] == 3
