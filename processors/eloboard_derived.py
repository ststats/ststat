from __future__ import annotations

from collections import defaultdict
import calendar
import hashlib

from processors.staruniv_ranking import build_index, rank


# 마감 월 이력 캐시 버전. 랭킹 모델·상수가 지난달 값을 바꾸면 올린다(다른 버전 캐시는 버린다).
RANKING_HISTORY_CACHE_VERSION = 'ranking-2026-10-04-v7'


def history_cache_metadata(source: dict) -> dict:
    """닫힌 월 결과에 영향을 주는 입력만 안정적으로 지문 처리한다."""
    match_dates = [str(r.get('match_date') or '')[:10] for r in source['matches']]
    as_of = max((d for d in match_dates if d), default='')
    current_month = as_of[:7]
    digest = hashlib.sha256()
    for row in source['matches']:
        day = str(row.get('match_date') or '')[:10]
        if not day or day[:7] == current_month:
            continue
        fields = (
            row.get('elo_match_id'), day, row.get('winner_elo_id'), row.get('loser_elo_id'),
            row.get('map_id'), row.get('category_id'),
        )
        digest.update(('|'.join('' if v is None else str(v) for v in fields) + '\n').encode())
    for row in source.get('tier_members', []):
        fields = [row.get('elo_id'), row.get('tier'), row.get('tier_table_registered')]
        fields.extend(row.get(f'promoted_tier_{n}') for n in range(9))
        digest.update(('|'.join('' if v is None else str(v) for v in fields) + '\n').encode())
    for row in source.get('categories', []):
        digest.update(f"{row.get('category_id')}|{row.get('name') or ''}\n".encode())
    # 종족은 상성 항에 들어가 지난달 레이팅도 바꾸므로 지문에 넣는다
    for row in sorted(source.get('players', []), key=lambda r: int(r['elo_id'])):
        digest.update(f"p|{row.get('elo_id')}|{row.get('race') or ''}\n".encode())
    return {
        'history_cache_version': RANKING_HISTORY_CACHE_VERSION,
        'closed_history_fingerprint': digest.hexdigest(),
        'history_current_month': current_month,
    }


def compact_store(source: dict) -> dict:
    """순위 계산용으로 줄인 전적: 형식 이름 목록(cats), 선수 {id: [이름, 종족]},
    경기 행 [경기id, 날짜, 승자, 패자, 맵, 형식 번호](최신 경기가 앞). 승자·패자가 빈 경기는 뺀다."""
    categories = sorted(source['categories'], key=lambda x: int(x['category_id']))
    max_cat = max((int(x['category_id']) for x in categories), default=-1)
    cats = [''] * (max_cat + 1)
    for r in categories:
        cats[int(r['category_id'])] = str(r.get('name') or '')
    players = {
        str(r['elo_id']): [str(r.get('name') or ''), str(r.get('race') or '')]
        for r in source['players']
    }
    rows = [
        [
            int(r['elo_match_id']), str(r['match_date'])[:10], int(r['winner_elo_id']),
            int(r['loser_elo_id']), r.get('map_id'), int(r['category_id'])
        ]
        for r in source['matches']
        if r.get('winner_elo_id') is not None and r.get('loser_elo_id') is not None
    ]
    rows.sort(key=lambda x: x[0], reverse=True)
    return {'cats': cats, 'players': players, 'rows': rows}


def aggregate_source(source: dict) -> list[dict]:
    """선수별 통산 전적. 상대전적·종족별 표는 쓰는 곳이 없어 만들지 않는다."""
    player = defaultdict(lambda: {'games': 0, 'wins': 0, 'last': None})

    for m in source['matches']:
        try:
            w, l = int(m['winner_elo_id']), int(m['loser_elo_id'])
        except (TypeError, ValueError):
            continue
        day = str(m.get('match_date') or '')[:10] or None
        for me, won in ((w, 1), (l, 0)):
            ps = player[me]
            ps['games'] += 1
            ps['wins'] += won
            if day and (ps['last'] is None or day > ps['last']):
                ps['last'] = day

    return [{'elo_id': elo_id, 'total_games': s['games'], 'wins': s['wins'], 'last_match_date': s['last']}
            for elo_id, s in player.items()]


