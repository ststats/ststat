"""자체 티어(참고용): 펨코 티어표 없이 우리 경기 데이터만으로 실력을 재고 티어를 나눈다.

티어랭킹(staruniv_ranking)은 펨코 티어를 기준선 m으로 쓰지만, 여기서는 티어를 입력으로 쓰지 않는다.
  · 실력 θ = 긴 창(반감기 540일) 바탕 b + 짧은 창(90일) 폼 δ. 카드군·숫자군을 잇는 경기는 대부분 옛날 것이라
    긴 창이 두 군의 높이를, 짧은 창이 지금 폼을 맡는다(staruniv_ranking과 같은 2단, 기준선만 데이터에서).
  · 형식 가중치·종족 상성은 staruniv_ranking과 같다.
  · 티어 한 칸 = 붙으면 위 칸이 약 62% 이기는 실력 차(로짓 0.49), 그리고 그 구간 확정 선수 표준오차 중앙값의
    2배 이상. 오차가 큰 아래쪽은 칸이 넓어진다. 칸 수는 실력 폭이 정한다.
  · 이 판은 몇 년째 강등이 없다. 그래서 티어 = 도달한 티어(내리지 않음)이고, 지금 실력이 몇 칸인지와
    '하락세'(지금 실력이 한 칸 넘게 아래인 게 DECLINE_MONTHS달 연속)는 따로 보인다.
  · 내리지 않으니 잘못 올리면 굳는다. 처음 칸도 승급도 오차를 뺀 점수(θ - z·표준오차)로 정하고,
    승급은 그 점수가 위 경계를 up_months달 연속 넘을 때만 한다.
  · 최근 90일 경기가 없으면 휴면: 티어를 얼린다.
  · 카드군·숫자군처럼 서로 거의 안 붙는 두 무리는 데이터로 높이를 맞출 수 없다(검증 90일 경기 18.7만 판 중
    무리 사이 45판). 그래서 누가 누구와 붙는지로 무리를 나눠(groups) 무리마다 따로 티어를 매기고,
    무리 사이 높이 차이는 모든 기간의 무리 사이 경기로 따로 재 참고로만 적는다(group_gap).
펨코 티어는 검증에서 비교 대상으로만 쓴다.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
from scipy.optimize import minimize

from processors.staruniv_ranking import CAT_WEIGHT, DEFAULT_CAT_WEIGHT, RACE_PAIRS, accept_solution, race_code

BASE_HALF_LIFE = 540.0
FORM_HALF_LIFE = 90.0
WINDOW_DAYS = 3 * 365  # 이보다 옛 경기는 무게가 1/4 아래라 뺀다(속도)
SIGMA_BASE = 2.0  # 바탕 b의 아주 약한 prior(수치 안정·저표본 묶기)
SIGMA_FORM = 0.675  # 폼 δ prior(티어랭킹과 같다)
LAMBDA_RACE = 1e-6

DORMANT_DAYS = 90
MIN_GAMES = 20  # 최근 1년 이만큼 두어야 '확정'(아니면 잠정)
MAX_SE = 0.45  # 표준오차가 이보다 크면 잠정
TIER_STEP_WIN = 0.62  # 한 칸 위와 붙었을 때 위 칸 승률
Z_UP, UP_MONTHS = 1.5, 3  # 승급: 오차 몇 배를 뺀 점수가 몇 달 연속 위 경계를 넘어야 하나(보고서가 다른 값과 견준다)
DECLINE_MONTHS = 3
TIER_NAMES = "SABCDEFGHIJKLMN"


def logit(p):
    return math.log(p / (1 - p))


def prepare(rows, cats, players, alias=None):
    """경기 행 [id, 날짜, 승자, 패자, 맵, 형식]을 넘파이 배열로. 노드 = 선수 id.
    alias {연결 계정 id: 메인 id}: 종족 변경 등으로 생긴 다른 계정의 경기를 메인 계정 경기로 합친다
    (같은 사람끼리 붙은 경기는 뺀다)."""
    alias = alias or {}
    cat_w = [CAT_WEIGHT.get(c, DEFAULT_CAT_WEIGHT) for c in cats]
    ids, index = [], {}

    def node(pid):
        key = str(pid)
        if key not in index:
            index[key] = len(ids)
            ids.append(key)
        return index[key]

    day, wi, li, cw = [], [], [], []
    for r in rows:
        try:
            d = dt.date.fromisoformat(str(r[1])[:10]).toordinal()
        except (TypeError, ValueError):
            continue
        win, lose = alias.get(str(r[2]), str(r[2])), alias.get(str(r[3]), str(r[3]))
        if win == lose:
            continue
        day.append(d)
        wi.append(node(win))
        li.append(node(lose))
        c = r[5]
        cw.append(cat_w[c] if isinstance(c, int) and 0 <= c < len(cat_w) else DEFAULT_CAT_WEIGHT)
    race = [race_code((players.get(pid) or ["", ""])[1]) for pid in ids]
    return {
        "ids": ids,
        "day": np.array(day, dtype=np.int64),
        "wi": np.array(wi, dtype=np.int64),
        "li": np.array(li, dtype=np.int64),
        "cw": np.array(cw, dtype=np.float64),
        "race": race,
    }


def _race_index(race, wi, li):
    lookup = {}
    for k, pair in enumerate(RACE_PAIRS):
        lookup[(pair[0], pair[1])] = (k, 1.0)
        lookup[(pair[1], pair[0])] = (k, -1.0)
    idx = np.zeros(len(wi), dtype=np.int64)
    sign = np.zeros(len(wi), dtype=np.float64)
    for n, (a, b) in enumerate(zip(wi, li)):
        hit = lookup.get((race[a], race[b]))
        if hit:
            idx[n], sign[n] = hit
    return idx, sign


def fold(data, as_of: dt.date):
    """as_of(포함)까지의 경기를 (승자, 패자) 쌍으로 접는다. 바탕·폼 무게를 함께 만든다."""
    t = as_of.toordinal()
    age = t - data["day"]
    keep = (age >= 0) & (age <= WINDOW_DAYS)
    age, wi, li, cw = age[keep], data["wi"][keep], data["li"][keep], data["cw"][keep]
    n = len(data["ids"])
    key = wi * n + li
    uniq, inv = np.unique(key, return_inverse=True)
    w_base = np.bincount(inv, weights=cw * 0.5 ** (age / BASE_HALF_LIFE))
    w_form = np.bincount(inv, weights=cw * 0.5 ** (age / FORM_HALF_LIFE))
    pwi, pli = uniq // n, uniq % n
    recent = age <= 365
    games = np.bincount(wi[recent], minlength=n) + np.bincount(li[recent], minlength=n)
    total = np.bincount(wi, minlength=n) + np.bincount(li, minlength=n)
    last = np.full(n, -(10**9), dtype=np.int64)
    np.maximum.at(last, wi, t - age)
    np.maximum.at(last, li, t - age)
    ridx, rsign = _race_index(data["race"], pwi, pli)
    return {"wi": pwi, "li": pli, "w_base": w_base, "w_form": w_form, "games": games, "total": total, "last": last,
            "ridx": ridx, "rsign": rsign, "n": n, "as_of": t}  # fmt: skip


def _fit(wi, li, w, n, prior, offset, ridx=None, rsign=None):
    """승자 기준 로짓 offset + x[wi] - x[li] (+ 종족)를 맞춘다. 반환: (x, 종족 3개 또는 None)."""
    with_race = ridx is not None
    size = n + (len(RACE_PAIRS) if with_race else 0)

    def fun_grad(v):
        x = v[:n]
        d = offset + x[wi] - x[li]
        if with_race:
            d = d + rsign * v[n:][ridx]
        d = np.clip(d, -60, 60)
        resid = w / (1.0 + np.exp(d))
        g = np.zeros(size)
        np.add.at(g, wi, -resid)
        np.add.at(g, li, resid)
        f = float(np.sum(w * np.logaddexp(0.0, -d)) + 0.5 * prior * np.sum(x**2))
        g[:n] += prior * x
        if with_race:
            rv = v[n:]
            np.add.at(g[n:], ridx, -resid * rsign)
            f += 0.5 * LAMBDA_RACE * float(np.sum(rv**2))
            g[n:] += LAMBDA_RACE * rv
        return f, g

    res = minimize(fun_grad, np.zeros(size), jac=True, method="L-BFGS-B",
                   options={"maxiter": 20000, "maxfun": 40000, "ftol": 1e-14, "gtol": 1e-8})  # fmt: skip
    accept_solution(res, "자체 티어 최적화")
    return res.x[:n], (res.x[n:] if with_race else None)


def rate(folded):
    """실력 θ, 표준오차(폼 창 조건부 근사), 종족 상성, 폼 δ(θ 중 최근 90일 몫)."""
    f = folded
    n = f["n"]
    base, race = _fit(f["wi"], f["li"], f["w_base"], n, 1 / SIGMA_BASE**2, 0.0, f["ridx"], f["rsign"])
    offset = base[f["wi"]] - base[f["li"]] + f["rsign"] * race[f["ridx"]]
    form, _ = _fit(f["wi"], f["li"], f["w_form"], n, 1 / SIGMA_FORM**2, offset)
    theta = base + form
    d = np.clip(offset + form[f["wi"]] - form[f["li"]], -60, 60)
    p = 1.0 / (1.0 + np.exp(-d))
    info = f["w_form"] * p * (1 - p)
    prec = np.full(n, 1 / SIGMA_FORM**2)
    np.add.at(prec, f["wi"], info)
    np.add.at(prec, f["li"], info)
    return theta, 1 / np.sqrt(prec), race, form


def groups(data, min_games=5):
    """경기 그래프를 둘로 나눈다(정규화 라플라시안 둘째 고유벡터 부호). 반환: 선수별 0·1, 경기가 적으면 -1.
    0번 무리 = 선수가 더 많은 쪽."""
    n = len(data["ids"])
    games = np.bincount(data["wi"], minlength=n) + np.bincount(data["li"], minlength=n)
    keep = np.flatnonzero(games >= min_games)
    pos = np.full(n, -1)
    pos[keep] = np.arange(len(keep))
    a, b = pos[data["wi"]], pos[data["li"]]
    ok = (a >= 0) & (b >= 0)
    m = len(keep)
    adj = np.zeros((m, m))
    np.add.at(adj, (a[ok], b[ok]), 1.0)
    adj = adj + adj.T
    deg = adj.sum(axis=1)
    inv = 1 / np.sqrt(np.maximum(deg, 1e-12))
    lap = np.eye(m) - inv[:, None] * adj * inv[None, :]
    _, vecs = np.linalg.eigh(lap)
    side = (vecs[:, 1] * inv > 0).astype(int)
    if side.sum() * 2 > m:
        side = 1 - side
    out = np.full(n, -1)
    out[keep] = side
    # 경기가 적은 선수는 상대가 더 많이 속한 무리로
    for i in np.flatnonzero((out < 0) & (games > 0)):
        opp = np.concatenate([data["li"][data["wi"] == i], data["wi"][data["li"] == i]])
        votes = np.bincount(out[opp][out[opp] >= 0], minlength=2)
        if votes.sum():
            out[i] = int(np.argmax(votes))
    return out


def group_gap(data, theta, group):
    """무리 사이 높이 차이(1번 무리 기준 0번 무리가 얼마나 높은지, 로짓)와 표준오차, 경기 수, 경기 날짜 중앙값.
    무리마다 실력을 중앙값 0으로 맞춘 뒤 모든 기간의 무리 사이 경기로 차이 하나만 맞춘다(지금 실력을 옛 경기에도
    써서 거칠다)."""
    gw, gl = group[data["wi"]], group[data["li"]]
    cross = (gw >= 0) & (gl >= 0) & (gw != gl)
    if cross.sum() < 10:
        return None
    centered = theta.copy()
    for g in (0, 1):
        mask = group == g
        centered[mask] -= np.median(theta[mask])
    d0 = centered[data["wi"][cross]] - centered[data["li"][cross]]
    sign = np.where(gw[cross] == 0, 1.0, -1.0)  # 승자가 0번 무리면 +gap
    gap = 0.0
    for _ in range(50):  # 1차원 뉴턴
        p = 1 / (1 + np.exp(-np.clip(d0 + sign * gap, -60, 60)))
        grad = float(np.sum(sign * (1 - p)))
        hess = float(np.sum(p * (1 - p)))
        step = grad / max(hess, 1e-9)
        gap += step
        if abs(step) < 1e-9:
            break
    days = np.sort(data["day"][cross])
    return gap, 1 / math.sqrt(max(hess, 1e-9)), int(cross.sum()), dt.date.fromordinal(int(days[len(days) // 2]))


def active_mask(folded):
    return (folded["as_of"] - folded["last"]) <= DORMANT_DAYS


class Bands:
    """칸 경계. k번 칸 = [lowers[k], uppers[k]). 0번 칸(가장 높음)은 위로, 마지막 칸은 아래로 열려 있다."""

    def __init__(self, lowers, uppers):
        self.lowers, self.uppers = list(lowers), list(uppers)
        self.count = len(self.lowers)

    @classmethod
    def even(cls, top, width, count):
        return cls([top - (k + 1) * width for k in range(count)], [top - k * width for k in range(count)])


def bands(theta, se, sure):
    """확정 선수(sure)로 칸을 나눈다. 위에서부터 한 칸씩, 폭 = 승률 기준(TIER_STEP_WIN)과
    그 구간 확정 선수 표준오차 중앙값의 2배 중 큰 쪽. 아래쪽처럼 오차가 큰 구간은 칸이 넓어져
    옆 칸과 확실히 구별될 때만 나뉜다. 잠정 선수는 칸 수를 정하는 데 쓰지 않는다."""
    if sure.sum() < 10:
        sure = np.ones(len(theta), dtype=bool)
    t, e = theta[sure], se[sure]
    lo, hi = (float(x) for x in np.quantile(t, [0.005, 0.995]))
    step = logit(TIER_STEP_WIN)
    lowers, uppers, upper = [], [], hi
    while upper > lo and len(lowers) < len(TIER_NAMES):
        width = step
        for _ in range(2):  # 구간 선수로 폭을 정하고, 넓어진 구간으로 한 번 더
            inside = (t <= upper) & (t > upper - width)
            if inside.sum() >= 3:
                width = max(step, 2 * float(np.median(e[inside])))
        uppers.append(upper)
        lowers.append(upper - width)
        upper -= width
    return Bands(lowers, uppers)


def band_of(value, b):
    for k, low in enumerate(b.lowers):
        if value >= low:
            return k
    return b.count - 1


def band_bounds(k, b):
    """k번 칸의 (아래, 위) 경계."""
    return b.lowers[k], b.uppers[k]


def band_mid(k, b):
    return (b.lowers[k] + b.uppers[k]) / 2


def assign(timeline, b, z_up=Z_UP, up_months=UP_MONTHS):
    """월별 [(θ, 표준오차, 활동 여부, 확정 여부)]에 '도달 티어' 규칙을 적용한다.
    반환: 월별 (도달 칸, 상태 '확정'·'잠정'·'휴면', 지금 실력 칸, 하락세 여부).
    한 번도 확정된 적 없는 잠정 선수는 칸을 매기지 않는다(None). 경기가 적으면 점수가 가운데로 끌려오고
    오차까지 빼면 실력보다 한참 낮은 칸에 놓이기 때문이다. 이런 선수는 보고서가 범위로만 보인다."""
    tier, up, low, out = None, 0, 0, []
    for theta, se, active, sure, *_ in timeline:
        now = band_of(theta, b)
        proven = band_of(theta - z_up * se, b)
        if not active:
            out.append((tier, "휴면", now, False))
            up = low = 0
            continue
        if tier is None:
            if sure:
                tier = proven
            out.append((tier, "확정" if sure else "잠정", now, False))
            continue
        up = up + 1 if proven < tier else 0
        if up >= up_months:
            tier, up = proven, 0
        low = low + 1 if now > tier else 0
        out.append((tier, "확정" if sure else "잠정", now, low >= DECLINE_MONTHS))
    return out


def month_ends(last_day: dt.date, count: int):
    out, y, m = [], last_day.year, last_day.month
    for _ in range(count):
        end = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
        out.append(min(end, last_day))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def log_loss(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(np.log(p)))
