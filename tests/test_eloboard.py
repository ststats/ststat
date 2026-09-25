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


def test_match_parser_rejects_bad_dates_and_self_matches():
    base = {"id": 5, "played_on": "2026-09-22", "participants": [
        {"player_id": 1, "result": "win"}, {"player_id": 2, "result": "loss"}]}
    assert EloMatch.parse(base, max_date="2026-09-25")[0] is not None
    assert EloMatch.parse({**base, "played_on": "INVALID"})[1] == "bad_date"
    assert EloMatch.parse({**base, "played_on": "2026-02-30"})[1] == "bad_date"
    assert EloMatch.parse({**base, "played_on": "2026-12-01"}, max_date="2026-09-25")[1] == "date_out_of_range"
    assert EloMatch.parse({**base, "id": 0})[1] == "bad_id"
    same = {**base, "participants": [{"player_id": 3, "result": "win"}, {"player_id": 3, "result": "loss"}]}
    assert EloMatch.parse(same)[1] == "same_player"


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
    monkeypatch.setattr(job, "load_previous_pending_deletes", lambda: set())

    class FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 24, 12, 0, tzinfo=tz)
    monkeypatch.setattr(job.dt, "datetime", FixedDateTime)

    result = job.run()
    assert saved["ids"] == [90, 104, 105]
    assert result.metadata["rescan_from"] == "2026-09-01"



def _setup_sync(monkeypatch, pages, stop_at, db_window_ids, backfill=False, previous_pending=()):
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
    def delete(ids):
        if not ids:
            return 0
        saved.setdefault("order", []).append(("delete", sorted(ids)))
        saved["deleted"] = sorted(ids)
        return len(ids)
    monkeypatch.setattr(job, "delete_match_ids", delete)
    monkeypatch.setattr(job, "load_previous_pending_deletes", lambda: set(previous_pending))
    monkeypatch.setattr(job, "load_match_rows", lambda ids: [{"elo_match_id": i} for i in sorted(ids)])
    monkeypatch.setattr(job, "save_deletion_backup",
                        lambda rows, run_id=None: saved.setdefault("order", []).append(("backup", [r["elo_match_id"] for r in rows])))

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
    job, saved, asked = _setup_sync(monkeypatch, pages, stop_at=3000, db_window_ids={3000, 2112333, 2500},
                                    previous_pending={2500})
    result = job.run()
    assert asked["range"] == ("2026-09-01", "2026-09-24")
    assert saved["deleted"] == [2500]
    assert result.metadata["delete_skipped"] is None
    assert result.metadata["pending_delete"] == []


def test_first_miss_is_kept_until_the_next_run(monkeypatch):
    """한 번 안 보인 경기는 지우지 않고 다음 실행으로 넘긴다(목록이 밀려 한 번 빠졌을 수 있다)."""
    pages = [[_row(3000, "2026-09-23"), _row(2900, "2026-09-10")]]
    job, saved, _ = _setup_sync(monkeypatch, pages, stop_at=3000, db_window_ids={3000, 2900, 2500, 2400},
                                previous_pending={2400, 1234})
    result = job.run()
    assert saved["deleted"] == [2400]                      # 지난번에도 안 보였던 것만
    assert result.metadata["pending_delete"] == [2500]     # 처음 안 보인 것은 다음 실행에서 판정
    assert result.metadata["matches_deleted"] == 1


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


def test_late_registered_old_match_with_bigger_id_is_saved_and_never_deleted(monkeypatch):
    """목록은 날짜순이라 지난 날짜로 늦게 등록된 경기는 뒤 페이지에 있고 ID가 더 크다.
    예전엔 첫 페이지 최대 ID(100)보다 큰 999를 건너뛰어 저장하지 않고, 삭제 대상으로까지 봤다."""
    pages = [[_row(100, "2026-09-24")], [_row(999, "2026-09-10"), _row(50, "2026-08-20")]]
    job, saved, _ = _setup_sync(monkeypatch, pages, stop_at=100, db_window_ids={100, 999},
                                previous_pending={999})
    result = job.run()
    assert 999 in saved["ids"]
    assert "deleted" not in saved
    assert result.metadata["pending_delete"] == []


def test_full_backfill_keeps_late_registered_bigger_ids(monkeypatch):
    pages = [[_row(100, "2026-09-24")], [_row(999, "2024-01-10")]]
    job, saved, _ = _setup_sync(monkeypatch, pages, stop_at=100, db_window_ids=set(), backfill=True)
    job.run()
    assert saved["ids"] == [100, 999]


def test_deleted_rows_are_recorded_before_deletion(monkeypatch):
    pages = [[_row(3000, "2026-09-23")]]
    job, saved, _ = _setup_sync(monkeypatch, pages, stop_at=3000, db_window_ids={3000, 2500},
                                previous_pending={2500})
    result = job.run()
    assert saved["deleted"] == [2500]
    assert result.metadata["deleted_rows"] == [{"elo_match_id": 2500}]


def test_invalid_rows_are_counted_with_reasons_and_not_saved(monkeypatch):
    bad = _row(2990, "INVALID")
    pages = [[_row(3000, "2026-09-23"), *[_row(2900 + i, "2026-09-20") for i in range(30)], bad]]
    job, saved, _ = _setup_sync(monkeypatch, pages, stop_at=3000, db_window_ids={3000})
    result = job.run()
    assert 2990 not in saved["ids"]
    assert {"id": 2990, "reason": "bad_date"} in result.metadata["invalid_samples"]


def test_backup_is_saved_before_deleting_and_failure_blocks_deletion(monkeypatch):
    import pytest
    pages = [[_row(3000, "2026-09-23")]]
    job, saved, _ = _setup_sync(monkeypatch, pages, stop_at=3000, db_window_ids={3000, 2500},
                                previous_pending={2500})
    job.run()
    assert saved["order"] == [("backup", [2500]), ("delete", [2500])]

    job, saved, _ = _setup_sync(monkeypatch, pages, stop_at=3000, db_window_ids={3000, 2500},
                                previous_pending={2500})
    def fail(rows, run_id=None):
        raise RuntimeError("backup write failed")
    monkeypatch.setattr(job, "save_deletion_backup", fail)
    with pytest.raises(RuntimeError, match="backup write failed"):
        job.run()
    assert "deleted" not in saved