def rankings_from_index(index: dict) -> tuple[list[dict], dict]:
    meta = index.get('ranking') or {}
    as_of = meta.get('asOf')
    tier_counts = meta.get('tierCounts') or {}
    rows = []
    for pid, p in (index.get('players') or {}).items():
        if p.get('k') is None:
            continue
        periods = p.get('periodStats') or {}
        def pair(days):
            v = periods.get(str(days)) or [0, 0]
            return int(v[0] or 0), int(v[1] or 0)
        g365, w365 = pair(365)
        g90, w90 = pair(90)
        g30, w30 = pair(30)
        tier = str(p.get('t') or '') or None
        rows.append({
            'elo_id': int(pid), 'tier': tier, 'tier_rank': int(p['k']),
            'tier_count': int(tier_counts.get(str(tier), 0)),
            'raw_rating': p.get('rawRating'),
            'data_tier': p.get('dataTier'), 'tier_gap': p.get('tierGap'),
            'recent_365_games': g365, 'recent_365_wins': w365,
            'recent_90_games': g90, 'recent_90_wins': w90,
            'recent_30_games': g30, 'recent_30_wins': w30,
            'as_of': as_of,
        })
    return rows, {
        'as_of': as_of,
        'half_life_days': int(meta.get('halfLifeDays') or 0),
        'half_life_tier_days': int(meta.get('halfLifeTierDays') or 0),
        'recent_days': int(meta.get('recentDays') or 0),
        'min_recent_games': int(meta.get('minRecentGames') or 0),
        'tier_counts': tier_counts,
        'tier_levels': meta.get('tierLevels') or {},
        'race_matchup': meta.get('raceMatchup') or {},
    }


def player_ratings_from_index(index: dict) -> list[dict]:
    """순위와 상관없이 맞춘 모든 선수의 θ와 표준오차(Elo 점수 단위)."""
    as_of = (index.get('ranking') or {}).get('asOf')
    rows = []
    for pid, p in (index.get('players') or {}).items():
        if p.get('theta') is None:
            continue
        rows.append({
            'elo_id': int(pid), 'rating': p['theta'], 'rating_se': p.get('thetaSE'),
            'as_of': as_of,
        })
    return rows


def history_rows(rating: dict) -> list[dict]:
    months = rating.get('months') or []
    out = []
    for pid, vals in (rating.get('players') or {}).items():
        for month, value in zip(months, vals):
            if value is None:
                continue
            y, m = map(int, month.split('-'))
            last = calendar.monthrange(y, m)[1]
            out.append({'elo_id': int(pid), 'month_end': f'{y:04d}-{m:02d}-{last:02d}', 'rating': value})
    return out


def build_payload(source: dict, history_cache: dict | None = None) -> dict:
    if not source['matches'] or not source['players']:
        raise RuntimeError('EloBoard source tables are empty; refusing to calculate derived snapshot')
    player_stats = aggregate_source(source)
    store = compact_store(source)
    index = build_index(store, source['tier_members'])
    rating = rank(store, index, source['tier_members'], history_cache)
    rankings, ranking_meta = rankings_from_index(index)
    history = history_rows(rating)
    player_ratings = player_ratings_from_index(index)
    payload = {
        'player_stats': player_stats,
        'rankings': rankings,
        'player_ratings': player_ratings,
        'ranking_meta': ranking_meta,
        'rating_history': history,
    }
    validate_payload(source, payload)
    return payload


def validate_payload(source: dict, payload: dict):
    src_matches = len(source['matches'])
    if src_matches < 1000:
        raise RuntimeError(f'Only {src_matches} EloBoard matches loaded; refusing suspicious derived rebuild')
    if len(payload['player_stats']) < 100:
        raise RuntimeError('Derived player stats unexpectedly small')
    if not payload['rankings']:
        raise RuntimeError('Ranking algorithm produced zero ranked players')
    if len(payload['player_ratings']) < len(payload['rankings']):
        raise RuntimeError('Player ratings unexpectedly fewer than ranked players')
    if not payload['ranking_meta'].get('as_of'):
        raise RuntimeError('Ranking metadata missing as_of')
