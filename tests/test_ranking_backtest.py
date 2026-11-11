import datetime as dt
import json
import random
import sys

from processors import staruniv_ranking as model


def test_repeat_discount_counts_same_day_same_pair_in_either_order():
    rows = [[k, "2026-01-01", 1 if k % 2 else 2, 2 if k % 2 else 1, None, 0] for k in range(1, 12)]
    rows += [[100, "2026-01-02", 1, 2, None, 0], [101, "2026-01-01", 1, 3, None, 0]]
    mult = model.repeat_multipliers(rows)
    assert [mult.get(k, 1.0) for k in range(1, 12)] == [1, 1, 1, 1, 0.5, 0.5, 0.5, 0.5, 0.5, 0.25, 0.25]
    assert 100 not in mult and 101 not in mult  # 다른 날·다른 상대는 새로 센다


def test_row_weights_scale_pair_weights():
    rows = [[1, "2026-01-01", 1, 2, None, 0], [2, "2026-01-01", 1, 2, None, 0]]
    players = {"1": {"n": "a", "r": "T", "t": "1"}, "2": {"n": "b", "r": "Z", "t": "1"}}
    t_pos = {t: i for i, t in enumerate([*model.TIER_ORDER, model.UNRANKED])}
    args = (rows, ["solo_event"], dt.date(2026, 1, 1), players, {}, t_pos)
    full = model.build_pairs(*args)[2].sum()
    half = model.build_pairs(*args, row_weights={2: 0.5})[2].sum()
    assert abs(half - full * 0.75) < 1e-9


def test_backtest_runs_end_to_end(tmp_path, monkeypatch):
    import repositories.derived_stats as ds
    from scripts import ranking_backtest as bt

    rng = random.Random(7)
    skill = {i: (i - 20) * 0.08 for i in range(1, 41)}
    rows, start = [], dt.date(2024, 1, 1)
    for k in range(12000):
        a, b = rng.sample(list(skill), 2)
        win = a if rng.random() < 1 / (1 + 2.718 ** -(skill[a] - skill[b])) else b
        lose = b if win == a else a
        rows.append((k + 1, (start + dt.timedelta(days=rng.randrange(560))).isoformat(), win, lose, k % 2))
    tiers = ["갓", "킹", "잭", "조커", "스페이드", "0", "1", "2", "3", "4", "5", "6", "7", "8"]
    source = {
        "categories": [{"category_id": 0, "name": "sponsored"}, {"category_id": 1, "name": "solo_event"}],
        "maps": [],
        "players": [{"elo_id": i, "name": f"p{i}", "race": "TZP"[i % 3]} for i in skill],
        "matches": [
            {
                "elo_match_id": k,
                "match_date": d,
                "winner_elo_id": w,
                "loser_elo_id": lo,
                "map_id": None,
                "category_id": c,
            }
            for k, d, w, lo, c in rows
        ],
        "tier_members": [
            {
                "elo_id": i,
                "nickname": f"n{i}",
                "soop_id": f"s{i}",
                "tier": tiers[min(13, (40 - i) // 3)],
                "affiliation": "캄몬대",
                "race": "T",
            }
            for i in skill
        ],
    }
    monkeypatch.setattr(ds, "load_source_data", lambda: source)
    monkeypatch.setattr(
        sys, "argv", ["x", "--variant", "B", "--step", "60", "--start", "2025-01-01", "--out", str(tmp_path)]
    )
    bt.main()
    report = json.loads((tmp_path / "B.json").read_text(encoding="utf-8"))
    res = report["results"]
    assert res["model"]["n"] > 1000 and res["model"]["accuracy"] > 0.55
    assert {"model+race", "model:spon", "model:event", "tier_only"} <= res.keys()
