"""티어랭킹 되돌려 시험: EloBoard 새 점수 공지(2026-10)와 같은 조건으로 예측력을 잰다. DB는 읽기만 한다.

조건: 2025-01-01 이후 경기 중 두 선수 모두 그 경기 전에 30판 이상 한 경기. 경기마다 그 전 정보만 쓴다
(EloBoard는 한 판씩 갱신, 여기는 STEP_DAYS일마다 다시 맞춰 다음 STEP_DAYS일을 맞힌다 - 우리 쪽이 조금 불리하다).

변형(--variant):
  A  지금 티어랭킹(펨코 티어 기준선 + 개인 폼, 형식 가중치, 공통 종족 상성)
  B  A + 같은 날 같은 상대 반복 판 할인(5판째 1/2, 10판째 1/4)
  D  B + 형식 가중치 없음(대회 = 스폰)
  G  EloBoard 공지 방식 재현: Glicko-1 경기 단위 갱신 + 선수별 종족 보정 + 반복 판 할인,
     주고받는 점수가 다른 원래 방식과 같은 방식(채택안) 둘 다. 세부 값은 --tune-from~--start 앞 경기로 고른다.
     (공지 3-6의 선수층·데뷔 연도 높이 맞춤은 넣지 않았다 - 두 선수층 사이 경기가 드물어 적중률 영향이 작다)
변형마다 '+선수별 종족 보정'(C)도 함께 잰다. 펨코 티어 칸 차이만 쓴 예측도 기준으로 잰다.
'+day<K>'는 그날 아침에 맞춘 점수에 그날 앞선 경기 결과를 K(로짓)씩 더해 가며 맞힌 것이다
(실제 파이프라인은 4시간마다 그날 경기까지 넣어 다시 맞춘다). --tune-from을 주면 그 날부터 돌리고
--start 앞 경기는 'tune:' 키로 따로 모아 값 고르기에만 쓴다.

python scripts/ranking_backtest.py --variant A [--step 7] [--start 2025-01-01] [--tune-from 2024-01-01] [--half-life 90]
결과는 out/ranking_backtest/<변형>.json, 실행 기록에 BACKTEST_JSON 한 줄.
"""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import math
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
# (반복 할인, 형식 가중치 없음). G는 Glicko라 형식 가중치를 안 쓰고 반복 할인은 늘 쓴다.
VARIANTS = {"A": (False, False), "B": (True, False), "D": (True, True), "G": (True, True)}
RACE_INDEX = {"T": 0, "Z": 1, "P": 2}
DAY_STEPS = (0.03, 0.06, 0.1, 0.15)  # 그날 앞선 경기 반영 폭 후보(로짓/판)

# Glicko-1(Elo 눈금). 오차 200에서 시작해 30까지 줄고, 쉬면 하루 c²씩 분산이 자라 200을 넘지 않는다.
GLICKO_Q = math.log(10) / 400
RD_MAX, RD_MIN = 200.0, 30.0
GLICKO_C = (3.0, 5.0, 8.0, 12.0, 18.0)  # 오차가 자라는 속도 후보
GLICKO_RACE_LR = (0.0, 1.0, 2.0, 4.0, 8.0)  # 종족 보정 학습 폭 후보(점/판)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def _g(rd2):
    return 1.0 / math.sqrt(1.0 + 3.0 * GLICKO_Q**2 * rd2 / math.pi**2)


