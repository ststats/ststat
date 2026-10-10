"""티어 안 순위(티어랭킹)를 계산한다. eloboard_derived가 build_index()·rank()로 파생 표에 저장한다.

모델: θ = 티어 기준선 m + 티어 안 편차 δ, 가중 로지스틱 최대가능도(볼록, scipy L-BFGS).
  · 카드군(갓~스페이드)과 숫자군(0~베이비)을 잇는 경기가 통산 2,009판뿐이라 보통 Elo는 두 군의 높이가
    어긋난다(숫자군이 위로 뜬다). m을 티어 간 맞대결로 맞춰 한 사다리에 묶는다.
  · 다리 경기는 대부분 옛날 것이라 m·종족 상성은 긴 창(540일), δ는 짧은 창(90일)으로 2단에 나눠 맞춘다.
  · 순위는 티어 안에서만 매긴다. 두 군의 경계는 ±1칸 남짓 흔들려 통합 순위는 내지 않는다.
  · 미분류(체크·티어표 밖)는 실력이 전 구간에 퍼져 있어 prior를 넓게 풀고 가중 3판 미만은 다리에서 뺀다.
    좁게 묶으면 스페이드-0티어가 -232점으로 뒤집힌다(맞대결 실측 +28점, 지금 설정 +28점).
  · 같은 (승자, 패자) 쌍은 가중치를 더해 접어도 손실이 없다(37.5만 경기 → 약 4.6만 쌍).
경기 수·점수·로그손실은 측정 당시 값이다.
"""

import bisect
import datetime as dt
import re
import math

import numpy as np
from scipy.optimize import minimize

HISTORY_MONTHS = 18         # 레이팅 변화 그래프 개월 수(월별 전적 그래프와 같다)

# 티어 사다리. 스타유니브 core.js의 SITE_ORDER.tiers, 시너지 app.js의 TIER_ORDER와 같은 순서여야 한다.
TIER_ORDER = ['갓', '킹', '잭', '조커', '스페이드', '0', '1', '2', '3', '4', '5', '6', '7', '8', '베이비']
# 체크·티어표 밖 상대. 순위는 안 내지만 그 경기도 실력 정보라 노드로 넣는다.
UNRANKED = '미분류'
# 휴면은 상대로는 계산에 넣고 순위에는 올리지 않는다.
DORMANT_TEAM = '휴면'

# 형식 가중치(키는 elo_categories.name). 시계열 홀드아웃으로 정했다. 한 판당 정보는 대회가 스폰보다 많지만
# 스폰이 73%라 세게 누르면 중요경기 예측까지 나빠진다. 0.1 간격 완화안이 4개 홀드아웃에서 가장 좋았다.
CAT_WEIGHT = {
    'solo_event': 1.0,      # 개인대회
    'college_event': 1.0,   # 대학대회
    'college_war': 0.9,     # 대학대전
    'college_mini': 0.8,    # 미니대전
    'pro_league': 0.7,      # 프로리그
    'team_event': 0.7,      # CK
    'sponsored': 0.6,       # 스폰
    '': 0.6,                # 기타
}
DEFAULT_CAT_WEIGHT = 0.6

# 미래 90일 동티어 경기·최근 승급 61건으로 정했다. 70~90일은 차이가 작아(75일이 근소하게 최선) 90일을 쓴다.
HALF_LIFE_DAYS = 90.0       # 개인 폼(δ) 반감기(약 3개월)

# 짧은 창은 카드군-숫자군 다리 경기를 깎아 두 군의 높이를 흔든다(120일이면 스페이드-0티어 -117점).
# 365일~감쇠 없음은 예측력이 같아, 맞대결 실측(+28점)에 가장 가까운 540일(+28.3점)을 쓴다.
HALF_LIFE_TIER_DAYS = 540.0  # 티어 기준선(m) 반감기(약 1.5년)
RECENT_DAYS = 365           # 순위를 매길 때 보는 최근 기간
MIN_RECENT_GAMES = 10       # 이 기간에 이보다 적게 뒀으면 순위에서 빼고 '기록 없음'
# 순위는 θ 순서다. θ-1.5·표준오차로 세우면 δ 폭이 좁아 많이 둔 사람이 위로 간다(합성 데이터 16위→7위).
# 저표본은 SIGMA_DELTA와 MIN_RECENT_GAMES가 막는다. 표준오차는 데이터 티어 판정에만 쓴다.
DATA_TIER_Z = 1.50          # 데이터 티어: θ ± 이 배수×표준오차가 경계를 완전히 넘을 때만 괴리

# 전 선수 공통의 종족 상성(TZ·ZP·PT 방향 로짓 우위). 따로 맞추지 않으면 상대 종족이 치우친 선수의 δ가 흔들린다.
# 데이터는 세 상성의 합만 정하고 나머지는 δ prior가 정한다(티어 안 종족별 평균 실력이 같다고 본다).
RACES = ('T', 'Z', 'P')
RACE_PAIRS = ('TZ', 'ZP', 'PT')
LAMBDA_RACE = 1e-6          # 수치 안정용으로만 아주 약하게 묶는다

