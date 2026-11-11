"""티어랭킹 되돌려 시험: EloBoard 새 점수 공지(2026-10)와 같은 조건으로 예측력을 잰다. DB는 읽기만 한다.

조건: 2025-01-01 이후 경기 중 두 선수 모두 그 경기 전에 30판 이상 한 경기. 경기마다 그 전 정보만 쓴다
(EloBoard는 한 판씩 갱신, 여기는 STEP_DAYS일마다 다시 맞춰 다음 STEP_DAYS일을 맞힌다 - 우리 쪽이 조금 불리하다).

변형(--variant):
  A  지금 티어랭킹(펨코 티어 기준선 + 개인 폼, 형식 가중치, 공통 종족 상성)
  B  A + 같은 날 같은 상대 반복 판 할인(5판째 1/2, 10판째 1/4)
  D  B + 형식 가중치 없음(대회 = 스폰)
변형마다 '+선수별 종족 보정'(C)도 함께 잰다. 펨코 티어 칸 차이만 쓴 예측도 기준으로 잰다.

python scripts/ranking_backtest.py --variant A [--step 7] [--start 2025-01-01]
결과는 out/ranking_backtest/<변형>.json, 실행 기록에 BACKTEST_JSON 한 줄.
"""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy.optimize import minimize, minimize_scalar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from processors import staruniv_ranking as model
from processors.eloboard_derived import compact_store

MIN_PRIOR = 30
OFFSET_SIGMA = 0.25  # 선수별 종족 보정 prior 폭(staruniv_prediction.MATCHUP_SIGMA와 같다)
VARIANTS = {"A": (False, False), "B": (True, False), "D": (True, True)}  # (반복 할인, 형식 가중치 없음)
RACE_INDEX = {"T": 0, "Z": 1, "P": 2}


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def prior_counts(rows):
    """경기마다 (승자, 패자)가 그 경기 전에 한 판수. rows는 (날짜, id) 순."""
    seen, out = {}, []
    for r in rows:
        w, lo = str(r[2]), str(r[3])
        out.append((seen.get(w, 0), seen.get(lo, 0)))
        seen[w] = seen.get(w, 0) + 1
        seen[lo] = seen.get(lo, 0) + 1
    return out


def fit_at(rows, days, cats, as_of, players, ladders, t_pos, n_tiers, weights, mult):
    cut = bisect.bisect_right(days, as_of.isoformat())
    wi, li, ww, ww_t, wt, lt, order, wsum, wsum_t, _ = model.build_pairs(
        rows[:cut], cats, as_of, players, ladders, t_pos, category_weights=weights, row_weights=mult
    )
    n = len(order)
    tiers_then = [model.tier_at(pid, as_of, ladders, players) for pid in order]
    tier_idx = np.fromiter((t_pos[t] for t in tiers_then), dtype=np.int64, count=n)
    unranked = tier_idx == t_pos[model.UNRANKED]
    lam = np.where(unranked, 1.0 / model.SIGMA_UNRANKED**2, 1.0 / model.SIGMA_DELTA**2)
    node_race = [model.race_code((players.get(pid) or {}).get("r")) for pid in order]
    theta, _se, keep, _m, race = model.solve_two_stage(
        wi, li, ww, ww_t, wt, lt, tier_idx, lam, n, n_tiers, wsum, wsum_t, node_race
    )
    offsets = fit_offsets(wi[keep], li[keep], ww[keep], theta, race, node_race, n)
    return {"pos": {pid: k for k, pid in enumerate(order)}, "theta": theta, "race": race, "node_race": node_race,
            "offsets": offsets}  # fmt: skip


def race_term(fit, w, lo):
    idx, sign = model.race_terms(fit["node_race"], w, lo)
    return sign * fit["race"][idx]


