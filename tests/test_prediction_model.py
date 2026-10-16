import copy
import datetime as dt
import numpy as np
from processors import staruniv_prediction as prediction
from processors import staruniv_ranking as ranking


def fixture():
    players = {str(i): [f'p{i}', 'TZP'[(i - 1) % 3]] for i in range(1, 7)}
    rows = []
    for i in range(1, 7):
        for j in range(i + 1, 7):
            for map_id in (1, 2):
                for k in range(16):
                    # 맵 하나만 테란-저그 상성을 뒤집어, 함께 맞추는 모델이 맵 효과를 따로 잡는지 본다.
                    threshold = 12 if map_id == 1 else 4
                    a, b = (i, j) if k < threshold else (j, i)
                    rows.append([len(rows), '2026-10-01', a, b, map_id, 0])
    tiers = [
        {'elo_id': i, 'nickname': f'p{i}', 'tier': '5', 'affiliation': 'A'}
        for i in range(1, 7)
    ]
    return {'players': players, 'cats': ['college_event'], 'rows': rows}, tiers


def test_prediction_is_separate_from_ranking_and_future_results():
    store, tiers = fixture()
    index = ranking.build_index(store, tiers)
    ranking.rank(store, index, tiers, with_history=False)
    original = copy.deepcopy(index)
    model = prediction.build_prediction_model(
        store, tiers, index, [{'map_id': 1, 'name': 'A'}, {'map_id': 2, 'name': 'B'}]
    )
    assert index == original
    assert model['model_version'] == 'joint-map-v1'
    assert set(model['map_matchup']) == {'A', 'B'}
    assert any(
        abs(model['map_matchup']['A'][p] - model['map_matchup']['B'][p]) > 1
        for p in ranking.RACE_PAIRS
    )
    fit = prediction.fit_prediction(store, tiers, dt.date(2026, 10, 1))
    assert np.allclose(fit['effects'].sum(axis=1), 0, atol=1e-12)
    future = dict(store, rows=store['rows'] + [[99999, '2026-10-02', 1, 2, 1, 0]] * 40)
    after = prediction.fit_prediction(future, tiers, dt.date(2026, 10, 1))
    assert np.allclose(fit['theta'], after['theta'], atol=1e-10)


def test_snapshot_writer_keeps_rank_and_prediction_distinct():
    from unittest.mock import patch, MagicMock
    from repositories import derived_stats

    payload = {
        'player_stats': [],
        'rankings': [],
        'player_ratings': [],
        'rating_history': [],
        'ranking_meta': {'as_of': '2026-10-01'},
        'prediction_model': {'model_version': 'joint-map-v1'},
    }
    with patch.object(derived_stats, '_insert', return_value=1) as insert, patch.object(
        derived_stats, 'get_supabase', return_value=MagicMock()
    ):
        derived_stats.write_snapshot('snapshot', payload)
    assert any(
        c.args
        == (
            'elo_prediction_models',
            [{'payload': payload['prediction_model']}],
            'snapshot',
        )
        for c in insert.call_args_list
    )