# δ prior 폭. 티어 한 칸은 로짓 약 0.36(1칸 차 승률 58.8%). 0.625~0.725 중 로그손실·승급 포착 균형이 가장 좋았다.
SIGMA_DELTA = 0.675
# 미분류는 좁게 묶으면 가짜 닻이 되므로 사실상 자유롭게 둔다.
SIGMA_UNRANKED = 3.0
# 미분류 선수 중 가중 경기 수가 이보다 적은 사람의 경기는 맞출 때 뺀다.
MIN_UNRANKED_GAMES = 3.0
# m은 데이터가 정하게 두고 수치 안정용으로만 아주 약하게 묶는다.
LAMBDA_M = 1e-6
# 인접 티어 기준선의 최소 간격. 같은 값은 허용하고 수치 오차로 인한 역전만 막는다.
MIN_TIER_GAP = 1e-8

# 로짓 → Elo식 점수 배율·기준점.
SCORE_SCALE = 400.0 / math.log(10)
SCORE_BASE = 1500.0


def tier_of(entry):
    """선수 목록(build_index)의 한 명에서 티어를 꺼낸다. 사다리에 없는 값은 전부 미분류."""
    t = str((entry or {}).get('t') or '').strip()
    return t if t in TIER_ORDER else UNRANKED


def _cell(row, key):
    # 0 같은 값도 빈 값으로 취급하지 않도록 None만 빈 문자열로 바꾼다.
    v = row.get(key)
    return '' if v is None else str(v).strip()


def tier_member_entries(tier_rows):
    """tier_members 행을 잇기용 {id(SOOP ID), nickname, elo_id, team, tier, race}로. 닉네임 없는 행은 뺀다.
    시너지 명단과 달리 휴면·FA까지 다 들어 있다(휴면은 상대로는 계산에 넣고 순위에만 안 올린다)."""
    out = []
    for r in tier_rows:
        nickname = _cell(r, 'nickname') or _cell(r, 'name')
        if not nickname:
            continue
        out.append({'id': _cell(r, 'soop_id'), 'nickname': nickname, 'elo_id': _cell(r, 'elo_id'),
                    'team': _cell(r, 'affiliation'), 'tier': _cell(r, 'tier'), 'race': _cell(r, 'race')})
    return out


def link_tier_players(players, tier_members):
    """명단을 ELO ID(메인 계정)로만 eloboard 선수에 잇는다. 반환: (linked {pid: {n,tm,s,t?}}, 못 찾은 닉네임 목록).
    EloBoard는 한 사람이 이름만 살짝 바꾼 계정을 여러 개 두어 이름으로 맞추면 엉뚱한 계정에 티어가 붙는다."""
    linked = {}
    missing = []
    for m in tier_members:
        pid = m['elo_id'] if m.get('elo_id') and m['elo_id'] in players else ''
        if not pid:
            missing.append(m['nickname'])
            continue
        # 이름은 티어표 닉네임을 쓴다 - 사이트 다른 화면과 같은 이름으로 보이게.
        linked[pid] = {'n': m['nickname'], 'tm': m['team'], 's': m['id'],
                       **({'t': m['tier']} if m['tier'] not in (None, '') else {})}
    if tier_members:
        print(f'  잇기: elo_id {len(linked)}명 · 못 찾음(ELO ID 없음·EloBoard에 없음) {len(missing)}명 (명단 {len(tier_members)}명)')
    return linked, missing


def _pid_sort_key(pid):
    try:
        return (0, int(pid))
    except (TypeError, ValueError):
        return (1, str(pid))


def build_index(store, tier_rows):
    """순위 계산 대상 선수 목록: 티어표에 이어진 선수 중 경기가 한 판이라도 있는 사람.
    store는 eloboard_derived.compact_store() 결과
    (players: {id: [이름, 종족]}, cats: [형식 코드], rows: [[경기id, 날짜, 승자, 패자, 맵, 형식 번호]]).
    반환: {'players': {pid: {n, r, tm, s, t?}}} - rank()가 여기에 순위 필드를 덧붙인다."""
    players = store.get('players') or {}
    tier_members = tier_member_entries(tier_rows)
    if not tier_members:
        raise RuntimeError('Supabase tier_members is empty; refusing incomplete ranking build')
    print(f'  명단: Supabase tier_members {len(tier_members):,}명')
    linked, missing = link_tier_players(players, tier_members)
    if not linked:
        raise RuntimeError(f'티어표 명단({len(tier_members)}명)과 이어진 EloBoard 선수가 0명입니다 - '
                           '명단의 ELO ID가 비어 있는지 확인하세요')
    games = {}
    for r in store.get('rows') or []:
        if len(r) < 6:
            continue
        for pid in (str(r[2]), str(r[3])):
            if pid in linked:
                games[pid] = games.get(pid, 0) + 1
    index_players = {}
    for pid in sorted(games, key=_pid_sort_key):
        info = players.get(pid) or ['', '']
        index_players[pid] = {'n': info[0], 'r': (info[1] if len(info) > 1 else '') or '', **linked[pid]}
    if missing:
        print(f'   ℹ️ eloboard에서 못 찾은 티어표 선수 {len(missing)}명: {", ".join(missing[:15])}'
              f'{" ..." if len(missing) > 15 else ""}')
    return {'players': index_players}


BABY = '베이비'


def _days(value):
    """'2021-07-10, 2021-08-01' 같은 칸의 날짜들(YYYY-MM-DD). 형식이 틀린 조각은 건너뛴다."""
    days = []
    for day in re.split(r'[,\s/]+', str(value or '').strip()):
        try:
            days.append(dt.date.fromisoformat(day[:10]).isoformat())
        except ValueError:
            continue
    return days


def _first_day(value):
    """칸에서 가장 이른 날짜. 없으면 ''."""
    return min(_days(value), default='')


