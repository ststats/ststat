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
    monkeypatch.setattr(job, "load_match_ids_between", lambda a, b: set())
    monkeypatch.setattr(job, "delete_match_ids", lambda ids: 0)

    class FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 24, 12, 0, tzinfo=tz)
    monkeypatch.setattr(job.dt, "datetime", FixedDateTime)

    result = job.run()
    assert saved["ids"] == [90, 104, 105]
    assert result.metadata["rescan_from"] == "2026-09-01"



def _setup_sync(monkeypatch, pages, stop_at, db_window_ids, backfill=False):
    import datetime as dt
    from jobs import sync_eloboard as job
    monkeypatch.setattr(job, "fetch_page", lambda offset, delay=0: pages[offset // job.PAGE_STEP] if offset // job.PAGE_STEP < len(pages) else [])
    monkeypatch.setattr(job, "DEFAULT_DELAY", 0)
    monkeypatch.setattr(job, "FULL_BACKFILL", backfill)
    monkeypatch.setattr(job, "get_latest_match_id", lambda: stop_at)
    monkeypatch.setattr(job, "upsert_dimensions", lambda m: {"players": 0, "maps": 0, "categories": 0})
    saved = {}
    monkeypatch.setattr(job, "upsert_matches", lambda m: saved.setdefault("ids", sorted(x.elo_match_id for x in m)) and len(m))
    monkeypatch.setattr(job, "stage_unknown_elo_candidates", lambda m: 0)
    asked = {}
    def window(a, b):
        asked["range"] = (a, b)
        return set(db_window_ids)
    monkeypatch.setattr(job, "load_match_ids_between", window)
    monkeypatch.setattr(job, "delete_match_ids", lambda ids: saved.setdefault("deleted", sorted(ids)) and len(ids))

    class FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 24, 12, 0, tzinfo=tz)
    monkeypatch.setattr(job.dt, "datetime", FixedDateTime)
    return job, saved, asked


def test_deletion_only_considers_the_scanned_date_window(monkeypatch):
    """옛 ID(2112333)가 스캔에 보여도 날짜 범위 밖 경기는 지우지 않는다(2026-09-24 사고 재현)."""
    pages = [[_row(3000, "2026-09-23"), _row(2112333, "2026-09-10"), _row(10, "2026-08-20")]]
    # 날짜 범위(9/1~9/24) 안의 저장된 경기: 3000, 2112333, 그리고 EloBoard에서 사라진 2500
    job, saved, asked = _setup_sync(monkeypatch, pages, stop_at=3000, db_window_ids={3000, 2112333, 2500})
    result = job.run()
    assert asked["range"] == ("2026-09-01", "2026-09-24")
    assert saved["deleted"] == [2500]
    assert result.metadata["delete_skipped"] is None


def test_mass_deletion_is_refused(monkeypatch):
    pages = [[_row(3000, "2026-09-23")]]
    job, saved, _ = _setup_sync(monkeypatch, pages, stop_at=3000, db_window_ids={3000, *range(100, 400)})
    result = job.run()
    assert "deleted" not in saved
    assert result.metadata["delete_skipped"].startswith("too_many:")


def test_full_backfill_reads_everything_and_never_deletes(monkeypatch):
    pages = [[_row(3000, "2026-09-23"), _row(20, "2024-05-01"), _row(10, "2022-02-23")]]
    job, saved, asked = _setup_sync(monkeypatch, pages, stop_at=3000, db_window_ids={999}, backfill=True)
    result = job.run()
    assert saved["ids"] == [10, 20, 3000]
    assert "deleted" not in saved and "range" not in asked
    assert result.metadata["delete_skipped"] == "full_backfill"
