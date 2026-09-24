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


def _row(match_id, played_on, winner=1, loser=2):
    return {
        "id": match_id, "played_on": played_on, "map_id": 7, "map_name": "Polypoid",
        "category": "sponsored",
        "participants": [
            {"player_id": winner, "name": "W", "race": "T", "result": "win"},
            {"player_id": loser, "name": "L", "race": "Z", "result": "loss"},
        ],
    }


def test_rescan_cutoff_covers_this_month_and_early_days_of_last_month():
    import datetime as dt
    from jobs.sync_eloboard import rescan_cutoff
    assert rescan_cutoff(dt.date(2026, 9, 24)) == "2026-09-01"      # 평소: 이번 달만
    assert rescan_cutoff(dt.date(2026, 9, 7)) == "2026-08-01"       # 월초 7일까지: 지난달부터
    assert rescan_cutoff(dt.date(2026, 9, 8)) == "2026-09-01"
    assert rescan_cutoff(dt.date(2026, 1, 3)) == "2025-12-01"       # 해 넘김
    # 이번 달만 읽는 설정이어도 최근 3일은 늘 다시 읽는다
    assert rescan_cutoff(dt.date(2026, 9, 2), prev_month_days=0) == "2026-08-30"


def test_sync_rereads_old_ids_restored_within_this_month(monkeypatch):
    """이미 받은 ID보다 작지만 이번 달에 복구된 경기도 다시 읽어 저장한다."""
    import datetime as dt
    from jobs import sync_eloboard as job

    pages = [[
        _row(105, "2026-09-23"),
        _row(104, "2026-09-22"),
        _row(90, "2026-09-10", winner=3, loser=4),   # 예전 수집 때 없었다가 복구된 경기
        _row(80, "2026-08-20"),                       # 이번 달 이전(월초 아님) - 다시 읽지 않는다
    ]]
    monkeypatch.setattr(job, "fetch_page", lambda offset, delay=0: pages[offset // job.PAGE_STEP] if offset // job.PAGE_STEP < len(pages) else [])
    monkeypatch.setattr(job, "DEFAULT_DELAY", 0)
    monkeypatch.setattr(job, "get_latest_match_id", lambda: 100)
    saved = {}
    monkeypatch.setattr(job, "upsert_dimensions", lambda m: {"players": 0, "maps": 0, "categories": 0})
    monkeypatch.setattr(job, "upsert_matches", lambda m: saved.setdefault("ids", sorted(x.elo_match_id for x in m)) and len(m))
    monkeypatch.setattr(job, "stage_unknown_elo_candidates", lambda m: 0)
    monkeypatch.setattr(job, "load_match_ids_from", lambda min_id: set())
    monkeypatch.setattr(job, "delete_match_ids", lambda ids: 0)

    class FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 24, 12, 0, tzinfo=tz)
    monkeypatch.setattr(job.dt, "datetime", FixedDateTime)

    result = job.run()
    assert saved["ids"] == [90, 104, 105]
    assert result.metadata["rescan_from"] == "2026-09-01"