def load_ladders(tier_rows, players):
    """promoted_tier_N 날짜로 선수별 티어 사다리 {선수id: [(날짜, 티어), ...]}(오름차순)를 만든다.
    과거 달을 오늘 티어로 재면 승급자의 과거 선이 통째로 들려 올라가기 때문이다.
    강등 날짜도 같은 칸에 적힌다. 마지막 티어가 지금 티어와 다르면 기록 안 된 변동이라 빼고 오늘 티어를 쓴다.
    유스 시절은 등록일(tier_table_registered)로만 남아, 첫 승급이 7·8티어이고 등록일이 앞서면 그 사이를 베이비로 둔다.
    """
    cols = [(str(i), f'promoted_tier_{i}') for i in range(9)]
    out = {}
    skipped_mismatch = 0
    for m in tier_rows:
        pid = str(m.get('elo_id') or '').strip()
        entry = players.get(pid)
        if not pid or entry is None:
            continue
        events = []
        for tier, col in cols:
            # 한 칸에 날짜가 여럿일 수 있다(강등 뒤 다시 그 티어가 됨: '2021-07-13, 2021-10-26')
            events.extend((day, tier) for day in _days(m.get(col)))
        if not events:
            continue
        events.sort(key=lambda e: (e[0], TIER_ORDER.index(e[1])))
        registered = _first_day(m.get('tier_table_registered'))
        if registered and registered < events[0][0] and events[0][1] in ('7', '8'):
            events.insert(0, (registered, BABY))
        # 같은 날 두 티어가 적힌 경우가 있다(티어표 첫 등재분). 더 센 쪽을 남긴다.
        merged = []
        for day, tier in events:
            if merged and merged[-1][0] == day:
                continue
            merged.append((day, tier))
        if merged[-1][1] != tier_of(entry):
            skipped_mismatch += 1
            continue
        out[pid] = merged
    demoted = sum(1 for lad in out.values()
                  if [TIER_ORDER.index(t) for _, t in lad]
                  != sorted((TIER_ORDER.index(t) for _, t in lad), reverse=True))
    print(f'   승급일 사다리 {len(out):,}명 사용(강등 포함 {demoted}명) · 지금 티어와 어긋남 {skipped_mismatch}명 제외')
    return out


def tier_at(pid, day, ladders, players):
    """day 시점의 티어. 사다리가 없으면 오늘 티어를 그대로 쓴다."""
    lad = ladders.get(pid)
    if not lad:
        return tier_of(players.get(pid))
    iso = day.isoformat()
    tier = lad[0][1]        # 첫 등재 전이면 그때 티어로 본다
    for d, t in lad:
        if d > iso:
            break
        tier = t
    return tier


def build_pairs(rows, cats, today, players, ladders, t_pos, category_weights=None):
    """경기 행을 (승자, 패자) 쌍별 가중치 합으로 접는다.
    두 단이 같은 쌍 목록을 써야 해서 ww(폼, 짧은 창)와 ww_tier(티어 간격, 긴 창)를 한 번에 만든다.

    반환: (승자 인덱스, 패자 인덱스, 폼 가중치, 티어 가중치,
           경기 당시 승자/패자 티어, 선수id 목록,
           선수별 가중 경기 수(폼/티어), 선수별 최근 경기일)
    """
    weights = category_weights or CAT_WEIGHT
    fallback_weight = weights.get('', DEFAULT_CAT_WEIGHT)
    cat_w = [weights.get(c, fallback_weight) for c in cats]
    pair_w = {}
    seen = {}            # 선수id -> 노드 번호
    order = []
    weight_sum = {}      # 노드 번호 -> 가중 경기 수
    last_day = {}        # 노드 번호 -> 최근 경기 날짜(문자열)

    def node(pid):
        key = str(pid)
        if key not in seen:
            seen[key] = len(order)
            order.append(key)
        return seen[key]

    for r in rows:
        if len(r) < 6:
            continue
        _, date, win, lose, _map, cat = r[:6]
        try:
            day = dt.date.fromisoformat(str(date)[:10])
        except ValueError:
            continue
        age = (today - day).days
        if age < 0:
            age = 0
        cw = cat_w[cat] if isinstance(cat, int) and 0 <= cat < len(cat_w) else fallback_weight
        w = cw * 0.5 ** (age / HALF_LIFE_DAYS)
        wt = cw * 0.5 ** (age / HALF_LIFE_TIER_DAYS)
        if w <= 0 and wt <= 0:
            continue
        win_key, lose_key = str(win), str(lose)
        i, j = node(win_key), node(lose_key)
        # 승급 전 경기는 당시 티어 기준선에 놓는다. 개인 편차는 같은 선수 노드에 남아
        # 과거 경기의 정보는 보존하지만, 승급 전 성적이 새 티어 기준선을 끌어올리지 않는다.
        win_tier = t_pos[tier_at(win_key, day, ladders, players)]
        lose_tier = t_pos[tier_at(lose_key, day, ladders, players)]
        pair_key = (i, j, win_tier, lose_tier)
        cur = pair_w.get(pair_key)
        if cur is None:
            pair_w[pair_key] = [w, wt]
        else:
            cur[0] += w
            cur[1] += wt
        for n in (i, j):
            s = weight_sum.get(n)
            if s is None:
                weight_sum[n] = [w, wt]
            else:
                s[0] += w
                s[1] += wt
            if str(date) > last_day.get(n, ''):
                last_day[n] = str(date)

    keys = list(pair_w.keys())
    wi = np.fromiter((k[0] for k in keys), dtype=np.int64, count=len(keys))
    li = np.fromiter((k[1] for k in keys), dtype=np.int64, count=len(keys))
    win_tier_idx = np.fromiter((k[2] for k in keys), dtype=np.int64, count=len(keys))
    lose_tier_idx = np.fromiter((k[3] for k in keys), dtype=np.int64, count=len(keys))
    ww = np.fromiter((pair_w[k][0] for k in keys), dtype=np.float64, count=len(keys))
    ww_tier = np.fromiter((pair_w[k][1] for k in keys), dtype=np.float64, count=len(keys))
    n = len(order)
    wsum = np.zeros(n)
    wsum_tier = np.zeros(n)
    for k, v in weight_sum.items():
        wsum[k], wsum_tier[k] = v
    return (wi, li, ww, ww_tier, win_tier_idx, lose_tier_idx,
            order, wsum, wsum_tier, last_day)


