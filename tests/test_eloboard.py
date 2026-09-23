from models.eloboard import EloMatch


def test_match_parser_uses_result_not_participant_order():
    raw = {
        "id": 10,
        "played_on": "2026-09-22",
        "map_id": 7,
        "map_name": "Polypoid",
        "category": "스폰",
        "participants": [
            {"player_id": 2, "name": "Lose", "race": "Z", "result": "loss"},
            {"player_id": 1, "name": "Win", "race": "T", "result": "win"},
        ],
    }
    match = EloMatch.from_api(raw)
    assert match is not None
    assert match.winner.elo_id == 1
    assert match.loser.elo_id == 2
    assert match.map_id == 7


def test_match_parser_rejects_bad_participants():
    assert EloMatch.from_api({"id": 1, "played_on": "2026-09-22", "participants": []}) is None
