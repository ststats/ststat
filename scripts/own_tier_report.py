"""자체 티어(참고용) 보고서: DB를 읽기만 하고 out/own_tier/에 report.md·table.csv를 쓴다.

python scripts/own_tier_report.py [--months 18]
검증: 과거 각 달 끝 시점까지의 경기로만 티어를 만들고, 그 뒤 90일 경기를 얼마나 맞히는지 잰다.
펨코 티어(그 시점 티어)는 비교 대상으로만 쓴다.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
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


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def official_index(pids, day, ladders, index):
    out = np.full(len(pids), -1, dtype=np.int64)
    for k, pid in enumerate(pids):
        if pid in index:
            t = tier_at(pid, day, ladders, index)
            if t in TIER_ORDER:
                out[k] = TIER_ORDER.index(t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=18)
    ap.add_argument("--out", default="out/own_tier")
    args = ap.parse_args()

    from repositories.derived_stats import load_source_data

    source = load_source_data()
    store = compact_store(source)
    data = ot.prepare(store["rows"], store["cats"], store["players"])
    ids = data["ids"]
    index = build_index(store, source["tier_members"])["players"]
    ladders = load_ladders(source["tier_members"], index)
    last_day = dt.date.fromordinal(int(data["day"].max()))
    months = ot.month_ends(last_day, args.months)
    print(f"경기 {len(data['day']):,} · 선수 {len(ids):,} · {months[0]}~{months[-1]} {len(months)}달", flush=True)

    fits = []
    for as_of in months:
        f = ot.fold(data, as_of)
        theta, se, race = ot.rate(f)
        active = ot.active_mask(f)
        sure = active & (f["games"] >= ot.MIN_GAMES) & (se <= ot.MAX_SE)
        fits.append({"as_of": as_of, "f": f, "theta": theta, "se": se, "race": race, "active": active, "sure": sure})
        print(f"  {as_of}: 활동 {int(active.sum()):,}명 · 확정 {int(sure.sum()):,}명", flush=True)

    # 무리 나누기(누가 누구와 붙는지). 높은 쪽(무리 사이 경기로 잰 높이)을 1그룹으로 부른다
    group = ot.groups(data)
    last = fits[-1]
    gap = ot.group_gap(data, last["theta"], group)
    first = 0 if gap is None or gap[0] >= 0 else 1
    label = {first: "1그룹", 1 - first: "2그룹"}
    # 달마다 무리별 실력 원점이 따로 움직여, 무리마다 확정 활동 선수 중앙값을 마지막 달에 맞춘다
    for g in (0, 1):
        in_g = group == g
        ref = float(np.median(last["theta"][last["sure"] & in_g]))
        for x in fits[:-1]:
            sel = x["sure"] & in_g
            if sel.any():
                x["theta"] = np.where(in_g, x["theta"] - float(np.median(x["theta"][sel])) + ref, x["theta"])
    band = {g: ot.bands(last["theta"][group == g], last["se"][group == g], last["active"][group == g]) for g in (0, 1)}
    seen = np.zeros(len(ids), dtype=bool)
    for x in fits:
        seen |= x["f"]["total"] > 0
    timelines = {}
    for i in np.flatnonzero(seen & (group >= 0)):
        tl = [(x["theta"][i], x["se"][i], bool(x["active"][i]), bool(x["sure"][i]), int(x["f"]["total"][i]))
              for x in fits]  # fmt: skip
        timelines[i] = ot.assign(tl, *band[group[i]])

    gw, gl = group[data["wi"]], group[data["li"]]
    both = (gw >= 0) & (gl >= 0)
    cross_share = float(((gw != gl) & both).sum() / max(both.sum(), 1))
    lines = ["# 자체 티어(참고용)", ""]
    lines += [
        f"- 기준일 {last_day} · 경기 {len(data['day']):,}판(실력은 최근 {ot.WINDOW_DAYS // 365}년)",
        (
            f"- 누가 누구와 붙는지로 선수를 두 그룹으로 나눴다. 그룹 사이 경기는 전체의 {cross_share:.2%}뿐이라 "
            "높이를 맞출 수 없어 그룹마다 따로 티어를 매긴다"
        ),
        (
            f"- 한 칸 = 붙으면 위 칸 승률 약 {ot.TIER_STEP_WIN:.0%}(오차가 크면 칸을 넓힌다) · 확정: 최근 1년 "
            f"{ot.MIN_GAMES}판 이상·표준오차 {ot.MAX_SE * SCORE_SCALE:.0f}점 이하 · 휴면: 최근 {ot.DORMANT_DAYS}일 경기 없음"
        ),
        (
            f"- 승급: 오차를 빼도 위 경계를 {ot.UP_MONTHS}달 연속 넘을 때 · 강등: 오차를 더해도 아래 경계보다 반 칸 넘게 "
            f"낮은 게 {ot.DOWN_MONTHS}달 연속, 복귀 뒤 {ot.RETURN_PROTECT_GAMES}판 지나야 · 한 칸씩"
        ),
        "",
    ]
    if gap is not None:
        g_, se_, n_, mid_ = gap
        lines += [
            "## 참고: 두 그룹 높이 차이",
            "",
            (
                f"모든 기간 그룹 사이 경기 {n_:,}판(날짜 중앙값 {mid_})으로 재면, 1그룹 중앙 선수가 2그룹 중앙 선수보다 "
                f"약 {abs(g_) * SCORE_SCALE:.0f}점(±{se_ * SCORE_SCALE:.0f}) 높다"
                f"(붙으면 1그룹 중앙 선수 승률 {1 / (1 + math.exp(-abs(g_))):.0%}). "
                "지금 실력을 옛 경기에도 써서 거친 값이라 티어에는 쓰지 않는다."
            ),
            "",
        ]

    def tier_name(i, k):
        return f"{label[group[i]]} {ot.TIER_NAMES[k]}" if k is not None else "-"

    rows = []
    for i, tl in timelines.items():
        k, status = tl[-1]
        pid = ids[i]
        info = index.get(pid) or {}
        rows.append({
            "elo_id": pid,
            "name": info.get("n") or (store["players"].get(pid) or [""])[0],
            "roster": "예" if pid in index else "",
            "group": label[group[i]],
            "tier": tier_name(i, k),
            "status": status,
            "score": round(SCORE_BASE + last["theta"][i] * SCORE_SCALE),
            "se": round(last["se"][i] * SCORE_SCALE),
            "games_365": int(last["f"]["games"][i]),
            "official": info.get("t") or "",
            "team": info.get("tm") or "",
            "_g": int(group[i]),
            "_k": k if k is not None else 99,
            "_theta": last["theta"][i],
        })  # fmt: skip
    live = [r for r in rows if r["status"] != "휴면" and r["games_365"] > 0]
    for g in sorted((0, 1), key=lambda g: label[g]):
        top, width, count = band[g]
        here_g = [r for r in live if r["_g"] == g]
        lines += [
            f"## {label[g]} 티어표(활동 {len(here_g)}명 · 칸 {count}개 · 한 칸 {width * SCORE_SCALE:.0f}점)",
            "",
            "| 티어 | 확정 | 잠정 | 명단 선수(점수 높은 순, 최대 10) |",
            "|---|---|---|---|",
        ]
        for k in range(count):
            here = sorted((r for r in here_g if r["_k"] == k), key=lambda r: -r["_theta"])
            named = [f"{r['name']}({r['official'] or '?'})" for r in here if r["roster"] and r["status"] == "확정"][:10]
            lines.append(
                f"| {ot.TIER_NAMES[k]} | {sum(r['status'] == '확정' for r in here)} | "
                f"{sum(r['status'] == '잠정' for r in here)} | {', '.join(named)} |"
            )
        lines += ["", "펨코 티어와 맞대기(명단 확정 선수, 칸 = 인원):", ""]
        lines.append("| 펨코 \\ 자체 | " + " | ".join(ot.TIER_NAMES[:count]) + " |")
        lines.append("|---" * (count + 1) + "|")
        sure_roster = [r for r in here_g if r["roster"] and r["status"] == "확정" and r["official"] in TIER_ORDER]
        for t in TIER_ORDER:
            cnt = [sum(1 for r in sure_roster if r["official"] == t and r["_k"] == k) for k in range(count)]
            if sum(cnt):
                lines.append(f"| {t} | " + " | ".join(str(c) if c else "" for c in cnt) + " |")
        lines.append("")
    lines += [f"휴면 {sum(r['status'] == '휴면' for r in rows):,}명은 표에서 뺐다. 괄호는 펨코 티어(비교용).", ""]

    # 검증: 그 달까지로 만든 티어로 다음 90일 같은 그룹 경기 맞히기
    lines += ["## 검증: 다음 90일 같은 그룹 경기 맞히기(로그손실, 낮을수록 좋음)", "",
              "| 시점 | 경기 | 실력 점수 | 자체 티어 | 펨코 티어 | 뺀 그룹 사이 경기 |", "|---|---|---|---|---|---|"]  # fmt: skip
    totals = {"n": 0, "a": 0.0, "b": 0.0, "c": 0.0, "x": 0}
    for mi, x in enumerate(fits):
        start = x["as_of"].toordinal()
        if start + HOLDOUT_DAYS > last_day.toordinal():
            continue
        mid = np.full(len(ids), np.nan)
        for i, tl in timelines.items():
            k, status = tl[mi]
            if k is not None and status != "휴면":
                top, width, _ = band[group[i]]
                mid[i] = top - (k + 0.5) * width
        off = official_index(ids, x["as_of"], ladders, index)
        mask = (data["day"] > start) & (data["day"] <= start + HOLDOUT_DAYS)
        w, lo = data["wi"][mask], data["li"][mask]
        ok = ~np.isnan(mid[w]) & ~np.isnan(mid[lo]) & (off[w] >= 0) & (off[lo] >= 0)
        same = ok & (group[w] == group[lo])
        crossed = int((ok & (group[w] != group[lo])).sum())
        w, lo = w[same], lo[same]
        if len(w) < 100:
            continue
        ridx, rsign = ot._race_index(data["race"], w, lo)
        rterm = rsign * x["race"][ridx]
        p_a = sigmoid(x["theta"][w] - x["theta"][lo] + rterm)
        p_b = sigmoid(mid[w] - mid[lo] + rterm)
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
        p_c = sigmoid(beta * (off[lo] - off[w]).astype(float) + rterm)
        la, lb, lc = ot.log_loss(p_a), ot.log_loss(p_b), ot.log_loss(p_c)
        lines.append(f"| {x['as_of']} | {len(w):,} | {la:.4f} | {lb:.4f} | {lc:.4f} | {crossed} |")
        totals["n"] += len(w)
        totals["a"] += la * len(w)
        totals["b"] += lb * len(w)
        totals["c"] += lc * len(w)
        totals["x"] += crossed
    if totals["n"]:
        n = totals["n"]
        lines.append(
            f"| **전체** | {n:,} | {totals['a'] / n:.4f} | {totals['b'] / n:.4f} | {totals['c'] / n:.4f} | "
            f"{totals['x']:,} |"
        )
    lines += ["", "실력 점수 = 티어로 묶기 전 점수, 자체 티어 = 칸 가운데 값, 펨코 티어 = 그 시점 티어 칸 차이.", ""]

    # 안정성: 달마다 확정 선수 티어가 바뀐 수
    lines += ["## 안정성: 달마다 티어가 바뀐 확정 선수", "", "| 달 | 승급 | 강등 | 확정 활동 |", "|---|---|---|---|"]
    for mi in range(1, len(fits)):
        upn = downn = sure_n = 0
        for tl in timelines.values():
            (k0, s0), (k1, s1) = tl[mi - 1], tl[mi]
            if s1 == "확정":
                sure_n += 1
            if k0 is None or k1 is None or "휴면" in (s0, s1) or s0 != "확정":
                continue
            upn += k1 < k0
            downn += k1 > k0
        lines.append(f"| {fits[mi]['as_of']:%Y-%m} | {upn} | {downn} | {sure_n} |")
    lines.append("")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    keys = ["elo_id", "name", "roster", "group", "tier", "status", "score", "se", "games_365", "official", "team"]
    with open(out / "table.csv", "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        wr.writeheader()
        for r in sorted(rows, key=lambda r: (r["group"], r["_k"], -r["_theta"])):
            wr.writerow(r)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