def race_code(value):
    """선수 종족을 T/Z/P 한 글자로. 모르면 빈 문자열."""
    text = str(value or '').strip().upper()
    if text in RACES:
        return text
    return {'테란': 'T', '저그': 'Z', '프로토스': 'P', '토스': 'P'}.get(str(value or '').strip(), '')


def race_terms(node_race, wi, li):
    """경기마다 종족 상성 파라미터 번호와 부호. 같은 종족/모르는 종족이면 부호 0.

    반환: (idx, sign) - 승자 기준 로짓 차이에 sign * race[idx]를 더한다.
    """
    lookup = {}
    for k, pair in enumerate(RACE_PAIRS):
        lookup[(pair[0], pair[1])] = (k, 1.0)
        lookup[(pair[1], pair[0])] = (k, -1.0)
    wr = [node_race[i] for i in wi]
    lr = [node_race[j] for j in li]
    idx = np.zeros(len(wi), dtype=np.int64)
    sign = np.zeros(len(wi), dtype=np.float64)
    for n, key in enumerate(zip(wr, lr)):
        hit = lookup.get(key)
        if hit:
            idx[n], sign[n] = hit
    return idx, sign


# 수렴 보고가 없어도 남은 기울기가 이 안이면 경고만 하고 쓴다(실측 최대 0.0038의 10배 넘는 여유).
# 넘거나 값이 유한하지 않으면 멈춰, 지금 게시된 스냅샷이 그대로 남는다.
MAX_UNCONVERGED_GRAD = 0.05


def accept_solution(res, label, bounds=None):
    x = np.asarray(res.x, dtype=float)
    g = np.array(res.jac, dtype=float)
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(g))):
        raise RuntimeError(f'{label}: 해에 유한하지 않은 값이 있어 결과를 쓰지 않습니다 ({res.message})')
    if res.success:
        return
    if bounds:
        # 경계에 붙은 변수는 경계 바깥쪽을 향하는 기울기가 남는 게 정상이라 뺀다(사영 기울기)
        for i, (lo, hi) in enumerate(bounds):
            if lo is not None and x[i] <= lo + 1e-9 and g[i] > 0:
                g[i] = 0.0
            if hi is not None and x[i] >= hi - 1e-9 and g[i] < 0:
                g[i] = 0.0
    worst = float(np.max(np.abs(g))) if g.size else 0.0
    if worst > MAX_UNCONVERGED_GRAD:
        raise RuntimeError(f'{label}: 수렴하지 않았고 남은 기울기 {worst:.4f}가 커서 결과를 쓰지 않습니다 ({res.message})')
    print(f'   ⚠️ {label}: 수렴 보고는 없지만 남은 기울기 {worst:.4f}로 기준 안이라 사용합니다 ({res.message})')


def fit_delta(wi, li, ww, win_tier_idx, lose_tier_idx, lam, n_players, m,
              race=None, race_idx=None, race_sign=None):
    """티어 기준선 m과 종족 상성을 고정한 채 티어 안 편차 δ만 맞춘다(2단)."""
    base_diff = m[win_tier_idx] - m[lose_tier_idx]
    if race is not None:
        base_diff = base_diff + race_sign * race[race_idx]

    def fun_grad(delta):
        d = np.clip(base_diff + delta[wi] - delta[li], -60, 60)
        f = float(np.sum(ww * np.logaddexp(0.0, -d)) + 0.5 * np.sum(lam * delta ** 2))
        resid = ww / (1.0 + np.exp(d))
        g = np.zeros(n_players)
        np.add.at(g, wi, -resid)
        np.add.at(g, li, resid)
        return f, g + lam * delta

    res = minimize(fun_grad, np.zeros(n_players), jac=True, method='L-BFGS-B',
                   options={'maxiter': 20000, 'maxfun': 40000, 'ftol': 1e-14, 'gtol': 1e-9})
    accept_solution(res, 'δ 최적화')
    return res.x


