"""티어랭킹 v4: 승급 직후 과대평가 방지 · θ 순위 · 종족 상성 · 전 선수 레이팅 공개."""
from __future__ import annotations

import datetime as dt
import json
import math
import random
import subprocess
import sys
from pathlib import Path

import numpy as np

from processors.staruniv_ranking import (
    RACE_PAIRS,
    SIGMA_DELTA,
    TIER_ORDER,
    UNRANKED,
    build_pairs,
    fit_delta,
    race_code,
    race_terms,
    solve_two_stage,
)

TODAY = dt.date(2026, 9, 1)
TIERS = TIER_ORDER + [UNRANKED]
T_POS = {t: i for i, t in enumerate(TIERS)}


def _simulate(seed, true, players, schedule, days=240, per_day=12):
    """true[pid] 실력으로 경기를 만든다. schedule(day) -> 그날 추가로 둘 (x, 상대 후보) 목록."""
    rng = random.Random(seed)
    rows = []
    a = [p for p in true if players[p].get('t') == '4' and p != 'p']
    b = [p for p in true if players[p].get('t') == '5']
    mid = 0

    def game(x, y, day):
        nonlocal mid
        edge = true[x] - true[y]
        w, l = (x, y) if rng.random() < 1 / (1 + math.exp(-edge)) else (y, x)
        mid += 1
        rows.append([mid, day.isoformat(), w, l, None, 0])

    for d in range(days):
        day = TODAY - dt.timedelta(days=d)
        for _ in range(per_day):
            if rng.random() < .1:
                game(rng.choice(a), rng.choice(b), day)
            else:
                pool = a if rng.random() < .5 else b
                x, y = rng.sample(pool, 2)
                game(x, y, day)
        for x, pool in schedule(day):
            game(x, rng.choice(pool), day)
    return rows


def _fit(rows, players, ladders=None, node_race=None):
    (wi, li, ww, wwt, wt, lt, order, ws, wst, _last) = build_pairs(
        rows, ['college_event'], TODAY, players, ladders or {}, T_POS)
    n = len(order)
    tidx = np.array([T_POS[players[p]['t']] for p in order])
    lam = np.full(n, 1 / SIGMA_DELTA ** 2)
    races = None if node_race is None else [node_race[p] for p in order]
    theta, se, keep, m, race = solve_two_stage(
        wi, li, ww, wwt, wt, lt, tidx, lam, n, len(TIERS), ws, wst, races)
    # 예전 방식(2단도 경기 당시 티어) - 같은 데이터에서 비교하려고 함께 돌려 둔다
    old_delta = fit_delta(wi[keep], li[keep], ww[keep], wt[keep], lt[keep], lam, n, m)
    old_theta = m[tidx] + old_delta
    return order, theta, se, m, race, old_theta


def test_promoted_player_does_not_carry_old_tier_edge_into_new_tier():
    """1년 내내 4티어 평균 실력인데 20일 전에 5티어에서 승급한 선수."""
    rng = random.Random(7)
    true, players = {}, {}
    for i in range(30):
        true[f'a{i}'] = .36 + rng.gauss(0, .25); players[f'a{i}'] = {'t': '4'}
        true[f'b{i}'] = rng.gauss(0, .25); players[f'b{i}'] = {'t': '5'}
    true['p'] = .36; players['p'] = {'t': '4'}
    a = [k for k in true if k.startswith('a')]
    b = [k for k in true if k.startswith('b')]
    promo = TODAY - dt.timedelta(days=20)
    rows = _simulate(1, true, players,
                     lambda day: [('p', a if day >= promo else b)] * 2)
    ladders = {'p': [('2024-01-01', '5'), (promo.isoformat(), '4')]}
    order, theta, _se, m, _race, old_theta = _fit(rows, players, ladders)
    k = order.index('p')
    tier4 = [order.index(x) for x in a]

    def bias(th):
        return (th[k] - th[tier4].mean()) - (true['p'] - np.mean([true[x] for x in a]))

    # 예전 방식은 옛 티어에서 번 편차를 새 티어 위에 얹어 티어 간격만큼 부풀린다.
    # 같은 데이터·같은 m으로 비교하므로 표본 잡음이 상쇄되고 차이는 대략 티어 간격이다.
    gap = m[T_POS['4']] - m[T_POS['5']]
    assert bias(old_theta) - bias(theta) > 0.6 * gap
    assert abs(bias(theta)) < abs(bias(old_theta))


def test_race_matchup_is_recovered_and_antisymmetric():
    """테란이 저그에게 0.3 로짓 우위인 세계에서 TZ 상성을 되찾는다."""
    rng = random.Random(3)
    true, players, node_race = {}, {}, {}
    for i in range(42):
        pid = f'b{i}'
        true[pid] = rng.gauss(0, .2)
        players[pid] = {'t': '5'}
        node_race[pid] = 'TZP'[i % 3]
    # 종족 상성은 '같은 티어 안에서 종족별 평균 실력은 같다'는 가정으로만 식별된다
    # (교차 종족 조합이 3개뿐이라 데이터만으로는 세 상성의 합만 정해진다). 그 가정이
    # 맞는 세계를 만들려고 종족별 참값 평균을 0으로 맞춘다.
    for r in 'TZP':
        grp = [p for p in true if node_race[p] == r]
        mean = sum(true[p] for p in grp) / len(grp)
        for p in grp:
            true[p] -= mean
    rows = []
    for mid in range(40000):
        x, y = rng.sample(list(true), 2)
        edge = true[x] - true[y]
        pair = node_race[x] + node_race[y]
        edge += {'TZ': .3, 'ZT': -.3}.get(pair, 0.0)
        w, l = (x, y) if rng.random() < 1 / (1 + math.exp(-edge)) else (y, x)
        day = TODAY - dt.timedelta(days=rng.randrange(200))
        rows.append([mid, day.isoformat(), w, l, None, 0])
    _order, _theta, _se, _m, race, _old = _fit(rows, players, node_race=node_race)
    tz, zp, pt = (race[RACE_PAIRS.index(k)] for k in ('TZ', 'ZP', 'PT'))
    assert abs(tz - .3) < .08
    assert abs(zp) < .08 and abs(pt) < .08