def glicko_run(games, c, lr, zero_sum, scored):
    """경기 순서대로 한 판씩 갱신한다. games: (날짜 서수, 승자, 패자, 승자가 만난 종족, 패자가 만난 종족, 배율).
    scored[k]가 참인 경기는 갱신 전에 승자 쪽 예측 확률을 남긴다(경기 단위, 그날 아침 값 둘 다).
    zero_sum이면 두 선수가 같은 크기를 주고받는다(각자 Glicko 변화량의 평균)."""
    r, rd2, last, off, start = {}, {}, {}, {}, {}
    per_game, frozen = [], []
    c2, max2, min2 = c * c, RD_MAX**2, RD_MIN**2
    for k, (day, w, lo, rw, rl, mult) in enumerate(games):
        for x in (w, lo):
            if x not in r:
                r[x], rd2[x], last[x], off[x] = 1500.0, max2, day, [0.0, 0.0, 0.0]
            elif last[x] != day:
                rd2[x] = min(max2, rd2[x] + c2 * (day - last[x]))
                last[x] = day
            if start.get(x, (None,))[0] != day:
                start[x] = (day, r[x], rd2[x], tuple(off[x]))
        ow = off[w][rw] if rw >= 0 else 0.0
        ol = off[lo][rl] if rl >= 0 else 0.0
        diff = r[w] + ow - r[lo] - ol
        if scored[k]:
            per_game.append(1.0 / (1.0 + 10 ** (-_g(rd2[w] + rd2[lo]) * diff / 400)))
            _, rw0, dw0, ow0 = start[w]
            _, rl0, dl0, ol0 = start[lo]
            d0 = rw0 + (ow0[rw] if rw >= 0 else 0.0) - rl0 - (ol0[rl] if rl >= 0 else 0.0)
            frozen.append(1.0 / (1.0 + 10 ** (-_g(dw0 + dl0) * d0 / 400)))
        gl, gw = _g(rd2[lo]), _g(rd2[w])
        ew = 1.0 / (1.0 + 10 ** (-gl * diff / 400))
        el = 1.0 / (1.0 + 10 ** (gw * diff / 400))
        den_w = 1.0 / rd2[w] + mult * GLICKO_Q**2 * gl * gl * ew * (1 - ew)
        den_l = 1.0 / rd2[lo] + mult * GLICKO_Q**2 * gw * gw * el * (1 - el)
        dw = GLICKO_Q / den_w * gl * (1.0 - ew) * mult
        dl = -GLICKO_Q / den_l * gw * el * mult
        if zero_sum:
            dw = (dw - dl) / 2
            dl = -dw
        r[w] += dw
        r[lo] += dl
        rd2[w] = max(min2, 1.0 / den_w)
        rd2[lo] = max(min2, 1.0 / den_l)
        if lr:
            e = lr * mult * (1.0 - 1.0 / (1.0 + 10 ** (-diff / 400)))
            if rw >= 0:
                off[w][rw] += e
            if rl >= 0:
                off[lo][rl] -= e
    return np.array(per_game), np.array(frozen)


def glicko_report(rows, prior, players, sponsored, start, tune_from):
    """2024년(tune_from~start) 경기의 로그손실로 c·종족 학습 폭을 고르고, start 뒤 경기로 채점한다."""
    mult = model.repeat_multipliers(rows)
    race = {pid: RACE_INDEX.get(model.race_code((p or {}).get("r")), -1) for pid, p in players.items()}
    games, when, spon = [], [], []
    for r in rows:
        w, lo = str(r[2]), str(r[3])
        day = dt.date.fromisoformat(str(r[1])[:10])
        games.append((day.toordinal(), w, lo, race.get(lo, -1), race.get(w, -1), mult.get(r[0], 1.0)))
        when.append(str(r[1])[:10])
        spon.append(r[5] in sponsored)
    ok = np.array([min(p) >= MIN_PRIOR for p in prior])
    when = np.array(when)
    tune = ok & (when >= tune_from) & (when < start)
    test = ok & (when >= start)
    spon = np.array(spon)
    cut = int(np.searchsorted(when, start))  # rows는 날짜순

    def score(p):
        return {"n": len(p), "accuracy": float(np.mean(np.where(p > 0.5, 1.0, np.where(p == 0.5, 0.5, 0.0)))),
                "logloss": float(-np.mean(np.log(np.clip(p, 1e-9, 1))))}  # fmt: skip

    out, chosen = {}, {}
    for zero_sum, name in ((False, "glicko"), (True, "glicko_zero_sum")):
        grid = []
        for c in GLICKO_C:
            for lr in GLICKO_RACE_LR:
                p, _ = glicko_run(games[:cut], c, lr, zero_sum, tune[:cut])
                grid.append((score(p)["logloss"], c, lr))
                print(f"  {name} c={c} lr={lr}: 2024 로그손실 {grid[-1][0]:.4f}", flush=True)
        _, c, lr = min(grid)
        chosen[name] = {"c": c, "race_lr": lr}
        for label, use_lr in ((name, 0.0), (name + "+race", lr)):
            p, f = glicko_run(games, c, use_lr, zero_sum, test)
            s_test = spon[test]
            out[label] = score(p)
            out[label + ":spon"] = score(p[s_test])
            out[label + ":event"] = score(p[~s_test])
            out[label + "@dayfrozen"] = score(f)
    return out, chosen


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