def solve_two_stage(wi, li, ww, ww_tier, win_tier_idx, lose_tier_idx,
                    current_tier_idx, lam, n, n_tiers, wsum, wsum_tier, node_race=None):
    """1단으로 티어 간격 m과 종족 상성(긴 창), 2단으로 개인 폼 δ(짧은 창)를 맞춘다.
    1단은 경기 당시 티어로 놓아 승급 전 성적이 새 기준선을 끌어올리지 않게 한다.
    2단은 지금 티어로 놓는다 - 당시 티어로 놓으면 옛 티어에서 번 δ가 새 기준선에 얹혀
    승급 직후 과대평가된다(합성 데이터 +0.28로짓 → +0.04).
    반환: (theta, standard_error, keep(짧은 창에서 남은 쌍 마스크), m, race(RACE_PAIRS 순서))
    """
    if node_race is None:
        node_race = [''] * n
    race_idx, race_sign = race_terms(node_race, wi, li)
    unranked = lam < 1.0 / SIGMA_DELTA ** 2      # prior가 넓으면 미분류
    # 판 적은 미분류는 두 단 모두에서 뺀다. 긴 창에서만 충분한 사람이 흔해 문턱은 각자의 창으로 잰다.
    thin_t = unranked & (wsum_tier < MIN_UNRANKED_GAMES)
    keep_t = ~(thin_t[wi] | thin_t[li])
    m, _, race = fit(
        wi[keep_t], li[keep_t], ww_tier[keep_t],
        win_tier_idx[keep_t], lose_tier_idx[keep_t], lam, n, n_tiers,
        race_idx=race_idx[keep_t], race_sign=race_sign[keep_t])

    thin = unranked & (wsum < MIN_UNRANKED_GAMES)
    keep = ~(thin[wi] | thin[li])
    cur_w = current_tier_idx[wi]
    cur_l = current_tier_idx[li]
    delta = fit_delta(
        wi[keep], li[keep], ww[keep], cur_w[keep], cur_l[keep], lam, n, m,
        race=race, race_idx=race_idx[keep], race_sign=race_sign[keep])
    theta = m[current_tier_idx] + delta
    match_diff = (theta[wi[keep]] - theta[li[keep]]
                  + race_sign[keep] * race[race_idx[keep]])
    standard_error = standard_errors(lam, wi[keep], li[keep], ww[keep], match_diff)
    return theta, standard_error, keep, m, race


def standard_errors(lam, wi, li, ww, match_diff):
    """선수별 표준오차 = 1 / sqrt(prior 정밀도 + Σ w·p(1-p))."""
    d = np.clip(match_diff, -60, 60)
    p_hat = 1.0 / (1.0 + np.exp(-d))
    info = ww * p_hat * (1.0 - p_hat)
    prec = lam.copy()
    np.add.at(prec, wi, info)
    np.add.at(prec, li, info)
    return 1.0 / np.sqrt(prec)


def infer_data_tier(current_index, theta, standard_error, levels, max_steps=2):
    """불확실성 구간이 인접 티어 경계를 완전히 넘을 때만 괴리로 판정한다."""
    idx = int(current_index)
    lower = float(theta) - DATA_TIER_Z * float(standard_error)
    upper = float(theta) + DATA_TIER_Z * float(standard_error)
    for _ in range(max_steps):
        if idx > 0 and lower > (levels[idx - 1] + levels[idx]) / 2.0:
            idx -= 1
            continue
        if idx + 1 < len(levels) and upper < (levels[idx] + levels[idx + 1]) / 2.0:
            idx += 1
            continue
        break
    return idx


def tier_parameters_to_levels(params, n_tiers):
    """기준점+양수 간격 파라미터를 강한 티어부터 단조 감소하는 기준선으로 바꾼다."""
    ranked_count = len(TIER_ORDER)
    anchor = float(params[0])
    gaps = np.asarray(params[1:ranked_count], dtype=np.float64)
    ranked = anchor - np.concatenate(([0.0], np.cumsum(gaps)))
    if n_tiers == ranked_count:
        return ranked
    return np.concatenate((ranked, np.asarray(params[ranked_count:n_tiers], dtype=np.float64)))


def level_gradient_to_parameters(level_gradient, n_tiers):
    """티어 기준선 기울기를 기준점+간격 파라미터 기울기로 변환한다."""
    ranked_count = len(TIER_ORDER)
    g = np.asarray(level_gradient, dtype=np.float64)
    out = np.zeros(n_tiers, dtype=np.float64)
    out[0] = np.sum(g[:ranked_count])
    for gap_index in range(1, ranked_count):
        out[gap_index] = -np.sum(g[gap_index:ranked_count])
    if n_tiers > ranked_count:
        out[ranked_count:n_tiers] = g[ranked_count:n_tiers]
    return out


def solve_at(rows, cats, as_of, players, t_pos, n_tiers, ladders):
    """as_of 시점까지의 경기로 맞춘 {선수id: 레이팅}. 가중치 기준일과 티어도 as_of 기준이다."""
    (wi, li, ww, ww_tier, win_tier_idx, lose_tier_idx,
     order, wsum, wsum_tier, last_day) = build_pairs(
        rows, cats, as_of, players, ladders, t_pos)
    if not len(ww):
        return {}
    n = len(order)
    tiers_then = [tier_at(pid, as_of, ladders, players) for pid in order]
    tier_idx = np.fromiter((t_pos[t] for t in tiers_then), dtype=np.int64, count=n)
    unranked = tier_idx == t_pos[UNRANKED]
    lam = np.where(unranked, 1.0 / SIGMA_UNRANKED ** 2, 1.0 / SIGMA_DELTA ** 2)
    node_race = [race_code((players.get(pid) or {}).get('r')) for pid in order]
    theta, _se, _keep, _m, _race = solve_two_stage(
        wi, li, ww, ww_tier, win_tier_idx, lose_tier_idx,
        tier_idx, lam, n, n_tiers, wsum, wsum_tier, node_race)

    cutoff = (as_of - dt.timedelta(days=RECENT_DAYS)).isoformat()
    out = {}
    for k, pid in enumerate(order):
        if players.get(pid) is None or tiers_then[k] == UNRANKED:
            continue
        if last_day.get(k, '') < cutoff:
            continue
        # θ를 그린다. 보수 추정을 그리면 쉬는 동안 표준오차가 커져 선이 떨어져 보인다.
        out[pid] = round(float(theta[k]) * SCORE_SCALE + SCORE_BASE, 1)
    return out


