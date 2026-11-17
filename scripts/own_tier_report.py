"""자체 티어(참고용) 보고서: DB를 읽기만 하고 out/own_tier/에 report.json·report.md·table.csv를 쓴다.

python scripts/own_tier_report.py [--months 36]
도달 티어는 이 기간 첫 달부터 따라간다. 짧으면 그 전에 휴면한 선수가 도달 티어를 잃고 복귀 때 오차 큰 낮은 칸에서
다시 시작하므로, 강등이 아주 드문 규칙에 맞춰 경기 창(3년)만큼 길게 본다.
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
# 승급 기준 후보(오차 배수, 최근 몇 달 중 몇 달). 첫 번째가 표에 쓰는 기준(own_tier 기본값)
VARIANTS = [(ot.Z_UP, ot.UP_MONTHS, ot.UP_WINDOW), (1.0, 2, 3), (0.0, 2, 3), (0.0, 3, 4)]


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


def load_aliases() -> dict[str, str]:
    """선수 관리의 연결 계정 {연결 ELO ID: 그 선수의 메인 ELO ID}."""
    from repositories.derived_stats import _paged

    main = {r["id"]: r.get("elo_id") for r in _paged("tier_members", "id,elo_id", "id")}
    out = {}
    for r in _paged("tier_member_elo_links", "elo_id,tier_member_id", "elo_id"):
        target = main.get(r.get("tier_member_id"))
        if target is not None and r.get("elo_id") is not None and str(r["elo_id"]) != str(target):
            out[str(r["elo_id"])] = str(target)
    return out


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


def tier_score(x, i):
    """칸을 정하는 점수: θ에서 최근 폼을 FORM_IN_TIER만큼만 남긴 값."""
    return x["theta"][i] - (1 - ot.FORM_IN_TIER) * x["form"][i]


def timeline_of(fits, i):
    """선수 i의 월별 (θ, 표준오차, 활동, 확정, 칸 점수). ot.assign이 받는 꼴."""
    return [(x["theta"][i], x["se"][i], bool(x["active"][i]), bool(x["sure"][i]), tier_score(x, i)) for x in fits]


def anchor_top(fits, group, g, target, most, z_up=ot.Z_UP, up_months=ot.UP_MONTHS, up_window=ot.UP_WINDOW):
    """g 그룹의 칸을 긋기 시작할 곳(ot.bands의 top). 칸 폭·선수 순서는 그대로 두고 경계 위치만 옮겨 보며,
    그때 매겨지는 도달 티어(승급·강등을 다시 따라간 마지막 달)가 펨코 티어(target, 그룹 안 칸 번호)와 같은
    명단 확정 선수가 가장 많은 곳을 고른다(같으면 평균 차가 작은 곳). 지금 실력이 아니라 도달 티어로 맞춰야
    승급이 늦는 만큼 한 칸 아래로 밀리지 않는다. 맨 위 칸(0칸·갓)이 비는 위치는 고르지 않는다.
    비교할 선수가 적으면 None(기본 위치)."""
    last = fits[-1]
    m = group == g
    theta, se, sure = last["theta"][m], last["se"][m], last["sure"][m]
    who = np.flatnonzero(m & last["sure"] & (target >= 0))
    if len(who) < 10:
        return None
    tls = [timeline_of(fits, i) for i in who]
    hi = float(np.quantile(theta[sure], 0.995))
    step = ot.logit(ot.TIER_STEP_WIN)
    best, best_key = None, None
    for top in hi + np.arange(-step, 3 * step, step / 20):
        b = ot.bands(theta, se, sure, top=top, most=most)
        reached = np.array([ot.assign(tl, b, z_up, up_months, up_window)[-1][0] for tl in tls])
        if reached.min() > 0:
            continue
        diff = reached - target[who]
        key = (float(np.mean(diff == 0)), -abs(float(np.mean(diff))))
        if best_key is None or key > best_key:
            best, best_key = float(top), key
    return best


def timelines_for(fits, group, band, z_up, up_months, up_window):
    seen = np.zeros(len(group), dtype=bool)
    for x in fits:
        seen |= x["f"]["total"] > 0
    out = {}
    for i in np.flatnonzero(seen & (group >= 0)):
        tl = timeline_of(fits, i)
        out[int(i)] = ot.assign(tl, band[group[i]], z_up=z_up, up_months=up_months, up_window=up_window)
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
                b = band[group[i]]
                reached[i], now[i] = ot.band_mid(k, b), ot.band_mid(k_now, b)
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
        up = down = declining = sure = 0
        for tl in timelines.values():
            (k0, s0, _, _), (k1, s1, _, dec) = tl[mi - 1], tl[mi]
            sure += s1 == "확정"
            declining += bool(dec)
            if k0 is not None and k1 is not None and s0 == "확정" and "휴면" not in (s0, s1):
                up += k1 < k0
                down += k1 > k0
        out.append({"month": f"{fits[mi]['as_of']:%Y-%m}", "promotions": up, "demotions": down,
                    "declining": declining, "sure": sure})  # fmt: skip
    return out


def matchups(players, last):
    """대회처럼 같은 칸끼리만 붙일 때 대진이 얼마나 기우나. 명단 확정 선수의 같은 그룹·같은 칸 모든 쌍에서
    강자 승률(지금 실력 점수로)의 중앙값과 70%를 넘는 쌍의 몫. 칸은 자체 도달 티어·그 안의 상하·지금 실력 칸·펨코 티어."""
    sure = [p for p in players if p["roster"] and p["status"] == "확정"]
    out = {}
    for key in ("tier", "half", "now", "official"):
        cells = {}
        for p in sure:
            cell = (p["tier"] and p["tier"] + p["half"]) if key == "half" else p[key]  # 같은 칸 안 상끼리·하끼리
            if cell and (key != "official" or cell in TIER_ORDER):  # 펨코 '체크'는 뺀다
                cells.setdefault((p["_g"], cell), []).append(last["theta"][p["_i"]])
        gaps = [np.abs(np.subtract.outer(v, v))[np.triu_indices(len(v), 1)] for v in cells.values()]
        fav = sigmoid(np.concatenate([np.zeros(0), *gaps]))
        if len(fav):
            out[key] = {"pairs": len(fav), "median": float(np.median(fav)), "over70": float(np.mean(fav > 0.7))}
    return out


def explain(data, ids, group, players, last, ladders, index, last_day, band, target):
    """펨코 티어와 2칸 이상 다른 명단 확정 선수의 근거. 칸 경계를 펨코에 맞춰 두어(anchor_top) 칸 번호를
    펨코 티어(target, 그룹 안 칸 번호)와 바로 견준다."""
    off = official_index(ids, last_day, ladders, index)
    t = last_day.toordinal()
    year, recent = data["day"] > t - 365, data["day"] > t - 90
    sure = [p for p in players if p["roster"] and p["status"] == "확정" and target[p["_g"]][p["_i"]] >= 0]
    out = []
    for p in sure:
        exp = target[p["_g"]][p["_i"]]
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

        def above(o, me=me):  # 바로 한 티어 위(엄보리 펨코 6이면 5티어만)
            return off[o] == me - 1

        low, high = ot.band_bounds(p["_k"], band[g])
        theta, se, form = last["theta"][i], last["se"][i], last["form"][i]
        sm, ab = record(year, same), record(year, above)
        higher = p["_k"] < exp
        why = []
        if sum(sm) >= 10 and ((sm[0] / sum(sm) >= 0.6) if higher else (sm[0] / sum(sm) <= 0.4)):
            why.append(f"같은 {p['official']}티어 상대 {sm[0] / sum(sm):.0%}")
        if higher and sum(ab) >= 5 and ab[0] / sum(ab) >= 0.45:
            why.append(f"한 티어 위({TIER_ORDER[me - 1]}티어) 상대도 {ab[0] / sum(ab):.0%}")
        if higher and sum(ab) == 0:
            why.append("한 티어 위와 붙은 기록이 없어 위쪽은 확인 안 됨")
        if abs(form) * SCORE_SCALE >= 40 and (form > 0) == higher:
            why.append(f"최근 폼 {pts(form):+d}점")
        if se * SCORE_SCALE >= 60:
            why.append(f"오차 ±{pts(se)}점으로 덜 확실")
        if not why:
            why.append("여러 상대와의 전적이 고르게 " + ("좋음" if higher else "나쁨"))
        out.append({
            "name": p["name"], "group": p["group"], "official": p["official"], "tier": p["tier"],
            "score": p["score"], "se": p["se"],
            "proven": pts(theta - ot.Z_UP * se) + SCORE_BASE, "no_form": pts(theta - form) + SCORE_BASE,
            "form": pts(form), "to_low": pts(theta - low), "to_high": pts(high - theta),
            "year": record(year, everyone), "same": sm, "above": ab, "recent": record(recent, everyone),
            "why": why, "higher": bool(higher),
        })  # fmt: skip
    out.sort(key=lambda r: (r["group"], not r["higher"], r["official"]))
    return out


def build(store, index, ladders, months_back, alias=None):
    data = ot.prepare(store["rows"], store["cats"], store["players"], alias)
    ids = data["ids"]
    last_day = dt.date.fromordinal(int(data["day"].max()))
    months = ot.month_ends(last_day, months_back)
    print(f"경기 {len(data['day']):,} · 선수 {len(ids):,} · {months[0]}~{months[-1]} {len(months)}달", flush=True)
    print(f"연결 계정 {len(alias or {}):,}개를 메인 계정으로 합침", flush=True)
    fits = fit_months(data, months)
    # 무리 나누기(누가 누구와 붙는지). 높은 쪽(무리 사이 경기로 잰 높이)을 1그룹으로 부른다
    group = ot.groups(data)
    last = fits[-1]
    gap = ot.group_gap(data, last["theta"], group)
    first = 0 if gap is None or gap[0] >= 0 else 1
    label = {first: "1그룹", 1 - first: "2그룹"}
    align(fits, group)

    # 칸 이름이 펨코와 같은 뜻이 되게 경계 위치만 맞춘다(명단 선수의 지금 펨코 티어, 그룹 안 칸 번호로)
    off = official_index(ids, last_day, ladders, index)
    card = len(ot.CARD_NAMES)
    names = {first: ot.CARD_NAMES, 1 - first: ot.NUMBER_NAMES}
    # 펨코 티어를 숫자 그룹 칸 번호로(이름으로 맞춰, 펨코에 없는 칸이 끼어도 베이비는 마지막 칸이 된다)
    num = np.array([ot.NUMBER_NAMES.index(t) if t in ot.NUMBER_NAMES else -1 for t in TIER_ORDER])
    target = {first: np.where(off < card, off, -1), 1 - first: np.where(off >= card, num[np.maximum(off, 0)], -1)}

    def make_band():
        out = {}
        for g in (0, 1):
            m = group == g
            top = anchor_top(fits, group, g, target[g], len(names[g]))
            out[g] = ot.bands(last["theta"][m], last["se"][m], last["sure"][m], top=top, most=len(names[g]))
        return out

    # 검증 상한 전 값으로 한 번 매겨 견준다(EloBoard 새 점수 방식 3-7)
    band_raw = make_band()
    tls_raw = timelines_for(fits, group, band_raw, ot.Z_UP, ot.UP_MONTHS, ot.UP_WINDOW)
    _, val_raw = validate(data, fits, group, band_raw, tls_raw, ladders, index, ids, last_day)
    for x in fits:
        x["theta_raw"] = x["theta"]
        x["theta"] = np.minimum(x["theta"], ot.verify_cap(x["f"], x["theta"]))
    band = make_band()

    # 1그룹은 갓·킹·잭·조커·스페이드, 2그룹은 숫자 0~9와 베이비(9티어는 펨코에 없다)
    def kname(g, k):
        return names[g][k]

    variants = []
    for z, m, w in VARIANTS:
        tls = timelines_for(fits, group, band, z, m, w)
        _, tot = validate(data, fits, group, band, tls, ladders, index, ids, last_day)
        stab = stability(fits, tls)
        # 승급이 늦는 정도: 마지막 달 확정 선수 중 지금 실력 칸이 도달 티어보다 위인 수
        behind = sum(1 for tl in tls.values() if tl[-1][1] == "확정" and tl[-1][2] < tl[-1][0])
        variants.append({"z": z, "months": m, "window": w, "promotions": sum(s["promotions"] for s in stab),
                         "behind": behind, "reached": tot.get("reached"), "_tls": tls})  # fmt: skip
        print(
            f"  승급 기준 오차 {z}배·{w}달 중 {m}달: 승급 {variants[-1]['promotions']}번 · 늦은 선수 {behind}명",
            flush=True,
        )
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
        theta_i, se_i = last["theta"][i], last["se"][i]
        # 칸을 매기지 않은 잠정 선수는 점수 ± 오차가 걸치는 칸 범위만 보인다
        span = [kname(g, ot.band_of(theta_i + se_i, band[g])), kname(g, ot.band_of(theta_i - se_i, band[g]))]
        # 경기가 적어도 점수 ± 오차가 한 칸 안이면 칸이 확실하므로 그 칸에 넣는다(처음 시작한 선수는 대개 베이비)
        if k is None and span[0] == span[1]:
            k = ot.band_of(theta_i - se_i, band[g])
        # 지금 칸을 받은 달과 그때 칸 점수. 보는 기간 첫 달부터 같은 칸이면 첫 달
        j = len(tl) - 1
        while j > 0 and tl[j - 1][0] == k:
            j -= 1

        players.append({
            "elo_id": pid, "name": info.get("n") or (store["players"].get(pid) or [""])[0],
            "roster": pid in index, "group": label[g], "tier": kname(g, k) if k is not None else "", "half": "",
            "now": kname(g, k_now), "status": status, "declining": bool(declining), "range": span,
            # 승급불: 이번 달 승급 조건(칸 점수 - 오차 Z_UP배가 위 경계 위)을 채웠지만 아직 3달 중 2달이 안 된 선수
            "pending": bool(k is not None and status == "확정"
                            and ot.band_of(tier_score(last, i) - ot.Z_UP * se_i, band[g]) < k),
            "score": pts(last["theta"][i]) + SCORE_BASE, "se": pts(last["se"][i]),
            "tier_score": pts(tier_score(last, i)) + SCORE_BASE,
            "since": f"{fits[j]['as_of']:%Y-%m}" if k is not None else "",
            "since_score": pts(tier_score(fits[j], i)) + SCORE_BASE,
            "capped": bool(last["theta_raw"][i] - last["theta"][i] > 1e-9),
            "games": int(last["f"]["games"][i]), "official": info.get("t") or "", "team": info.get("tm") or "",
            "_i": i, "_g": g, "_k": k if k is not None else 99,
        })  # fmt: skip
    players.sort(key=lambda p: (p["group"], p["_k"], -p["score"]))
    # 칸 안 위치: 같은 칸 선수 중 지금 실력이 위쪽 절반이면 상, 아래쪽 절반이면 하(같은 칸끼리 붙일 때 상끼리·하끼리
    # 먼저). 칸 가운데를 기준으로 하면 승급이 보수적이라 대부분 상에 몰리고 맨 위·아래 칸은 열려 있어 뜻이 없다
    # 나누는 선은 명단 선수로 긋는다(표에 보이는 선수가 반씩 갈리게). 명단 밖 선수는 그 선에 맞춰 붙인다
    cut = {}
    for p in players:
        if p["tier"] and p["roster"]:
            cut.setdefault((p["_g"], p["_k"]), []).append(p["score"])
    for p in players:
        if p["tier"]:
            top = cut.get((p["_g"], p["_k"])) or [p["score"]]  # 이미 점수 높은 순
            p["half"] = "상" if p["score"] >= top[(len(top) + 1) // 2 - 1] else "하"
    gw, gl = group[data["wi"]], group[data["li"]]
    both = (gw >= 0) & (gl >= 0)
    groups_out = []
    for g in sorted((0, 1), key=lambda g: label[g]):
        count = band[g].count
        mine = [p for p in players if p["_g"] == g]
        cross = []
        for t in TIER_ORDER:
            cnt = [sum(1 for p in mine if p["roster"] and p["status"] == "확정" and p["official"] == t and p["_k"] == k)
                   for k in range(count)]  # fmt: skip
            if sum(cnt):
                cross.append({"official": t, "counts": cnt})
        groups_out.append({
            "label": label[g], "names": [kname(g, k) for k in range(count)],
            "widths": [pts(band[g].uppers[k] - band[g].lowers[k]) for k in range(count)],
            "bounds": [pts(band[g].lowers[k]) + SCORE_BASE for k in range(count)],
            "active": len(mine), "cross": cross,
        })  # fmt: skip
    capped = []
    for p in players:
        i = p["_i"]
        over = last["theta_raw"][i] - last["theta"][i]
        if p["roster"] and over > 1e-9:
            capped.append({"name": p["name"], "group": p["group"], "official": p["official"], "tier": p["tier"],
                           "raw": pts(last["theta_raw"][i]) + SCORE_BASE, "score": p["score"], "games": p["games"]})  # fmt: skip
    capped.sort(key=lambda c: c["score"] - c["raw"])
    cohorts = [
        {"group": label[g], "year": y, "gap": pts(gap), "se": pts(se_), "games": n_}
        for g, y, gap, se_, n_ in ot.cohort_gaps(data, last["theta"], group, last_day.toordinal())
    ]
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
            "z_down": ot.Z_DOWN,
            "up_months": ot.UP_MONTHS,
            "up_window": ot.UP_WINDOW,
            "decline_months": ot.DECLINE_MONTHS,
            "decline_window": ot.DECLINE_WINDOW,
            "demote_gap": ot.DEMOTE_GAP,
            "demote_months": ot.DEMOTE_MONTHS,
            "demote_window": ot.DEMOTE_WINDOW,
            "window_years": ot.WINDOW_DAYS // 365,
            "form_in_tier": ot.FORM_IN_TIER,
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
        "cap": {"before": val_raw, "after": val_tot, "players": capped},
        "cohorts": cohorts,
        "matchups": matchups(players, last),
        "stability": stability(fits, timelines),
        "explain": explain(data, ids, group, players, last, ladders, index, last_day, band, target),
    }
    return report


def markdown(r):
    rule = r["rules"]
    lines = [
        "# 자체 티어(참고용)",
        "",
        f"- 기준일 {r['as_of']} · 경기 {r['matches']:,}판 · 그룹 사이 경기 {r['cross_share']:.2%}라 그룹마다 따로 매긴다",
        (
            f"- 티어 = 도달 티어. 처음 칸·승급 모두 오차 {rule['z_up']}배를 뺀 점수로, 승급은 "
            f"최근 {rule['up_window']}달 중 {rule['up_months']}달 · 하락세 = 지금 실력이 한 칸 넘게 아래인 게 "
            f"최근 {rule['decline_window']}달 중 {rule['decline_months']}달"
        ),
        (
            f"- 강등: 오차 {rule['z_down']}배를 더한 점수로도 {rule['demote_gap']}칸 이상 아래인 게 "
            f"최근 {rule['demote_window']}달 중 {rule['demote_months']}달이면 한 칸 · 보는 기간 강등 {sum(s['demotions'] for s in r['stability'])}번"
        ),
        "",
    ]
    for g in r["groups"]:
        lines += [
            f"## {g['label']} (활동 {g['active']}명 · 칸 {len(g['names'])}개 · 칸 폭 {min(g['widths'])}~{max(g['widths'])}점)",
            "",
        ]
        for name in g["names"]:
            here = [p for p in r["players"] if p["group"] == g["label"] and p["tier"] == name and p["roster"]]
            for half in ("상", "하"):
                lines.append(f"- **{name} {half}**: " + ", ".join(
                    f"{p['name']}({p['official'] or '?'}){'↓' if p['declining'] else ''}{'🔥' if p['pending'] else ''}"
                    for p in here if p["half"] == half
                ))  # fmt: skip
        unplaced = [p for p in r["players"] if p["group"] == g["label"] and not p["tier"] and p["roster"]]
        if unplaced:
            lines.append("- **미정(경기 적음)**: " + ", ".join(
                f"{p['name']}({p['official'] or '?'}, {p['range'][0]}~{p['range'][1]}칸, {p['games']}판)" for p in unplaced
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
    m = r["matchups"]
    lines.append("같은 칸끼리 붙일 때 70% 넘게 기운 대진: " + " · ".join(
        f"{label} {m[k]['over70']:.1%}"
        for k, label in (("tier", "도달 티어"), ("half", "상하끼리"), ("now", "지금 칸"), ("official", "펨코"))
        if k in m
    ))  # fmt: skip
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=36)
    ap.add_argument("--out", default="out/own_tier")
    args = ap.parse_args()
    store, index, ladders = load()
    report = build(store, index, ladders, args.months, load_aliases())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False)
    (out / "report.json").write_text(text, encoding="utf-8")
    (out / "report.md").write_text(markdown(report), encoding="utf-8")
    keys = [
        "elo_id",
        "name",
        "group",
        "tier",
        "half",
        "now",
        "declining",
        "pending",
        "status",
        "score",
        "se",
        "games",
        "official",
        "team",
    ]
    with open(out / "table.csv", "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(report["players"])
    print(markdown(report))
    print("OWN_TIER_JSON " + text)


if __name__ == "__main__":
    main()
