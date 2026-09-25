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


def _run(monkeypatch, members, api, pending=(), linked=()):
    from jobs import sync_roster as job
    from models.roster import ExistingRosterMember, TierApiPlayer
    rows = [ExistingRosterMember(id=i + 1, soop_id=m.get('soop'), elo_name=m.get('name'), elo_id=m.get('elo'),
                                 modified_at=None) for i, m in enumerate(members)]
    players = [TierApiPlayer(soop_id=p.get('soop', ''), elo_name=p.get('name', ''), elo_id=p.get('elo'), gender=None,
                             race='테란', tier='5', affiliation=None) for p in api]
    out = {}
    monkeypatch.setattr(job, 'load_roster', lambda: rows)
    monkeypatch.setattr(job, 'load_pending_ids', lambda: set(pending))
    monkeypatch.setattr(job, 'load_linked_elo_ids', lambda: set(linked))
    monkeypatch.setattr(job, 'fetch_tier_players', lambda: players)
    def cands(c):
        if c:
            out['cands'] = c
        return len(c)
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


def test_existing_players_are_never_modified(monkeypatch):
    """EloBoard 이름이 달라도 명단 선수 정보는 건드리지 않는다(예전엔 이름 칸을 EloBoard 이름으로 덮어썼다)."""
    from jobs import sync_roster as job
    assert not hasattr(job, 'update_elo_names')
    result, out = _run(monkeypatch, [{'soop': 'a', 'name': '박쭈이', 'elo': 100}],
                       [{'soop': 'a', 'name': '쭈이', 'elo': 100}])
    assert out == {}
    assert result.records_written == 0


def test_linked_other_race_account_is_not_new(monkeypatch):
    """종족을 바꿔 생긴 새 ELO 계정을 선수에 연결해 두면 신규 인원에 다시 뜨지 않는다."""
    result, out = _run(monkeypatch, [{'soop': 'zzu', 'name': '쭈이', 'elo': 100}],
                       [{'soop': 'zzu', 'name': '쭈이', 'elo': 100}, {'soop': 'zzu', 'name': '쭈이P', 'elo': 101}],
                       linked={101})
    assert 'cands' not in out
    assert result.metadata['linked_accounts'] == 1


def test_linked_ids_empty_when_table_not_created_yet(monkeypatch):
    from repositories import roster as repo

    class Q:
        def __getattr__(self, name):
            return lambda *a, **k: self

        def execute(self):
            raise RuntimeError("Could not find the table 'public.tier_member_elo_links' in the schema cache")

    class DB:
        def table(self, name):
            return Q()
    monkeypatch.setattr(repo, "get_supabase", lambda: DB())
    assert repo.load_linked_elo_ids() == set()


def test_match_staging_skips_linked_accounts(monkeypatch):
    from repositories import eloboard as repo
    from models.eloboard import EloMatch
    written = {}

    class Q:
        def __init__(self, name):
            self.name = name

        def __getattr__(self, attr):
            return lambda *a, **k: self

        def upsert(self, payload, **k):
            written["ids"] = sorted(p["elo_id"] for p in payload)
            return self

        def execute(self):
            rows = [{"id": 1, "elo_id": 100}] if self.name == "tier_members" else []
            return type("R", (), {"data": rows})()

    class DB:
        def table(self, name):
            return Q(name)
    monkeypatch.setattr(repo, "get_supabase", lambda: DB())
    monkeypatch.setattr(repo, "load_linked_elo_ids", lambda: {101})
    m = EloMatch.from_api({"id": 9, "played_on": "2026-09-22", "participants": [
        {"player_id": 101, "name": "쭈이P", "race": "P", "result": "win"},
        {"player_id": 555, "name": "신입", "race": "T", "result": "loss"}]})
    assert repo.stage_unknown_elo_candidates([m]) == 1
    assert written["ids"] == [555]


def test_ranking_links_tier_players_by_elo_id_only():
    """이름으로는 잇지 않는다: '진땅콩.' 같은 다른 계정에 티어가 붙으면 안 된다."""
    from processors.staruniv_h2h import link_tier_players
    players = {'775': ['진땅콩', 'P'], '900': ['진땅콩.', 'T'], '5': ['노아이디', 'Z']}
    members = [{'elo_id': '775', 'nickname': '진땅콩', 'team': 'A', 'id': 1, 'tier': '7'},
               {'elo_id': '', 'nickname': '노아이디', 'team': 'B', 'id': 2, 'tier': '8'}]
    linked, missing = link_tier_players(players, members)
    assert list(linked) == ['775']
    assert missing == ['노아이디']


def test_ladder_keeps_demotions_and_every_date_in_a_cell(tmp_path):
    """'N티어 승급' 칸은 그 티어가 된 날(강등 포함). 한 칸의 날짜 여러 개도 모두 쓴다."""
    import datetime as dt
    import json
    from processors.staruniv_ranking import load_ladders, tier_at
    db = tmp_path / 'db.json'
    db.write_text(json.dumps({'tierMembers': [
        # 8 → 7(3월) → 8로 강등(6월) → 다시 7(9월)
        {'ELO ID': 1, '8티어 승급': '2025-01-01, 2025-06-01', '7티어 승급': '2025-03-01, 2025-09-01'},
        # 6 → 7로 강등(날짜 하나뿐)
        {'ELO ID': 2, '6티어 승급': '2025-01-01', '7티어 승급': '2025-05-01'},
    ]}, ensure_ascii=False), encoding='utf-8')
    players = {'1': {'t': '7'}, '2': {'t': '7'}}
    lad = load_ladders(str(db), players)
    assert lad['1'] == [('2025-01-01', '8'), ('2025-03-01', '7'), ('2025-06-01', '8'), ('2025-09-01', '7')]
    assert tier_at('1', dt.date(2025, 7, 1), lad, players) == '8'
    assert tier_at('2', dt.date(2025, 3, 1), lad, players) == '6'
    assert tier_at('2', dt.date(2025, 6, 1), lad, players) == '7'
