from __future__ import annotations

import datetime as dt
import numpy as np

from processors.eloboard_derived import history_cache_metadata
from processors import staruniv_ranking
from processors.staruniv_ranking import (
    TIER_ORDER,
    UNRANKED,
    build_pairs,
    fit,
    infer_data_tier,
    monotone_tier_levels,
)


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


def test_history_cache_changes_for_every_input_that_moves_closed_months():
    """지난 달 레이팅을 바꾸는 입력(경기·티어·승급일·경기 분류·선수 종족)이 바뀌면 지문도 바뀐다."""
    def fp(mutate=None):
        src = _source()
        src["players"] = [{"elo_id": 1, "race": "T"}, {"elo_id": 2, "race": "Z"}]
        src["categories"] = [{"category_id": 4, "name": "리그"}]
        if mutate:
            mutate(src)
        return history_cache_metadata(src)["closed_history_fingerprint"]

    base = fp()
    changes = {
        "race": lambda s: s["players"][1].update(race="P"),
        "tier": lambda s: s["tier_members"][0].update(tier="2"),
        "promotion": lambda s: s["tier_members"][0].update(promoted_tier_1="2026-01-01"),
        "category": lambda s: s["categories"][0].update(name="미니"),
        "closed_match": lambda s: s["matches"][0].update(map_id=9),
    }
    for name, mutate in changes.items():
        assert fp(mutate) != base, name
    # 같은 입력은 순서가 달라도 같은 지문
    assert fp(lambda s: s["players"].reverse()) == base


def test_monotone_levels_pool_inverted_tier_centers():
    levels = monotone_tier_levels([3.0, 2.0, 2.5, 1.0], [10, 10, 10, 10])
    assert np.all(levels[:-1] >= levels[1:])
    assert levels.tolist() == [3.0, 2.25, 2.25, 1.0]


def test_data_tier_requires_confident_boundary_crossing_and_caps_jump():
    levels = np.asarray([3.0, 2.0, 1.0, 0.0])

    assert infer_data_tier(2, theta=2.8, standard_error=0.1, levels=levels) == 0
    assert infer_data_tier(2, theta=2.8, standard_error=1.0, levels=levels) == 2
    assert infer_data_tier(0, theta=-2.0, standard_error=0.1, levels=levels) == 2


def test_fit_enforces_tier_order_even_when_results_push_toward_inversion():
    # 약한 티어(1)가 강한 티어(0)를 계속 이기는 극단적 입력에서도 기준선은 역전되지 않는다.
    wi = np.asarray([1], dtype=np.int64)
    li = np.asarray([0], dtype=np.int64)
    ww = np.asarray([100.0])
    win_tier = np.asarray([1], dtype=np.int64)
    lose_tier = np.asarray([0], dtype=np.int64)
    lam = np.asarray([100.0, 100.0])
    levels, _delta, _race = fit(
        wi, li, ww, win_tier, lose_tier, lam, 2, len(TIER_ORDER) + 1)

    assert np.all(levels[:len(TIER_ORDER) - 1] >= levels[1:len(TIER_ORDER)])


def test_build_pairs_uses_tier_at_match_date_for_promoted_player():
    players = {'1': {'t': '1'}, '2': {'t': '1'}}
    ladders = {'1': [('2026-01-01', '2'), ('2026-07-01', '1')]}
    tiers = TIER_ORDER + [UNRANKED]
    t_pos = {tier: idx for idx, tier in enumerate(tiers)}
    rows = [
        [1, '2026-06-01', 1, 2, 0, 0],
        [2, '2026-08-01', 1, 2, 0, 0],
    ]
    result = build_pairs(rows, ['solo_event'], dt.date(2026, 8, 1), players, ladders, t_pos)
    win_tiers, lose_tiers = result[4], result[5]

    assert set(win_tiers.tolist()) == {t_pos['2'], t_pos['1']}
    assert set(lose_tiers.tolist()) == {t_pos['1']}


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