def test_race_terms_sign_and_unknown_race():
    idx, sign = race_terms(['T', 'Z', 'P', ''], np.array([0, 1, 2, 0, 3]), np.array([1, 0, 0, 0, 1]))
    assert (idx[0], sign[0]) == (RACE_PAIRS.index('TZ'), 1.0)     # T가 Z를 이김
    assert (idx[1], sign[1]) == (RACE_PAIRS.index('TZ'), -1.0)    # Z가 T를 이김
    assert (idx[2], sign[2]) == (RACE_PAIRS.index('PT'), 1.0)     # P가 T를 이김
    assert sign[3] == 0.0 and sign[4] == 0.0                       # 동족전 · 종족 모름
    assert race_code('저그') == 'Z' and race_code('p') == 'P' and race_code('?') == ''


def test_main_ranks_by_theta_and_exports_every_fitted_player(tmp_path):
    """스크립트 전체를 돌려 순위가 θ 순서이고 순위 밖 선수도 θ가 나오는지 본다."""
    rng = random.Random(11)
    true = {str(100 + i): .36 + rng.gauss(0, .4) for i in range(12)}      # 4티어 12명
    true.update({str(200 + i): rng.gauss(0, .4) for i in range(12)})      # 5티어 12명
    true['300'] = .2                                                      # 티어표 밖 상대
    rows = []
    ids = list(true)
    for mid in range(6000):
        x, y = rng.sample(ids, 2)
        w, l = (x, y) if rng.random() < 1 / (1 + math.exp(-(true[x] - true[y]))) else (y, x)
        day = TODAY - dt.timedelta(days=rng.randrange(300))
        rows.append([mid, day.isoformat(), int(w), int(l), None, 0])
    store = {'cats': ['college_event'], 'rows': rows,
             'players': {pid: [f'p{pid}', 'TZP'[int(pid) % 3]] for pid in ids}}
    players = {pid: {'n': f'p{pid}', 'r': 'TZP'[int(pid) % 3],
                     **({'t': '4' if pid.startswith('1') else '5'} if pid != '300' else {})}
               for pid in ids}
    (tmp_path / 'data').mkdir()
    (tmp_path / 'docs/data/h2h').mkdir(parents=True)
    src = tmp_path / 'data/eloboard.json'
    idx = tmp_path / 'docs/data/h2h/index.json'
    src.write_text(json.dumps(store), encoding='utf-8')
    idx.write_text(json.dumps({'players': players}), encoding='utf-8')
    script = Path(__file__).resolve().parents[1] / 'processors' / 'staruniv_ranking.py'
    subprocess.run([sys.executable, str(script), '--src', str(src), '--index', str(idx),
                    '--db', str(tmp_path / 'none.json'), '--no-history'],
                   cwd=tmp_path, check=True, capture_output=True)
    out = json.loads(idx.read_text(encoding='utf-8'))
    meta = out['ranking']
    assert 'backtest' not in meta
    assert set(meta['raceMatchup']) == set(RACE_PAIRS)
    ps = out['players']
    for tier in ('4', '5'):
        ranked = sorted((p for p in ps.values() if p.get('t') == tier and p.get('k')), key=lambda p: p['k'])
        assert ranked, tier
        thetas = [p['rawRating'] for p in ranked]
        assert thetas == sorted(thetas, reverse=True)          # 순위 = θ 순서
        assert all(p['rating'] == p['rawRating'] for p in ranked)
    assert ps['300'].get('theta') is not None                  # 순위 밖 선수도 θ 공개
    assert ps['300'].get('k') is None
    assert all(p.get('thetaSE', 0) > 0 for p in ps.values() if p.get('theta') is not None)


def test_accept_solution_blocks_bad_results_and_tolerates_near_optimal():
    from types import SimpleNamespace
    import numpy as np
    import pytest
    from processors.staruniv_ranking import accept_solution

    ok = SimpleNamespace(x=np.array([1.0, 2.0]), jac=np.array([1e-5, -1e-5]), success=True, message='ok')
    accept_solution(ok, 't')
    near = SimpleNamespace(x=np.array([1.0]), jac=np.array([0.01]), success=False, message='ABNORMAL')
    accept_solution(near, 't')                                   # 기준 안: 경고만
    far = SimpleNamespace(x=np.array([1.0]), jac=np.array([0.5]), success=False, message='ABNORMAL')
    with pytest.raises(RuntimeError):
        accept_solution(far, 't')
    nan = SimpleNamespace(x=np.array([np.nan]), jac=np.array([0.0]), success=True, message='ok')
    with pytest.raises(RuntimeError):
        accept_solution(nan, 't')
    # 하한에 붙은 변수의 바깥쪽 기울기는 문제 삼지 않는다
    at_bound = SimpleNamespace(x=np.array([0.1]), jac=np.array([3.0]), success=False, message='ABNORMAL')
    accept_solution(at_bound, 't', bounds=[(0.1, None)])
