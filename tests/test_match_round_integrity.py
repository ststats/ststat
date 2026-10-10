from processors.match_round_integrity import audit_match_rounds


def test_clean_round():
    matches = [{
        "match_no": 1,
        "match_date": "2026-09-01",
        "opponent_team": "상대A",
        "match_format": "단판",
    }]
    rounds = [{
        "id": 10,
        "match_no": 1,
        "match_date": "2026-09-01",
        "opponent_team": "상대A",
        "match_format": "단판",
        "our_player": "A",
        "opponent_player": "B",
        "result": "승",
    }]
    report = audit_match_rounds(matches, rounds)
    assert report.error_count == 0
    assert report.expected_mirrors == 0


def test_internal_round_counts_mirror():
    matches = [{
        "match_no": 1,
        "match_date": "2026-09-01",
        "opponent_team": "내전",
        "match_format": "단판",
    }]
    rounds = [{
        "id": 10,
        "match_no": 1,
        "match_date": "2026-09-01",
        "opponent_team": "내전",
        "match_format": "단판",
        "our_player": "A",
        "opponent_player": "B",
        "result": "승",
    }]
    report = audit_match_rounds(matches, rounds)
    assert report.error_count == 0
    assert report.expected_mirrors == 1


def test_orphan_is_hard_integrity_issue():
    report = audit_match_rounds([], [{
        "id": 99,
        "match_no": 777,
        "result": "승",
    }])
    assert [x.code for x in report.issues] == ["orphan_round"]


def test_metadata_mismatch_is_reported_not_fixed():
    matches = [{
        "match_no": 1,
        "match_date": "2026-09-01",
        "opponent_team": "상대A",
        "match_format": "단판",
    }]
    rounds = [{
        "id": 10,
        "match_no": 1,
        "match_date": "2026-09-02",
        "opponent_team": "상대A",
        "match_format": "단판",
        "result": "패",
    }]
    report = audit_match_rounds(matches, rounds)
    assert "match_date_mismatch" in [x.code for x in report.issues]
