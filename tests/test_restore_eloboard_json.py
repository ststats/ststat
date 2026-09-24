from jobs.restore_eloboard_json import backup_matches


def test_backup_rows_become_matches_and_bad_rows_are_skipped():
    store = {
        "cats": ["sponsored", "pro_league"],
        "maps": {"299": "녹아웃"},
        "players": {"6536": ["승자", "Z"], "6482": ["패자", "T"]},
        "rows": [
            [2900448, "2026-09-22", 6536, 6482, 299, 0],
            [2900447, "2026-09-22", 6482, 6536, None, 1],
            [2112333, "2026-08-30", None, None, None, 0],   # 선수 없음 - 건너뛴다
        ],
    }
    matches, skipped = backup_matches(store)
    assert skipped == 1
    first = matches[0]
    assert (first.elo_match_id, first.winner.elo_id, first.loser.elo_id) == (2900448, 6536, 6482)
    assert first.winner.name == "승자" and first.winner.race == "Z"
    assert first.map_id == 299 and first.map_name == "녹아웃" and first.category == "sponsored"
    assert matches[1].map_id is None and matches[1].category == "pro_league"
