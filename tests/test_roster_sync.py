from jobs.sync_roster import flatten_players, normalize_tier


def test_normalize_tier():
    assert normalize_tier("5티어") == "5"
    assert normalize_tier("킹") == "킹"
    assert normalize_tier(None) is None


def test_flatten_players_ignores_broken_items():
    payload = {
        "tiers": [
            {
                "label": "5티어",
                "players": [
                    {
                        "soop_id": "abc123",
                        "name": "테스트",
                        "player_id": 77,
                        "race": "Z",
                        "division": "women",
                        "college": "테스트대",
                    },
                    None,
                ],
            },
            "broken",
        ]
    }
    players = flatten_players(payload)
    assert len(players) == 1
    p = players[0]
    assert p.soop_id == "abc123"
    assert p.elo_name == "테스트"
    assert p.elo_id == 77
    assert p.race == "저그"
    assert p.gender == "여자"
    assert p.tier == "5"
    assert p.affiliation == "테스트대"


def test_flatten_players_empty_on_bad_shape():
    assert flatten_players([]) == []
    assert flatten_players({}) == []
    assert flatten_players({"tiers": "not-list"}) == []


def _run(monkeypatch, members, api, pending=()):
    from jobs import sync_roster as job
    from models.roster import ExistingRosterMember, TierApiPlayer
    rows = [ExistingRosterMember(id=i + 1, soop_id=m.get('soop'), elo_name=m.get('name'), elo_id=m.get('elo'),
                                 modified_at=None) for i, m in enumerate(members)]
    players = [TierApiPlayer(soop_id=p.get('soop', ''), elo_name=p.get('name', ''), elo_id=p.get('elo'), gender=None,
                             race='테란', tier='5', affiliation=None) for p in api]
    out = {}
    monkeypatch.setattr(job, 'load_roster', lambda: rows)
    monkeypatch.setattr(job, 'load_pending_ids', lambda: set(pending))
    monkeypatch.setattr(job, 'fetch_tier_players', lambda: players)
    def names(u):
        if u:
            out['names'] = u
        return len(u)

    def cands(c):
        if c:
            out['cands'] = c
        return len(c)
    monkeypatch.setattr(job, 'update_elo_names', names)
    monkeypatch.setattr(job, 'upsert_candidates', cands)
    monkeypatch.setattr(job, 'drop_resolved_candidates', lambda ids: 0)
    return job.run(), out


def test_wrong_soop_id_on_eloboard_is_not_a_new_player(monkeypatch):
    """EloBoard에 SOOP ID가 틀리게 적혀 있어도 ELO ID가 같으면 명단에 있는 사람이다(예전엔 매번 신규로 떴다)."""
    result, out = _run(monkeypatch, [{'soop': 'right_id', 'name': '쭈이', 'elo': 100}],
                       [{'soop': 'typo_id', 'name': '쭈이', 'elo': 100}])
    assert 'cands' not in out
    assert result.metadata['new_candidates'] == 0


def test_new_player_is_staged_once_by_elo_id(monkeypatch):
    result, out = _run(monkeypatch, [{'soop': 'a', 'elo': 1}],
                       [{'soop': 'newbie', 'name': '신입', 'elo': 555}, {'soop': 'newbie2', 'name': '신입', 'elo': 555}])
    assert [(c.id, c.elo_id, c.soop_id) for c in out['cands']] == [('elo:555', 555, 'newbie')]


def test_already_pending_elo_id_is_not_staged_again(monkeypatch):
    # 경기 기록에서 먼저 'elo:555'로 올라온 선수는 티어 목록에서 다시 올리지 않는다
    result, out = _run(monkeypatch, [{'soop': 'a', 'elo': 1}], [{'soop': 'x', 'name': '신입', 'elo': 555}],
                       pending={'elo:555'})
    assert 'cands' not in out


def test_member_without_elo_id_is_reported_not_auto_linked(monkeypatch):
    result, out = _run(monkeypatch, [{'soop': 'same', 'name': '신입', 'elo': None}],
                       [{'soop': 'same', 'name': '신입', 'elo': 777}])
    assert result.metadata['possible_links'] == [{'tier_member_id': 1, 'elo_id': 777, 'soop_id': 'same', 'elo_name': '신입'}]
    assert [c.id for c in out['cands']] == ['elo:777']
