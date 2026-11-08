"""엔트리 예상 승률 전용 모델. 랭킹 점수·월별 이력은 건드리지 않는다(staruniv_ranking과 별도).

선수 실력 + 선수별 종족전 성향(세 값 평균 0) + 맵별 종족 상성을 함께 맞춘다.
prior 폭(MATCHUP_SIGMA·MAP_RACE_SIGMA)은 시간순 재현으로 골랐고, 상대전적·개인 맵 승률은 따로 더하지 않는다.
"""

from __future__ import annotations
import datetime as dt
import numpy as np
from scipy.optimize import minimize
from scipy.sparse import coo_matrix
from scipy.special import expit
from processors import staruniv_ranking as model

MODEL_VERSION = "joint-map-v1"
MATCHUP_SIGMA = 0.25
MAP_RACE_SIGMA = 0.30


def fit_prediction(store, tier_rows, as_of):
    rows = [r for r in store["rows"] if str(r[1]) <= as_of.isoformat()]
    index = model.build_index(dict(store, rows=rows), tier_rows, include_unlisted=True)
    players = index["players"]
    ladders = model.load_ladders(tier_rows, players)
    for pid, p in players.items():
        if model.tier_of(p) != model.UNRANKED:
            p["t"] = model.tier_at(pid, as_of, ladders, players)
    tpos = {t: i for i, t in enumerate(model.TIER_ORDER + [model.UNRANKED])}
    wi, li, ww, wwt, wt, lt, order, ws, wst, _ = model.build_pairs(rows, store["cats"], as_of, players, ladders, tpos)
    ti = np.array([tpos[model.tier_of(players.get(pid))] for pid in order])
    lam = np.where(ti == tpos[model.UNRANKED], 1 / model.SIGMA_UNRANKED**2, 1 / model.SIGMA_DELTA**2)
    races = [model.race_code((players.get(pid) or {}).get("r")) for pid in order]
    n = len(order)
    _, _, keep, levels, race = model.solve_two_stage(
        wi, li, ww, wwt, wt, lt, ti, lam, n, len(tpos), ws, wst, races, race_mode="cycle"
    )
    fitted = np.bincount(wi[keep], minlength=n) + np.bincount(li[keep], minlength=n) > 0
    pos = {pid: i for i, pid in enumerate(order)}
    grouped = {}
    for row in rows:
        i, j = pos[str(row[2])], pos[str(row[3])]
        if not fitted[i] or not fitted[j]:
            continue
        age = (as_of - dt.date.fromisoformat(row[1])).days
        cat = store["cats"][row[5]] if 0 <= row[5] < len(store["cats"]) else ""
        weight = model.CAT_WEIGHT.get(cat, model.DEFAULT_CAT_WEIGHT) * 0.5 ** (age / model.HALF_LIFE_DAYS)
        key = (i, j, row[4])
        grouped[key] = grouped.get(key, 0) + weight
    keys = list(grouped)
    wi = np.array([k[0] for k in keys])
    li = np.array([k[1] for k in keys])
    maps = sorted({k[2] for k in keys if k[2] is not None})
    map_pos = {v: i for i, v in enumerate(maps)}
    mi = np.array([map_pos.get(k[2], -1) for k in keys])
    ri, rs = model.race_terms(races, wi, li)
    codes = np.array([model.RACES.index(r) if r in model.RACES else -1 for r in races])
    rowidx = np.arange(len(keys))
    rr = []
    cc = []
    vv = []

    def add(r, c, v):
        rr.append(r)
        cc.append(c)
        vv.append(np.broadcast_to(v, r.shape))

    add(rowidx, wi, 1.0)
    add(rowidx, li, -1.0)
    good = (codes[wi] >= 0) & (codes[li] >= 0)
    r = rowidx[good]
    a = wi[good]
    b = li[good]
    for k in range(3):
        add(r, n + a * 3 + k, (codes[b] == k).astype(float) - 1 / 3)
        add(r, n + b * 3 + k, -(codes[a] == k).astype(float) + 1 / 3)
    good = (mi >= 0) & (rs != 0)
    add(rowidx[good], n * 4 + mi[good] * 3 + ri[good], rs[good])
    precision = np.array(list(lam) + [1 / MATCHUP_SIGMA**2] * (n * 3) + [1 / MAP_RACE_SIGMA**2] * (len(maps) * 3))
    design = coo_matrix(
        (np.concatenate(vv), (np.concatenate(rr), np.concatenate(cc))),
        shape=(len(keys), len(precision)),
    ).tocsr()
    base = levels[ti[wi]] - levels[ti[li]] + rs * race[ri]
    weights = np.array(list(grouped.values()))

    def objective(x):
        diff = base + design @ x
        loss = np.dot(weights, np.logaddexp(0, -diff)) + 0.5 * np.dot(precision, x * x)
        grad = design.T @ (-weights * expit(-diff)) + precision * x
        return float(loss), grad

    result = minimize(
        objective,
        np.zeros(len(precision)),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": 20000, "ftol": 1e-12, "gtol": 1e-7},
    )
    model.accept_solution(result, "승률 전용 종족·맵 공동학습")
    effects = result.x[n : n * 4].reshape(n, 3)
    effects = effects - effects.mean(axis=1, keepdims=True)
    return {
        "players": players,
        "order": order,
        "fitted": fitted,
        "races": races,
        "levels": levels,
        "race": race,
        "theta": levels[ti] + result.x[:n],
        "effects": effects,
        "maps": maps,
        "map_effects": result.x[n * 4 :].reshape(-1, 3),
    }


def build_prediction_model(store, tier_rows, ranking_index, map_rows):
    as_of = dt.date.fromisoformat(ranking_index["ranking"]["asOf"])
    fit = fit_prediction(store, tier_rows, as_of)
    scale = model.SCORE_SCALE
    public = []
    for k, pid in enumerate(fit["order"]):
        if not fit["fitted"][k]:
            continue
        rank_player = ranking_index["players"].get(pid) or {}
        public.append(
            [
                int(pid),
                round(float(fit["theta"][k]) * scale + model.SCORE_BASE, 1),
                {r: round(float(fit["effects"][k, j]) * scale, 3) for j, r in enumerate(model.RACES)},
                fit["races"][k],
                rank_player.get("k"),
            ]
        )
    names = {int(r["map_id"]): str(r["name"]) for r in map_rows if r.get("name")}
    return {
        "as_of": as_of.isoformat(),
        "model_version": MODEL_VERSION,
        "half_life_days": model.HALF_LIFE_DAYS,
        "tier_counts": ranking_index["ranking"]["tierCounts"],
        "tier_levels": {
            t: round(float(fit["levels"][k]) * scale + model.SCORE_BASE, 1) for k, t in enumerate(model.TIER_ORDER)
        },
        "race_matchup": {p: round(float(fit["race"][k]) * scale, 1) for k, p in enumerate(model.RACE_PAIRS)},
        "map_matchup": {
            names[mid]: {p: round(float(fit["map_effects"][k, j]) * scale, 3) for j, p in enumerate(model.RACE_PAIRS)}
            for k, mid in enumerate(fit["maps"])
            if mid in names
        },
        "ratings": {
            "c": ["elo_id", "rating", "matchup_effects", "race", "tier_rank"],
            "r": public,
        },
    }
