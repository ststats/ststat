from __future__ import annotations

import datetime as dt
import numpy as np

from processors.eloboard_derived import history_cache_metadata
from processors import staruniv_ranking
from processors.staruniv_ranking import infer_data_tier, monotone_tier_levels


def _source(current_winner=1, closed_winner=1):
    return {
        "matches": [
            {
                "elo_match_id": 1,
                "match_date": "2026-08-31",
                "winner_elo_id": closed_winner,
                "loser_elo_id": 2,
                "map_id": 3,
                "category_id": 4,
            },
            {
                "elo_match_id": 2,
                "match_date": "2026-09-20",
                "winner_elo_id": current_winner,
                "loser_elo_id": 2,
                "map_id": 3,
                "category_id": 4,
            },
        ],
        "tier_members": [
            {"elo_id": 1, "tier": "1", **{f"promoted_tier_{n}": None for n in range(9)}}
        ],
    }


def test_history_cache_ignores_current_month_matches_but_not_closed_months():
    original = history_cache_metadata(_source())
    current_changed = history_cache_metadata(_source(current_winner=9))
    closed_changed = history_cache_metadata(_source(closed_winner=9))

    assert original["closed_history_fingerprint"] == current_changed["closed_history_fingerprint"]
    assert original["closed_history_fingerprint"] != closed_changed["closed_history_fingerprint"]


def test_monotone_levels_pool_inverted_tier_centers():
    levels = monotone_tier_levels([3.0, 2.0, 2.5, 1.0], [10, 10, 10, 10])
    assert np.all(levels[:-1] >= levels[1:])
    assert levels.tolist() == [3.0, 2.25, 2.25, 1.0]


def test_data_tier_requires_confident_boundary_crossing_and_caps_jump():
    levels = np.asarray([3.0, 2.0, 1.0, 0.0])

    assert infer_data_tier(2, theta=2.8, standard_error=0.1, levels=levels) == 0
    assert infer_data_tier(2, theta=2.8, standard_error=1.0, levels=levels) == 2
    assert infer_data_tier(0, theta=-2.0, standard_error=0.1, levels=levels) == 2


def test_closed_history_reuses_cache_and_only_solves_current_month(monkeypatch):
    last_day = dt.date(2026, 9, 22)
    months = [d.strftime("%Y-%m") for d in staruniv_ranking.month_ends(last_day, 18)]
    cached = {"months": months, "players": {"1": list(range(18))}}
    calls = []

    def fake_solve(*args, **kwargs):
        calls.append(args[2])
        return {"1": 123.0}

    monkeypatch.setattr(staruniv_ranking, "solve_at", fake_solve)
    rows = [[i, "2026-09-22", 1, 2, 0, 0] for i in range(100)]
    keys, players = staruniv_ranking.build_history(
        rows, ["sponsored"], {}, {}, 0, last_day, {}, cached, {"1": 123.0}
    )

    assert keys == months
    assert calls == []
    assert players["1"][-1] == 123.0