def month_ends(last_day, count):
    """마지막 경기일부터 거슬러 올라가며 각 달의 말일을 count개 만든다(오름차순)."""
    y, mth = last_day.year, last_day.month
    out = []
    for _ in range(count):
        nxt = dt.date(y + (mth == 12), 1 if mth == 12 else mth + 1, 1)
        out.append(min(nxt - dt.timedelta(days=1), last_day))
        mth -= 1
        if mth == 0:
            y, mth = y - 1, 12
    return list(reversed(out))


def build_history(rows, cats, players, t_pos, n_tiers, last_day, ladders,
                  cached_payload=None, current_scores=None):
    """달마다 그 시점까지의 경기로 다시 맞춰 '그때의 점수'를 모은다.
    마감 월은 입력 지문이 같은 이전 스냅샷(cached_payload)을, 현재 월은 current_scores를 쓴다.
    """
    # 날짜순 정렬은 다시 계산할 달이 생길 때만 한다
    dated = days = None
    months = month_ends(last_day, HISTORY_MONTHS)
    series = {}
    keys = []
    cached_months = list((cached_payload or {}).get('months') or [])
    cached_players = (cached_payload or {}).get('players') or {}
    cached_by_player = {
        str(pid): dict(zip(cached_months, values or []))
        for pid, values in cached_players.items()
    }
    current_key = last_day.strftime('%Y-%m')
    reused = recalculated = 0
    for i, end in enumerate(months):
        key = end.strftime('%Y-%m')
        keys.append(key)
        if key == current_key and current_scores is not None:
            for pid, value in current_scores.items():
                series.setdefault(pid, {})[key] = value
            reused += 1
            continue
        if key != current_key and key in cached_months:
            for pid, values in cached_by_player.items():
                value = values.get(key)
                if value is not None:
                    series.setdefault(pid, {})[key] = value
            reused += 1
            continue
        if dated is None:
            dated = sorted(rows, key=lambda r: str(r[1])[:10])
            days = [str(r[1])[:10] for r in dated]
        cut = bisect.bisect_right(days, end.isoformat())
        if cut < 100:
            continue
        scores = solve_at(dated[:cut], cats, end, players, t_pos, n_tiers, ladders)
        for pid, sc in scores.items():
            series.setdefault(pid, {})[key] = sc
        recalculated += 1
    # 쉰 달은 null로 채워 길이를 맞춘다
    out = {pid: [vals.get(k) for k in keys] for pid, vals in series.items()}
    print(f'   월별 이력: 캐시 재사용 {reused}개월 · 재계산 {recalculated}개월')
    return keys, out


def fit(wi, li, ww, win_tier_idx, lose_tier_idx, lam, n_players, n_tiers,
        race_idx=None, race_sign=None):
    """θ = m[티어] + δ와 종족 상성을 가중 로지스틱 최대가능도로 맞춘다. 반환: (m, delta, race)
    최소화: -Σ w·log sigmoid(d) + (1/2)Σ λ_i·δ_i² + (λ_m/2)Σm² + (λ_r/2)Σr², lam은 선수별 prior 정밀도(1/σ²).
    """
    n_race = len(RACE_PAIRS)
    if race_idx is None:
        race_idx = np.zeros(len(wi), dtype=np.int64)
        race_sign = np.zeros(len(wi), dtype=np.float64)

    def fun_grad(x):
        delta = x[:n_players]
        m = tier_parameters_to_levels(x[n_players:n_players + n_tiers], n_tiers)
        race = x[n_players + n_tiers:]
        d = np.clip(
            m[win_tier_idx] + delta[wi] - m[lose_tier_idx] - delta[li]
            + race_sign * race[race_idx],
            -60, 60)
        # -log sigmoid(d) = log(1 + e^-d), logaddexp로 넘침을 막는다
        f = float(np.sum(ww * np.logaddexp(0.0, -d))
                  + 0.5 * np.sum(lam * delta ** 2)
                  + 0.5 * LAMBDA_M * np.sum(m ** 2)
                  + 0.5 * LAMBDA_RACE * np.sum(race ** 2))
        # 1 - sigmoid(d) = sigmoid(-d)
        resid = ww / (1.0 + np.exp(d))
        g_theta = np.zeros(n_players)
        np.add.at(g_theta, wi, -resid)
        np.add.at(g_theta, li, resid)
        g_m = (
            np.bincount(win_tier_idx, weights=-resid, minlength=n_tiers)
            + np.bincount(lose_tier_idx, weights=resid, minlength=n_tiers)
            + LAMBDA_M * m
        )
        g_race = np.bincount(race_idx, weights=-resid * race_sign, minlength=n_race) + LAMBDA_RACE * race
        return f, np.concatenate([
            g_theta + lam * delta,
            level_gradient_to_parameters(g_m, n_tiers),
            g_race,
        ])

    # 첫 기준점과 미분류 기준점은 자유롭고, 실제 티어 사이 간격만 0 이상으로 묶는다.
    x0 = np.zeros(n_players + n_tiers + n_race)
    x0[n_players + 1:n_players + len(TIER_ORDER)] = 0.1
    bounds = [(None, None)] * (n_players + n_tiers + n_race)
    for idx in range(1, len(TIER_ORDER)):
        bounds[n_players + idx] = (MIN_TIER_GAP, None)
    res = minimize(fun_grad, x0, jac=True, method='L-BFGS-B', bounds=bounds,
                   options={'maxiter': 20000, 'maxfun': 40000, 'ftol': 1e-14, 'gtol': 1e-9})
    accept_solution(res, '티어 기준선 최적화', bounds)
    delta = res.x[:n_players].copy()
    m = tier_parameters_to_levels(res.x[n_players:n_players + n_tiers], n_tiers)
    race = res.x[n_players + n_tiers:].copy()
    # θ는 차이만 의미가 있어 티어 기준선 평균을 0에 묶는다(미분류는 평균에서 뺀다).
    m -= m[:len(TIER_ORDER)].mean()
    return m, delta, race


