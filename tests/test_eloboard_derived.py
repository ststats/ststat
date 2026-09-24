from pathlib import Path

from processors.eloboard_derived import aggregate_source, player_ratings_from_index, rankings_from_index


def test_aggregate_source_directional_h2h_and_race():
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
    players, h2h, race = aggregate_source(source)
    p1 = next(x for x in players if x['elo_id'] == 1)
    assert (p1['total_games'], p1['wins'], p1['losses']) == (3, 2, 1)
    row = next(x for x in h2h if x['player_elo_id'] == 1 and x['opponent_elo_id'] == 2)
    assert (row['games'], row['wins'], row['losses']) == (3, 2, 1)
    rr = next(x for x in race if x['elo_id'] == 1 and x['opponent_race'] == 'Z')
    assert (rr['games'], rr['wins']) == (3, 2)


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
