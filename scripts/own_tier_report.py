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

CARD_GROUP = set(TIER_ORDER[:5])  # 갓~스페이드
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

    # 달마다 실력 원점이 조금씩 움직여 확정 활동 선수 중앙값을 마지막 달에 맞춘다
    ref = float(np.median(fits[-1]["theta"][fits[-1]["sure"]]))
    for x in fits:
        x["theta"] = x["theta"] - float(np.median(x["theta"][x["sure"]])) + ref
    last = fits[-1]
    top, width, count = ot.bands(last["theta"], last["se"], last["active"])
    seen = np.zeros(len(ids), dtype=bool)
    for x in fits:
        seen |= x["f"]["total"] > 0
    timelines = {}
    for i in np.flatnonzero(seen):
        tl = [(x["theta"][i], x["se"][i], bool(x["active"][i]), bool(x["sure"][i]), int(x["f"]["total"][i]))
              for x in fits]  # fmt: skip
        timelines[i] = ot.assign(tl, top, width, count)

    def tier_name(k):
        return ot.TIER_NAMES[k] if k is not None else "-"

    lines = ["# 자체 티어(참고용)", ""]
    win_step = 1 / (1 + math.exp(-width))
    lines += [
        f"- 기준일 {last_day} · 경기 {len(data['day']):,}판(최근 {ot.WINDOW_DAYS // 365}년 사용)",
        (
            f"- 칸 {count}개 ({ot.TIER_NAMES[0]}~{ot.TIER_NAMES[count - 1]}) · 한 칸 = 점수 {width * SCORE_SCALE:.0f}점"
            f" = 붙으면 위 칸 승률 {win_step:.0%}"
        ),
        (
            f"- 확정: 최근 1년 {ot.MIN_GAMES}판 이상·표준오차 {ot.MAX_SE * SCORE_SCALE:.0f}점 이하 · "
            f"휴면: 최근 {ot.DORMANT_DAYS}일 경기 없음"
        ),
        (
            f"- 승급: 오차를 빼도 위 경계를 {ot.UP_MONTHS}달 연속 넘을 때 · 강등: 오차를 더해도 아래 경계보다 반 칸 넘게 "
            f"낮은 게 {ot.DOWN_MONTHS}달 연속, 복귀 뒤 {ot.RETURN_PROTECT_GAMES}판 지나야 · 한 칸씩"
        ),
        "",
    ]

    # 1. 지금 티어표
    rows = []
    for i, tl in timelines.items():
        k, status = tl[-1]
        pid = ids[i]
        info = index.get(pid) or {}
        rows.append({
            "elo_id": pid,
            "name": info.get("n") or (store["players"].get(pid) or [""])[0],
            "roster": "예" if pid in index else "",
            "tier": tier_name(k),
            "status": status,
            "score": round(SCORE_BASE + last["theta"][i] * SCORE_SCALE),
            "se": round(last["se"][i] * SCORE_SCALE),
            "games_365": int(last["f"]["games"][i]),
            "official": info.get("t") or "",
            "team": info.get("tm") or "",
            "_k": k if k is not None else 99,
            "_theta": last["theta"][i],
        })  # fmt: skip
    live = [r for r in rows if r["status"] != "휴면" and r["games_365"] > 0]
    lines += [
        "## 지금 티어표(활동 선수)",
        "",
        "| 티어 | 확정 | 잠정 | 명단 선수(점수 높은 순, 최대 10) |",
        "|---|---|---|---|",
    ]
    for k in range(count):
        here = sorted((r for r in live if r["_k"] == k), key=lambda r: -r["_theta"])
        named = [f"{r['name']}({r['official'] or '?'})" for r in here if r["roster"] and r["status"] == "확정"][:10]
        lines.append(
            f"| {ot.TIER_NAMES[k]} | {sum(r['status'] == '확정' for r in here)} | "
            f"{sum(r['status'] == '잠정' for r in here)} | {', '.join(named)} |"
        )
    lines += ["", f"휴면 {sum(r['status'] == '휴면' for r in rows):,}명은 표에서 뺐다. 괄호는 펨코 티어(비교용).", ""]

    # 2. 펨코 티어와 맞대기(명단 확정 선수)
    sure_roster = [r for r in live if r["roster"] and r["status"] == "확정" and r["official"] in TIER_ORDER]
    lines += ["## 펨코 티어와 맞대기(명단 확정 선수, 칸 = 인원)", ""]
    lines.append("| 펨코 \\ 자체 | " + " | ".join(ot.TIER_NAMES[:count]) + " |")
    lines.append("|---" * (count + 1) + "|")
    for t in TIER_ORDER:
        cnt = [sum(1 for r in sure_roster if r["official"] == t and r["_k"] == k) for k in range(count)]
        if sum(cnt):
            lines.append(f"| {t} | " + " | ".join(str(c) if c else "" for c in cnt) + " |")
    lines.append("")

    # 3. 검증: 그 달까지로 만든 티어로 다음 90일 경기 맞히기
    lines += ["## 검증: 다음 90일 경기 맞히기(낮을수록 좋음: 로그손실)", "",
              "| 시점 | 경기 | 실력 점수 | 자체 티어 | 펨코 티어 | 그중 카드군↔숫자군 경기 | 실력 | 자체 | 펨코 |",
              "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    totals = {"n": 0, "a": 0.0, "b": 0.0, "c": 0.0, "bn": 0, "ba": 0.0, "bb": 0.0, "bc": 0.0}
    for mi, x in enumerate(fits):
        start = x["as_of"].toordinal()
        if start + HOLDOUT_DAYS > last_day.toordinal():
            continue
        tiers = np.full(len(ids), -1, dtype=np.int64)
        for i, tl in timelines.items():
            k, status = tl[mi]
            if k is not None and status != "휴면":
                tiers[i] = k
        off = official_index(ids, x["as_of"], ladders, index)
        mask = (data["day"] > start) & (data["day"] <= start + HOLDOUT_DAYS)
        w, lo = data["wi"][mask], data["li"][mask]
        ok = (tiers[w] >= 0) & (tiers[lo] >= 0) & (off[w] >= 0) & (off[lo] >= 0)
        w, lo = w[ok], lo[ok]
        if len(w) < 100:
            continue
        ridx, rsign = ot._race_index(data["race"], w, lo)
        rterm = rsign * x["race"][ridx]
        p_a = sigmoid(x["theta"][w] - x["theta"][lo] + rterm)
        mid = top - (tiers + 0.5) * width
        p_b = sigmoid(mid[w] - mid[lo] + rterm)
        # 펨코 티어는 칸 차이 하나에 배율 β를 학습 기간(앞 1년)에서 맞춘다
        tmask = (data["day"] <= start) & (data["day"] > start - 365)
        tw, tl_ = data["wi"][tmask], data["li"][tmask]
        tok = (off[tw] >= 0) & (off[tl_] >= 0)
        gap = (off[tl_] - off[tw])[tok].astype(float)
        beta = minimize_scalar(
            lambda b, gap=gap: -np.mean(np.log(np.clip(sigmoid(b * gap), 1e-9, 1))), bounds=(0, 3), method="bounded"
        ).x
        p_c = sigmoid(beta * (off[lo] - off[w]).astype(float) + rterm)
        bridge = np.array([(TIER_ORDER[off[a]] in CARD_GROUP) != (TIER_ORDER[off[b]] in CARD_GROUP)
                           for a, b in zip(w, lo)])  # fmt: skip
        la, lb, lc = ot.log_loss(p_a), ot.log_loss(p_b), ot.log_loss(p_c)
        nb = int(bridge.sum())
        bl = [ot.log_loss(p[bridge]) if nb else float("nan") for p in (p_a, p_b, p_c)]
        lines.append(
            f"| {x['as_of']} | {len(w):,} | {la:.4f} | {lb:.4f} | {lc:.4f} | {nb:,} | "
            + " | ".join(f"{v:.4f}" for v in bl)
            + " |"
        )
        totals["n"] += len(w)
        totals["a"] += la * len(w)
        totals["b"] += lb * len(w)
        totals["c"] += lc * len(w)
        if nb:
            totals["bn"] += nb
            totals["ba"] += bl[0] * nb
            totals["bb"] += bl[1] * nb
            totals["bc"] += bl[2] * nb
    if totals["n"]:
        n, bn = totals["n"], max(totals["bn"], 1)
        lines.append(
            f"| **전체** | {n:,} | {totals['a'] / n:.4f} | {totals['b'] / n:.4f} | {totals['c'] / n:.4f} | "
            f"{totals['bn']:,} | {totals['ba'] / bn:.4f} | {totals['bb'] / bn:.4f} | {totals['bc'] / bn:.4f} |"
        )
    lines += ["", "실력 점수 = 티어로 묶기 전 점수, 자체 티어 = 칸 가운데 값, 펨코 티어 = 그 시점 티어 칸 차이.", ""]

    # 4. 안정성: 달마다 확정 선수 티어가 바뀐 수
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
    keys = ["elo_id", "name", "roster", "tier", "status", "score", "se", "games_365", "official", "team"]
    with open(out / "table.csv", "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        wr.writeheader()
        for r in sorted(rows, key=lambda r: (r["_k"], -r["_theta"])):
            wr.writerow(r)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