def offset_term(fit, w, lo):
    """승자 쪽 선수별 종족 보정 - 패자 쪽. 종족을 모르면 0."""
    rw = np.array([RACE_INDEX.get(fit["node_race"][j], -1) for j in lo])
    rl = np.array([RACE_INDEX.get(fit["node_race"][j], -1) for j in w])
    return np.where((rw >= 0) & (rl >= 0), fit["offsets"][w, np.maximum(rw, 0)]
                    - fit["offsets"][lo, np.maximum(rl, 0)], 0.0)  # fmt: skip


def day_updates(fit, day_rows, day_prior, mult, steps=DAY_STEPS):
    """아침에 맞춘 점수(+종족 보정)에 그날 앞선 경기 결과를 한 판씩 더해 가며 맞힌다.
    채점 경기(두 선수 모두 MIN_PRIOR판 이상, 둘 다 점수 있음) 순서대로 {폭: 승자 쪽 확률 배열}."""
    pos = fit["pos"]
    out = {}
    for k_step in steps:
        adj, probs = {}, []
        for r, pr in zip(day_rows, day_prior):
            a, b = pos.get(str(r[2])), pos.get(str(r[3]))
            if a is None or b is None:
                continue
            wa, la = np.array([a]), np.array([b])
            d = float(fit["theta"][a] - fit["theta"][b] + race_term(fit, wa, la)[0] + offset_term(fit, wa, la)[0])
            p = float(sigmoid(d + adj.get(a, 0.0) - adj.get(b, 0.0)))
            if min(pr) >= MIN_PRIOR:
                probs.append(p)
            step = k_step * mult.get(r[0], 1.0) * (1.0 - p)
            adj[a] = adj.get(a, 0.0) + step
            adj[b] = adj.get(b, 0.0) - step
        out[k_step] = np.array(probs)
    return out


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
    ap.add_argument("--tune-from", default="", help="이 날부터 돌려 --start 앞 경기를 값 고르기용(tune:)으로 모은다")
    ap.add_argument("--half-life", type=float, default=model.HALF_LIFE_DAYS, help="개인 폼 반감기(일)")
    ap.add_argument("--out", default="out/ranking_backtest")
    args = ap.parse_args()
    repeat, equal = VARIANTS[args.variant]
    model.HALF_LIFE_DAYS = args.half_life  # build_pairs가 부를 때마다 읽는다

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
    tune_from = args.tune_from or args.start

    if args.variant == "G":
        results, chosen = glicko_report(rows, prior, players, sponsored, args.start, tune_from)
        report = {"variant": "G", "start": args.start, "tune_from": tune_from, "chosen": chosen,
                  "seconds": round(time.monotonic() - started), "results": results}  # fmt: skip
        write_report(args, report)
        return
    day_mult = model.repeat_multipliers(rows)  # 그날 반영도 같은 날 반복 판은 덜 센다

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
    prefix = ""  # --start 앞(값 고르기용)이면 'tune:'

    def add(key, p):
        s = stats.setdefault(prefix + key, {"n": 0, "hit": 0.0, "loss": 0.0})
        s["n"] += len(p)
        s["hit"] += float(np.sum(np.where(p > 0.5, 1.0, np.where(p == 0.5, 0.5, 0.0))))
        s["loss"] += float(-np.sum(np.log(np.clip(p, 1e-9, 1))))

    scored_from = dt.date.fromisoformat(args.start)
    start = dt.date.fromisoformat(tune_from)
    last = dt.date.fromisoformat(days[-1])
    fits = 0
    while start <= last:
        end = start + dt.timedelta(days=args.step)
        prefix = "tune:" if start < scored_from else ""
        if start < scored_from < end:
            end = scored_from  # 값 고르기 구간과 채점 구간이 한 창에 섞이지 않게
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
                off = offset_term(fit, w, lo)
                spon = np.array([r[5] in sponsored for r, _, _ in got])
                for name, p in (("model", sigmoid(base)), ("model+race", sigmoid(base + off))):
                    add(name, p)
                    add(name + ":spon", p[spon])
                    add(name + ":event", p[~spon])
                for k_step, p in day_updates(fit, rows[lo_i:hi_i], prior[lo_i:hi_i], day_mult).items():
                    name = f"model+race+day{k_step}"
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
              "half_life": args.half_life, "start": args.start, "tune_from": tune_from, "fits": fits,
              "tier_beta": float(beta), "seconds": round(time.monotonic() - started), "results": summary}  # fmt: skip
    write_report(args, report)


def write_report(args, report):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False)
    (out / f"{args.variant}.json").write_text(text, encoding="utf-8")
    print("BACKTEST_JSON " + text)


if __name__ == "__main__":
    main()
