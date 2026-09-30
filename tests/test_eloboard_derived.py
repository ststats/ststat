
from processors.eloboard_derived import aggregate_source, player_ratings_from_index, rankings_from_index


def test_aggregate_source_player_totals():
    source = {
        'players': [
            {'elo_id': 1, 'race': 'T'},
            {'elo_id': 2, 'race': 'Z'},
        ],
        'matches': [
            {'winner_elo_id': 1, 'loser_elo_id': 2, 'match_date': '2026-09-01'},
            {'winner_elo_id': 2, 'loser_elo_id': 1, 'match_date': '2026-09-02'},
            {'winner_elo_id': 1, 'loser_elo_id': 2, 'match_date': '2026-09-03'},
        ],
    }
    players = aggregate_source(source)
    p1 = next(x for x in players if x['elo_id'] == 1)
    assert (p1['total_games'], p1['wins'], p1['losses']) == (3, 2, 1)
    assert p1['last_match_date'] == '2026-09-03'
    p2 = next(x for x in players if x['elo_id'] == 2)
    assert (p2['total_games'], p2['wins'], p2['losses']) == (3, 1, 2)


def test_unused_h2h_and_race_tables_are_not_written():
    import inspect
    from repositories import derived_stats
    src = inspect.getsource(derived_stats.write_snapshot)
    assert 'elo_h2h_stats' not in src and 'elo_race_stats' not in src
    sql = open('supabase/ststat.sql', encoding='utf-8').read()
    assert 'drop table if exists public.elo_h2h_stats;' in sql and 'drop table if exists public.elo_race_stats;' in sql
    assert 'create table if not exists public.elo_h2h_stats' not in sql


def test_rankings_from_index():
    index = {
        'ranking': {
            'asOf': '2026-09-23', 'halfLifeDays': 90, 'halfLifeTierDays': 540,
            'recentDays': 365, 'minRecentGames': 10,
            'tierCounts': {'킹': 1}, 'tierLevels': {'킹': 2100.0},
            'raceMatchup': {'TZ': 12.5, 'ZP': -3.0, 'PT': 4.0},
        },
        'players': {
            '10': {
                't': '킹', 'k': 1, 'rawRating': 2110.0, 'rating': 2050.0,
                'dataTier': '킹', 'tierGap': 0,
                'periodStats': {'365': [20, 12], '90': [10, 6], '30': [3, 2]},
            }
        },
    }
    rows, meta = rankings_from_index(index)
    assert rows[0]['elo_id'] == 10
    assert rows[0]['tier_rank'] == 1
    assert rows[0]['recent_90_wins'] == 6
    assert meta['half_life_tier_days'] == 540
    assert 'backtest' not in meta
    assert meta['race_matchup']['TZ'] == 12.5


def test_player_ratings_include_unranked_players():
    index = {
        'ranking': {'asOf': '2026-09-23'},
        'players': {
            '10': {'t': '킹', 'k': 1, 'theta': 2110.0, 'thetaSE': 40.0},
            '11': {'theta': 1720.0, 'thetaSE': 120.0},          # 순위 밖(티어 없음)
            '12': {'t': '1'},                                   # 맞춘 경기 없음
        },
    }
    rows = {r['elo_id']: r for r in player_ratings_from_index(index)}
    assert set(rows) == {10, 11}
    assert rows[11]['rating'] == 1720.0 and rows[11]['rating_se'] == 120.0
    assert rows[10]['as_of'] == '2026-09-23'


def test_build_index_keeps_linked_players_with_games():
    import pytest
    from processors.eloboard_derived import compact_store
    from processors.staruniv_ranking import build_index
    source = {
        'categories': [{'category_id': 0, 'name': 'sponsored'}],
        'players': [{'elo_id': 1, 'name': 'a', 'race': 'T'}, {'elo_id': 2, 'name': 'b', 'race': 'Z'},
                    {'elo_id': 3, 'name': 'c', 'race': 'P'}],
        'matches': [{'elo_match_id': 1, 'match_date': '2026-01-01', 'winner_elo_id': 1, 'loser_elo_id': 3,
                     'map_id': None, 'category_id': 0}],
    }
    tier_rows = [
        {'elo_id': 1, 'nickname': '에이', 'soop_id': 's1', 'tier': '3', 'affiliation': 'A'},
        {'elo_id': 2, 'nickname': '비', 'soop_id': 's2', 'tier': '4', 'affiliation': 'B'},   # 경기 없음
        {'elo_id': None, 'nickname': '아이디없음', 'tier': '5'},
    ]
    index = build_index(compact_store(source), tier_rows)
    assert index == {'players': {'1': {'n': '에이', 'r': 'T', 'tm': 'A', 's': 's1', 't': '3'}}}
    with pytest.raises(RuntimeError):
        build_index(compact_store(source), [])