def fit_offsets(wi, li, ww, theta, race, node_race, n):
    """θ·공통 상성을 고정하고 선수별로 상대 종족(T·Z·P)마다 작은 보정을 맞춘다. 반환: (n, 3)."""
    base = theta[wi] - theta[li]
    idx, sign = model.race_terms(node_race, wi, li)
    base = base + sign * race[idx]
    rw = np.array([RACE_INDEX.get(node_race[j], -1) for j in li])  # 승자가 만난 종족
    rl = np.array([RACE_INDEX.get(node_race[j], -1) for j in wi])  # 패자가 만난 종족
    ok = (rw >= 0) & (rl >= 0)
    wi, li, ww, base, rw, rl = wi[ok], li[ok], ww[ok], base[ok], rw[ok], rl[ok]
    a, b = wi * 3 + rw, li * 3 + rl
    lam = 1.0 / OFFSET_SIGMA**2

    def fun_grad(o):
        d = np.clip(base + o[a] - o[b], -60, 60)
        resid = ww / (1.0 + np.exp(d))
        g = np.zeros(n * 3)
        np.add.at(g, a, -resid)
        np.add.at(g, b, resid)
        return float(np.sum(ww * np.logaddexp(0.0, -d)) + 0.5 * lam * np.sum(o**2)), g + lam * o

    res = minimize(fun_grad, np.zeros(n * 3), jac=True, method="L-BFGS-B", options={"maxiter": 5000})
    return res.x.reshape(n, 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=sorted(VARIANTS), default="A")
    ap.add_argument("--step", type=int, default=7)
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--out", default="out/ranking_backtest")
    args = ap.parse_args()
    repeat, equal = VARIANTS[args.variant]

    from repositories.derived_stats import load_source_data

    started = time.monotonic()
    source = load_source_data()
    store = compact_store(source)
    index = model.build_index(store, source["tier_members"], include_unlisted=True)
    players = index["players"]
    ladders = model.load_ladders(source["tier_members"], players)
    cats = store["cats"]
    rows = sorted((r for r in store["rows"] if len(r) >= 6), key=lambda r: (str(r[1])[:10], r[0]))
    days = [str(r[1])[:10] for r in rows]
    prior = prior_counts(rows)
    t_pos = {t: i for i, t in enumerate([*model.TIER_ORDER, model.UNRANKED])}
    n_tiers = len(t_pos)
    weights = {c: 1.0 for c in cats} if equal else None
    if equal:
        weights[""] = 1.0
    mult = model.repeat_multipliers(rows) if repeat else None
    sponsored = {i for i, c in enumerate(cats) if c == "sponsored"}

    # 펨코 티어 칸 차이 예측의 배율은 2024년 경기로 한 번 맞춘다
    def tier_gap(r, day):
        a, b = (model.tier_at(str(x), day, ladders, players) for x in (r[2], r[3]))
        if a in model.TIER_ORDER and b in model.TIER_ORDER:
            return model.TIER_ORDER.index(b) - model.TIER_ORDER.index(a)
        return None

    gaps = [g for r in rows if "2024-01-01" <= str(r[1])[:10] <= "2024-12-31"
            if (g := tier_gap(r, dt.date.fromisoformat(str(r[1])[:10]))) is not None]  # fmt: skip
    gaps = np.array(gaps, dtype=float)
    beta = minimize_scalar(lambda b: -np.mean(np.log(np.clip(sigmoid(b * gaps), 1e-9, 1))), bounds=(0, 3),
                           method="bounded").x  # fmt: skip

    stats = {}

    def add(key, p):
        s = stats.setdefault(key, {"n": 0, "hit": 0.0, "loss": 0.0})
        s["n"] += len(p)
        s["hit"] += float(np.sum(np.where(p > 0.5, 1.0, np.where(p == 0.5, 0.5, 0.0))))
        s["loss"] += float(-np.sum(np.log(np.clip(p, 1e-9, 1))))

    start = dt.date.fromisoformat(args.start)
    last = dt.date.fromisoformat(days[-1])
    fits = 0
    while start <= last:
        end = start + dt.timedelta(days=args.step)
        lo_i, hi_i = bisect.bisect_left(days, start.isoformat()), bisect.bisect_left(days, end.isoformat())
        batch = [(rows[k], prior[k]) for k in range(lo_i, hi_i) if min(prior[k]) >= MIN_PRIOR]
        if batch:
            as_of = start - dt.timedelta(days=1)
            fit = fit_at(rows, days, cats, as_of, players, ladders, t_pos, n_tiers, weights, mult)
            fits += 1
            pos = fit["pos"]
            got = [(r, pos.get(str(r[2])), pos.get(str(r[3]))) for r, _ in batch]
            got = [(r, a, b) for r, a, b in got if a is not None and b is not None]
            if got:
                w = np.array([a for _, a, _ in got])
                lo = np.array([b for _, _, b in got])
                base = fit["theta"][w] - fit["theta"][lo] + race_term(fit, w, lo)
                rw = np.array([RACE_INDEX.get(fit["node_race"][j], -1) for j in lo])
                rl = np.array([RACE_INDEX.get(fit["node_race"][j], -1) for j in w])
                off = np.where((rw >= 0) & (rl >= 0), fit["offsets"][w, np.maximum(rw, 0)]
                               - fit["offsets"][lo, np.maximum(rl, 0)], 0.0)  # fmt: skip
                spon = np.array([r[5] in sponsored for r, _, _ in got])
                for name, p in (("model", sigmoid(base)), ("model+race", sigmoid(base + off))):
                    add(name, p)
                    add(name + ":spon", p[spon])
                    add(name + ":event", p[~spon])
                tg = [tier_gap(r, as_of) for r, _, _ in got]
                has = np.array([g is not None for g in tg])
                if has.any():
                    pt = sigmoid(beta * np.array([g for g in tg if g is not None], dtype=float))
                    add("tier_only", pt)
                    add("model@tiered", sigmoid(base)[has])
            print(f"  {start}: 경기 {len(got)}", flush=True)
        start = end

    summary = {
        k: {"n": v["n"], "accuracy": v["hit"] / v["n"], "logloss": v["loss"] / v["n"]}
        for k, v in stats.items()
        if v["n"]
    }
    report = {"variant": args.variant, "repeat_discount": repeat, "equal_weights": equal, "step_days": args.step,
              "start": args.start, "fits": fits, "tier_beta": float(beta), "seconds": round(time.monotonic() - started),
              "results": summary}  # fmt: skip
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False)
    (out / f"{args.variant}.json").write_text(text, encoding="utf-8")
    print("BACKTEST_JSON " + text)


if __name__ == "__main__":
    main()
