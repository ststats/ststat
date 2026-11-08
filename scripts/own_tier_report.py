"""자체 티어(참고용) 보고서: DB를 읽기만 하고 out/own_tier/에 report.json·report.md·table.csv를 쓴다.

python scripts/own_tier_report.py [--months 18]
검증: 과거 각 달 끝 시점까지의 경기로만 티어를 만들고, 그 뒤 90일 경기를 얼마나 맞히는지 잰다.
펨코 티어(그 시점 티어)는 비교 대상으로만 쓴다. 실행 기록에서 보고서를 꺼낼 수 있게 JSON을 한 줄(OWN_TIER_JSON)로도 찍는다.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from processors import own_tier as ot
from processors.eloboard_derived import compact_store
from processors.staruniv_ranking import (
    SCORE_BASE,
    SCORE_SCALE,
    TIER_ORDER,
    build_index,
    load_ladders,
    tier_at,
)

HOLDOUT_DAYS = 90
# 승급 기준 후보(오차 배수, 연속 달 수). 첫 번째가 표에 쓰는 기준(own_tier 기본값)
VARIANTS = [(ot.Z_UP, ot.UP_MONTHS), (1.0, 2), (1.0, 3), (1.5, 2)]


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def pts(x):
    return round(float(x) * SCORE_SCALE)


def official_index(pids, day, ladders, index):
    out = np.full(len(pids), -1, dtype=np.int64)
    for k, pid in enumerate(pids):
        if pid in index:
            t = tier_at(pid, day, ladders, index)
            if t in TIER_ORDER:
                out[k] = TIER_ORDER.index(t)
    return out


def load():
    from repositories.derived_stats import load_source_data

    source = load_source_data()
    store = compact_store(source)
    index = build_index(store, source["tier_members"])["players"]
    return store, index, load_ladders(source["tier_members"], index)


def fit_months(data, months):
    fits = []
    for as_of in months:
        f = ot.fold(data, as_of)
        theta, se, race, form = ot.rate(f)
        active = ot.active_mask(f)
        sure = active & (f["games"] >= ot.MIN_GAMES) & (se <= ot.MAX_SE)
        fits.append({"as_of": as_of, "f": f, "theta": theta, "se": se, "race": race, "form": form,
                     "active": active, "sure": sure})  # fmt: skip
        print(f"  {as_of}: 활동 {int(active.sum()):,}명 · 확정 {int(sure.sum()):,}명", flush=True)
    return fits


def align(fits, group):
    """달마다 무리별 실력 원점이 따로 움직여, 무리마다 확정 활동 선수 중앙값을 마지막 달에 맞춘다."""
    last = fits[-1]
    for g in (0, 1):
        in_g = group == g
        ref = float(np.median(last["theta"][last["sure"] & in_g]))
        for x in fits[:-1]:
            sel = x["sure"] & in_g
            if sel.any():
                x["theta"] = np.where(in_g, x["theta"] - float(np.median(x["theta"][sel])) + ref, x["theta"])


def timelines_for(fits, group, band, z_up, up_months):
    seen = np.zeros(len(group), dtype=bool)
    for x in fits:
        seen |= x["f"]["total"] > 0
    out = {}
    for i in np.flatnonzero(seen & (group >= 0)):
        tl = [(x["theta"][i], x["se"][i], bool(x["active"][i]), bool(x["sure"][i])) for x in fits]
        out[int(i)] = ot.assign(tl, *band[group[i]], z_up=z_up, up_months=up_months)
    return out


def validate(data, fits, group, band, timelines, ladders, index, ids, last_day):
    """그 달까지로 만든 값으로 다음 90일 같은 그룹 경기 맞히기(로그손실). 실력 점수·지금 실력 칸·도달 티어·펨코."""
    rows, tot = [], {"n": 0, "score": 0.0, "now": 0.0, "reached": 0.0, "official": 0.0}
    for mi, x in enumerate(fits):
        start = x["as_of"].toordinal()
        if start + HOLDOUT_DAYS > last_day.toordinal():
            continue
        reached = np.full(len(ids), np.nan)
        now = np.full(len(ids), np.nan)
        for i, tl in timelines.items():
            k, status, k_now, _ = tl[mi]
            if k is not None and status != "휴면":
                top, width, _ = band[group[i]]
                reached[i] = top - (k + 0.5) * width
                now[i] = top - (k_now + 0.5) * width
        off = official_index(ids, x["as_of"], ladders, index)
        mask = (data["day"] > start) & (data["day"] <= start + HOLDOUT_DAYS)
        w, lo = data["wi"][mask], data["li"][mask]
        ok = ~np.isnan(reached[w]) & ~np.isnan(reached[lo]) & (off[w] >= 0) & (off[lo] >= 0)
        ok &= group[w] == group[lo]
        w, lo = w[ok], lo[ok]
        if len(w) < 100:
            continue
        ridx, rsign = ot._race_index(data["race"], w, lo)
        rterm = rsign * x["race"][ridx]
        # 펨코 티어는 칸 차이 하나에 배율 β를 학습 기간(앞 1년)에서 맞춘다
        tmask = (data["day"] <= start) & (data["day"] > start - 365)
        tw, tl_ = data["wi"][tmask], data["li"][tmask]
        tok = (off[tw] >= 0) & (off[tl_] >= 0)
        gap_t = (off[tl_] - off[tw])[tok].astype(float)
        beta = minimize_scalar(
            lambda b, gap_t=gap_t: -np.mean(np.log(np.clip(sigmoid(b * gap_t), 1e-9, 1))),
            bounds=(0, 3),
            method="bounded",
        ).x
        row = {
            "as_of": x["as_of"].isoformat(),
            "n": len(w),
            "score": ot.log_loss(sigmoid(x["theta"][w] - x["theta"][lo] + rterm)),
            "now": ot.log_loss(sigmoid(now[w] - now[lo] + rterm)),
            "reached": ot.log_loss(sigmoid(reached[w] - reached[lo] + rterm)),
            "official": ot.log_loss(sigmoid(beta * (off[lo] - off[w]).astype(float) + rterm)),
        }
        rows.append(row)
        tot["n"] += row["n"]
        for key in ("score", "now", "reached", "official"):
            tot[key] += row[key] * row["n"]
    if tot["n"]:
        for key in ("score", "now", "reached", "official"):
            tot[key] /= tot["n"]
    return rows, tot


def stability(fits, timelines):
    out = []
    for mi in range(1, len(fits)):
        up = declining = sure = 0
        for tl in timelines.values():
            (k0, s0, _, _), (k1, s1, _, dec) = tl[mi - 1], tl[mi]
            sure += s1 == "확정"
            declining += bool(dec)
            if k0 is not None and k1 is not None and s0 == "확정" and "휴면" not in (s0, s1):
                up += k1 < k0
        out.append({"month": f"{fits[mi]['as_of']:%Y-%m}", "promotions": up, "declining": declining, "sure": sure})
    return out


def explain(data, ids, group, players, last, ladders, index, last_day, band, kname):
    """같은 펨코 티어 확정 선수들의 보통 칸(중앙값)과 2칸 이상 다른 명단 선수의 근거."""
    off = official_index(ids, last_day, ladders, index)
    t = last_day.toordinal()
    year, recent = data["day"] > t - 365, data["day"] > t - 90
    sure = [p for p in players if p["roster"] and p["status"] == "확정" and p["official"] in TIER_ORDER]
    usual = {}
    for p in sure:
        usual.setdefault((p["_g"], p["official"]), []).append(p["_k"])
    usual = {key: float(np.median(v)) for key, v in usual.items()}
    out = []
    for p in sure:
        exp = usual[(p["_g"], p["official"])]
        if abs(p["_k"] - exp) < 2:
            continue
        i, g, me = p["_i"], p["_g"], off[p["_i"]]

        def record(mask, cond, i=i, g=g):
            wins, losses = mask & (data["wi"] == i), mask & (data["li"] == i)
            ow, ol = data["li"][wins], data["wi"][losses]
            return [int((cond(ow) & (group[ow] == g)).sum()), int((cond(ol) & (group[ol] == g)).sum())]

        def everyone(o):
            return np.ones(len(o), dtype=bool)

        def same(o, me=me):
            return off[o] == me

        def above(o, me=me):
            return (off[o] >= 0) & (off[o] < me)

        top, width, _ = band[g]
        low, high = ot.band_bounds(p["_k"], top, width)
        theta, se, form = last["theta"][i], last["se"][i], last["form"][i]
        sm, ab = record(year, same), record(year, above)
        higher = p["_k"] < exp
        why = []
        if sum(sm) >= 10 and ((sm[0] / sum(sm) >= 0.6) if higher else (sm[0] / sum(sm) <= 0.4)):
            why.append(f"같은 {p['official']}티어 상대 {sm[0] / sum(sm):.0%}")
        if higher and sum(ab) >= 5 and ab[0] / sum(ab) >= 0.45:
            why.append(f"위 티어 상대도 {ab[0] / sum(ab):.0%}")
        if higher and sum(ab) == 0:
            why.append("위 티어와 붙은 기록이 없어 위쪽은 확인 안 됨")
        if abs(form) * SCORE_SCALE >= 40 and (form > 0) == higher:
            why.append(f"최근 폼 {pts(form):+d}점")
        if se * SCORE_SCALE >= 60:
            why.append(f"오차 ±{pts(se)}점으로 덜 확실")
        if not why:
            why.append("여러 상대와의 전적이 고르게 " + ("좋음" if higher else "나쁨"))
        out.append({
            "name": p["name"], "group": p["group"], "official": p["official"], "tier": p["tier"],
            "usual": kname(g, round(exp)), "score": p["score"], "se": p["se"],
            "proven": pts(theta - ot.Z_UP * se) + SCORE_BASE, "no_form": pts(theta - form) + SCORE_BASE,
            "form": pts(form), "to_low": pts(theta - low), "to_high": pts(high - theta),
            "year": record(year, everyone), "same": sm, "above": ab, "recent": record(recent, everyone),
            "why": why, "higher": bool(higher),
        })  # fmt: skip
    out.sort(key=lambda r: (r["group"], not r["higher"], r["official"]))
    return out


def build(store, index, ladders, months_back):
    data = ot.prepare(store["rows"], store["cats"], store["players"])
    ids = data["ids"]
    last_day = dt.date.fromordinal(int(data["day"].max()))
    months = ot.month_ends(last_day, months_back)
    print(f"경기 {len(data['day']):,} · 선수 {len(ids):,} · {months[0]}~{months[-1]} {len(months)}달", flush=True)
    fits = fit_months(data, months)
    # 무리 나누기(누가 누구와 붙는지). 높은 쪽(무리 사이 경기로 잰 높이)을 1그룹으로 부른다
    group = ot.groups(data)
    last = fits[-1]
    gap = ot.group_gap(data, last["theta"], group)
    first = 0 if gap is None or gap[0] >= 0 else 1
    label = {first: "1그룹", 1 - first: "2그룹"}
    align(fits, group)
    band = {g: ot.bands(last["theta"][group == g], last["se"][group == g], last["active"][group == g]) for g in (0, 1)}

    # 1그룹은 글자(S·A·B…), 2그룹은 숫자(0·1·2…)
    def kname(g, k):
        return ot.TIER_NAMES[k] if g == first else str(k)

    variants = []
    for z, m in VARIANTS:
        tls = timelines_for(fits, group, band, z, m)
        _, tot = validate(data, fits, group, band, tls, ladders, index, ids, last_day)
        stab = stability(fits, tls)
        variants.append({"z": z, "months": m, "promotions": sum(s["promotions"] for s in stab),
                         "reached": tot.get("reached"), "_tls": tls})  # fmt: skip
        print(f"  승급 기준 오차 {z}배·{m}달: 승급 {variants[-1]['promotions']}번", flush=True)
    timelines = variants[0]["_tls"]
    val_rows, val_tot = validate(data, fits, group, band, timelines, ladders, index, ids, last_day)

    players = []
    for i, tl in timelines.items():
        k, status, k_now, declining = tl[-1]
        if status == "휴면" or last["f"]["games"][i] == 0:
            continue
        pid = ids[i]
        info = index.get(pid) or {}
        g = int(group[i])
        players.append({
            "elo_id": pid, "name": info.get("n") or (store["players"].get(pid) or [""])[0],
            "roster": pid in index, "group": label[g], "tier": kname(g, k) if k is not None else "",
            "now": kname(g, k_now), "status": status, "declining": bool(declining),
            "score": pts(last["theta"][i]) + SCORE_BASE, "se": pts(last["se"][i]),
            "games": int(last["f"]["games"][i]), "official": info.get("t") or "", "team": info.get("tm") or "",
            "_i": i, "_g": g, "_k": k if k is not None else 99,
        })  # fmt: skip
    players.sort(key=lambda p: (p["group"], p["_k"], -p["score"]))
    gw, gl = group[data["wi"]], group[data["li"]]
    both = (gw >= 0) & (gl >= 0)
    groups_out = []
    for g in sorted((0, 1), key=lambda g: label[g]):
        top, width, count = band[g]
        mine = [p for p in players if p["_g"] == g]
        cross = []
        for t in TIER_ORDER:
            cnt = [sum(1 for p in mine if p["roster"] and p["status"] == "확정" and p["official"] == t and p["_k"] == k)
                   for k in range(count)]  # fmt: skip
            if sum(cnt):
                cross.append({"official": t, "counts": cnt})
        groups_out.append({
            "label": label[g], "names": [kname(g, k) for k in range(count)], "width": pts(width),
            "bounds": [pts(ot.band_bounds(k, top, width)[0]) + SCORE_BASE for k in range(count)],
            "active": len(mine), "cross": cross,
        })  # fmt: skip
    report = {
        "as_of": last_day.isoformat(),
        "matches": len(data["day"]),
        "players": [{k: v for k, v in p.items() if not k.startswith("_")} for p in players],
        "dormant": sum(1 for tl in timelines.values() if tl[-1][1] == "휴면"),
        "cross_share": float(((gw != gl) & both).sum() / max(both.sum(), 1)),
        "rules": {
            "step_win": ot.TIER_STEP_WIN,
            "min_games": ot.MIN_GAMES,
            "max_se": pts(ot.MAX_SE),
            "dormant_days": ot.DORMANT_DAYS,
            "z_up": ot.Z_UP,
            "up_months": ot.UP_MONTHS,
            "decline_months": ot.DECLINE_MONTHS,
            "window_years": ot.WINDOW_DAYS // 365,
        },
        "gap": None
        if gap is None
        else {
            "points": pts(abs(gap[0])),
            "se": pts(gap[1]),
            "games": gap[2],
            "median_day": gap[3].isoformat(),
            "win": 1 / (1 + math.exp(-abs(gap[0]))),
        },
        "groups": groups_out,
        "variants": [{k: v for k, v in x.items() if k != "_tls"} for x in variants],
        "validation": {"rows": val_rows, "total": val_tot},
        "stability": stability(fits, timelines),
        "explain": explain(data, ids, group, players, last, ladders, index, last_day, band, kname),
    }
    return report


def markdown(r):
    rule = r["rules"]
    lines = [
        "# 자체 티어(참고용)",
        "",
        f"- 기준일 {r['as_of']} · 경기 {r['matches']:,}판 · 그룹 사이 경기 {r['cross_share']:.2%}라 그룹마다 따로 매긴다",
        (
            f"- 티어 = 도달 티어(내리지 않음). 처음 칸·승급 모두 오차 {rule['z_up']}배를 뺀 점수로, 승급은 "
            f"{rule['up_months']}달 연속 · 하락세 = 지금 실력이 한 칸 넘게 아래인 게 {rule['decline_months']}달 연속"
        ),
        "",
    ]
    for g in r["groups"]:
        lines += [f"## {g['label']} (활동 {g['active']}명 · 칸 {len(g['names'])}개 · 한 칸 {g['width']}점)", ""]
        for name in g["names"]:
            here = [p for p in r["players"] if p["group"] == g["label"] and p["tier"] == name]
            lines.append(f"- **{name}**: " + ", ".join(
                f"{p['name']}({p['official'] or '?'}){'↓' if p['declining'] else ''}" for p in here if p["roster"]
            ))  # fmt: skip
        lines.append("")
    v = r["validation"]["total"]
    if v.get("n"):
        lines += [
            (
                f"검증(다음 90일 {v['n']:,}판 로그손실): 실력 {v['score']:.4f} · 지금 칸 {v['now']:.4f} · "
                f"도달 티어 {v['reached']:.4f} · 펨코 {v['official']:.4f}"
            ),
            "",
        ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=18)
    ap.add_argument("--out", default="out/own_tier")
    args = ap.parse_args()
    store, index, ladders = load()
    report = build(store, index, ladders, args.months)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False)
    (out / "report.json").write_text(text, encoding="utf-8")
    (out / "report.md").write_text(markdown(report), encoding="utf-8")
    keys = ["elo_id", "name", "group", "tier", "now", "declining", "status", "score", "se", "games", "official", "team"]
    with open(out / "table.csv", "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(report["players"])
    print(markdown(report))
    print("OWN_TIER_JSON " + text)


if __name__ == "__main__":
    main()
