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