def rank(store, index, tier_rows, history_cache=None, with_history=True):
    """티어 안 순위를 매겨 index['players']에 순위 필드(k·rating·periodStats·dataTier·theta 등)를,
    index['ranking']에 메타를 붙인다. with_history면 월별 레이팅 이력 {asOf, months, players}를 돌려준다.
    history_cache는 지난 활성 스냅샷의 월별 이력(같은 형식) - 입력이 같은 마감 월은 다시 맞추지 않는다."""
    rows = store.get('rows') or []
    cats = store.get('cats') or []
    players = index.get('players') or {}
    if not rows or not players:
        raise RuntimeError('전적이나 선수 목록이 비어 있어 티어랭킹을 계산할 수 없습니다')

    # 기준일은 마지막 경기일이다. 오늘로 잡으면 수집이 밀렸을 때 가중치가 통째로 깎여 순위가 흔들린다.
    today = max(dt.date.fromisoformat(str(r[1])[:10]) for r in rows if len(r) > 1)

    tiers = TIER_ORDER + [UNRANKED]
    t_pos = {t: i for i, t in enumerate(tiers)}
    ladders = load_ladders(tier_rows, players)

    (wi, li, ww, ww_tier, win_tier_idx, lose_tier_idx,
     order, wsum, wsum_tier, last_day) = build_pairs(
        rows, cats, today, players, ladders, t_pos)
    n = len(order)
    tier_idx = np.fromiter(
        (t_pos[tier_of(players.get(pid))] for pid in order), dtype=np.int64, count=n)

    unranked = tier_idx == t_pos[UNRANKED]
    lam = np.where(unranked, 1.0 / SIGMA_UNRANKED ** 2, 1.0 / SIGMA_DELTA ** 2)

    # 표준오차는 '지금 얼마나 아는가'라 폼과 같은 짧은 창 기준이다.
    node_race = [race_code((players.get(pid) or {}).get('r')) for pid in order]
    theta, standard_error, keep, m, race = solve_two_stage(
        wi, li, ww, ww_tier, win_tier_idx, lose_tier_idx,
        tier_idx, lam, n, len(tiers), wsum, wsum_tier, node_race)
    dropped_pairs = int((~keep).sum())
    wi, li, ww = wi[keep], li[keep], ww[keep]
    # 맞춘 경기가 한 판이라도 남은 선수(판 적은 미분류는 빠졌다)
    fitted = np.bincount(wi, minlength=n) + np.bincount(li, minlength=n) > 0

    # 순위 문턱은 눈에 보이는 값이라 가중치 없이 최근 RECENT_DAYS 판 수로 센다.
    cutoff_day = (today - dt.timedelta(days=RECENT_DAYS)).isoformat()
    history_current_scores = {
        pid: round(float(theta[k]) * SCORE_SCALE + SCORE_BASE, 1)
        for k, pid in enumerate(order)
        if players.get(pid) is not None
        and tier_of(players.get(pid)) != UNRANKED
        and last_day.get(k, '') >= cutoff_day
    }
    recent_games = {}
    for r in rows:
        if len(r) < 6 or str(r[1])[:10] < cutoff_day:
            continue
        for pid in (str(r[2]), str(r[3])):
            recent_games[pid] = recent_games.get(pid, 0) + 1

    # 순위 대상: 티어가 있고 휴면이 아니며 최근 RECENT_DAYS 안에 MIN_RECENT_GAMES판 이상 둔 선수.
    ranked = {}          # 티어 -> [(θ, 선수id)]
    skipped_recent = skipped_thin = skipped_dormant = 0
    for k, pid in enumerate(order):
        entry = players.get(pid)
        if entry is None:
            continue                       # 티어표 밖 상대(others)
        t = tier_of(entry)
        if t == UNRANKED:
            continue
        if str(entry.get('tm') or '').strip() == DORMANT_TEAM:
            skipped_dormant += 1
            continue
        n_recent = recent_games.get(pid, 0)
        if n_recent == 0:
            skipped_recent += 1
            continue
        if n_recent < MIN_RECENT_GAMES:
            skipped_thin += 1
            continue
        ranked.setdefault(t, []).append((theta[k], pid))
        # 정렬은 반올림 전 θ로 한다
        entry['rawRating'] = round(float(theta[k]) * SCORE_SCALE + SCORE_BASE, 1)

    tier_sizes = {}
    for t, lst in ranked.items():
        lst.sort(key=lambda x: -x[0])
        tier_sizes[t] = len(lst)
        for rank, (_score, pid) in enumerate(lst, start=1):
            players[pid]['k'] = rank

    ranked_ids = {pid for lst in ranked.values() for _s, pid in lst}

    # 어드민 랭킹의 기간 필터용: 순위에 오른 선수의 최근 1년/90일/30일 [경기수, 승수].
    period_days = (365, 90, 30)
    cutoffs = {days: today - dt.timedelta(days=days - 1) for days in period_days}
    period_stats = {pid: {days: [0, 0] for days in period_days} for pid in ranked_ids}
    for r in rows:
        if len(r) < 6:
            continue
        _, date, win, lose, _map, _cat = r[:6]
        try:
            day = dt.date.fromisoformat(str(date)[:10])
        except ValueError:
            continue
        for pid, won in ((str(win), True), (str(lose), False)):
            if pid not in period_stats:
                continue
            for days in period_days:
                if day >= cutoffs[days] and day <= today:
                    period_stats[pid][days][0] += 1
                    if won:
                        period_stats[pid][days][1] += 1

    for pid in ranked_ids:
        players[pid]['periodStats'] = {str(days): period_stats[pid][days] for days in period_days}

    # 어드민 '티어 괴리' 표시용 데이터 티어. 순위에는 쓰지 않는다.
    levels = np.array([m[t_pos[t]] for t in TIER_ORDER])
    pos = {pid: k for k, pid in enumerate(order)}
    for t, lst in ranked.items():
        for _score, pid in lst:
            k = pos[pid]
            current_idx = TIER_ORDER.index(t)
            fit_idx = infer_data_tier(current_idx, theta[k], standard_error[k], levels)
            fit_t = TIER_ORDER[fit_idx]
            gap = TIER_ORDER.index(t) - TIER_ORDER.index(fit_t)
            players[pid]['dataTier'] = fit_t
            players[pid]['tierGap'] = int(gap)

    # 순위 밖 선수도 엔트리 예상승률에 실제 추정치를 쓰도록 맞춘 모든 선수의 θ·표준오차를 둔다.
    for k, pid in enumerate(order):
        entry = players.get(pid)
        if entry is None:
            continue
        if fitted[k]:
            entry['theta'] = round(float(theta[k]) * SCORE_SCALE + SCORE_BASE, 1)
            entry['thetaSE'] = round(float(standard_error[k]) * SCORE_SCALE, 1)

    index['ranking'] = {
        'asOf': today.isoformat(),
        'halfLifeDays': int(HALF_LIFE_DAYS),
        'halfLifeTierDays': int(HALF_LIFE_TIER_DAYS),
        'recentDays': RECENT_DAYS,
        'minRecentGames': MIN_RECENT_GAMES,
        # 뱃지 '3위/16명'의 분모
        'tierCounts': {t: tier_sizes[t] for t in TIER_ORDER if t in tier_sizes},
        # 화면에는 안 쓰고 간격 점검용이다
        'tierLevels': {t: round(float(m[t_pos[t]]) * SCORE_SCALE + SCORE_BASE, 1) for t in TIER_ORDER},
        # 'TZ'가 +10이면 테란이 저그에게 10점 우위
        'raceMatchup': {pair: round(float(race[k]) * SCORE_SCALE, 1) for k, pair in enumerate(RACE_PAIRS)},
    }

    total = sum(tier_sizes.values())
    print(f'✅ 티어랭킹: {total:,}명 순위 매김 '
          f'(휴면 {skipped_dormant:,}명 · 최근 {RECENT_DAYS}일 경기 없음 {skipped_recent:,}명 · '
          f'{MIN_RECENT_GAMES}판 미만 {skipped_thin:,}명)')
    print(f'   기준일 {today} · 반감기 폼 {int(HALF_LIFE_DAYS)}일 / 티어 {int(HALF_LIFE_TIER_DAYS)}일'
          f' · 쌍 {len(ww):,}개'
          f' (판 적은 미분류 제외 {dropped_pairs:,}쌍)')
    print('   종족 상성(Elo 점수): ' + ' · '.join(
        f'{pair[0]}→{pair[1]} {race[k] * SCORE_SCALE:+.1f}' for k, pair in enumerate(RACE_PAIRS)))
    print('   티어 기준선(높을수록 강함):')
    for t in TIER_ORDER:
        if t in tier_sizes:
            print(f'     {t:>4}티어 {tier_sizes[t]:4d}명  {m[t_pos[t]] * SCORE_SCALE + SCORE_BASE:7.1f}')

    # 티어표 갱신 참고용 로그. 갓·킹은 실력 순이 아니라 대회 명단이라 어긋남이 많다.
    off = []
    for t, lst in ranked.items():
        for _s, pid in lst:
            fit_t = players[pid].get('dataTier', t)
            if fit_t != t:
                off.append((TIER_ORDER.index(t) - TIER_ORDER.index(fit_t),
                            players[pid].get('n', pid), t, fit_t))
    if off:
        off.sort(key=lambda x: -abs(x[0]))
        print(f'   ℹ️ 사람이 매긴 티어와 데이터가 어긋난 선수 {len(off)}명 (상위 10명):')
        for gap, nm, t, fit_t in off[:10]:
            arrow = '↑' if gap > 0 else '↓'
            print(f'      {nm} : {t}티어 → 데이터는 {fit_t}티어 {arrow}{abs(gap)}칸')

    if not with_history:
        return None
    months, series = build_history(
        rows, cats, players, t_pos, len(tiers), today, ladders,
        history_cache or None, history_current_scores)
    print(f'✅ 레이팅 변화: {len(series):,}명 · {len(months)}개월')
    return {'asOf': today.isoformat(), 'months': months, 'players': series}
