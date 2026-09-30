"""지난 방송통계의 스폰 승패 재정정과 월말 누락 경고."""
from datetime import date

from repositories import synergy_stats as repo


def _row(day, soop, elo, wins, losses):
    return {"stat_date": day, "soop_id": soop, "elo_id": elo, "sponsor_wins": wins, "sponsor_losses": losses}


def _setup(monkeypatch, days, matches, rows_by_day):
    published = {}
    monkeypatch.setattr(repo, "snapshot_dates_between", lambda a, b: [d for d in days if a <= d <= b])
    monkeypatch.setattr(repo, "_paged_matches", lambda a, b: [m for m in matches if a <= m["match_date"] <= b])
    full_loads = []
    def load(d, columns=repo.DAILY_SNAPSHOT_COLUMNS):
        if columns == repo.DAILY_SNAPSHOT_COLUMNS:
            full_loads.append(d)
        return [dict(r) for r in rows_by_day[d]]
    monkeypatch.setattr(repo, "load_daily_snapshot_rows", load)
    monkeypatch.setattr(repo, "upsert_daily_snapshot", lambda d, rows: published.setdefault(d, rows) and len(rows))
    published["_full_loads"] = full_loads
    return published


def test_late_match_updates_only_days_on_or_after_it(monkeypatch):
    days = ["2026-09-05", "2026-09-10", "2026-09-20"]
    matches = [
        {"match_date": "2026-09-03", "winner_elo_id": 1, "loser_elo_id": 2},
        # 월말 확정·게시 뒤 늦게 등록된 9/10 경기
        {"match_date": "2026-09-10", "winner_elo_id": 1, "loser_elo_id": 2},
    ]
    rows = {d: [_row(d, "a", 1, 1, 0), _row(d, "b", 2, 0, 1)] for d in days}
    published = _setup(monkeypatch, days, matches, rows)

    result = repo.refresh_sponsor_stats("2026-09-01", "2026-09-30")

    assert sorted(k for k in published if not k.startswith("_")) == ["2026-09-10", "2026-09-20"]
    # 전체 행은 바뀐 날만 받는다
    assert published["_full_loads"] == ["2026-09-10", "2026-09-20"]
    a = next(r for r in published["2026-09-20"] if r["soop_id"] == "a")
    assert (a["sponsor_wins"], a["sponsor_losses"]) == (2, 0)
    assert result["rows_changed"] == 4


def test_elo_id_backfill_recounts_with_the_new_id(monkeypatch):
    days = ["2026-09-10"]
    matches = [{"match_date": "2026-09-05", "winner_elo_id": 200, "loser_elo_id": 9}]
    # elo_id는 200으로 소급 수정됐지만 승패는 예전 100번 기준(3승) 그대로 남아 있다
    rows = {"2026-09-10": [_row("2026-09-10", "a", 200, 3, 0)]}
    published = _setup(monkeypatch, days, matches, rows)

    repo.refresh_sponsor_stats("2026-09-01", "2026-09-30")

    r = published["2026-09-10"][0]
    assert (r["sponsor_wins"], r["sponsor_losses"]) == (1, 0)


def test_months_are_counted_separately_and_unchanged_days_are_not_republished(monkeypatch):
    days = ["2026-08-31", "2026-09-01"]
    matches = [
        {"match_date": "2026-08-30", "winner_elo_id": 1, "loser_elo_id": 2},
        {"match_date": "2026-09-01", "winner_elo_id": 2, "loser_elo_id": 1},
    ]
    rows = {
        "2026-08-31": [_row("2026-08-31", "a", 1, 1, 0)],
        "2026-09-01": [_row("2026-09-01", "a", 1, 0, 1)],   # 9월은 월초부터 다시 센다
    }
    published = _setup(monkeypatch, days, matches, rows)
    result = repo.refresh_sponsor_stats("2026-08-01", "2026-09-30")
    assert published == {"_full_loads": []}   # 바뀐 날이 없으면 전체 행을 한 번도 받지 않는다
    assert result["days_checked"] == 2


def test_deleted_match_brings_counts_back_to_zero(monkeypatch):
    days = ["2026-09-10"]
    rows = {"2026-09-10": [_row("2026-09-10", "a", 1, 1, 0), _row("2026-09-10", "b", 2, 0, 1)]}
    published = _setup(monkeypatch, days, [], rows)        # 그 경기가 지워져 이제 경기 없음
    repo.refresh_sponsor_stats("2026-09-01", "2026-09-30")
    assert [(r["sponsor_wins"], r["sponsor_losses"]) for r in published["2026-09-10"]] == [(0, 0), (0, 0)]


def test_missing_month_end_snapshot_is_reported(monkeypatch):
    from jobs import sync_synergy_daily as job

    monkeypatch.setattr(job, "existing_snapshot_count", lambda d: 0 if d == "2026-08-31" else 5)
    monkeypatch.setattr(job, "get_month_confirmation",
                        lambda m: {"poonggo_complete": m != "2026-08-01", "sponsor_complete": m != "2026-08-01"})
    monkeypatch.setattr(job, "load_snapshot_roster", lambda d: [object()])
    monkeypatch.setattr(job, "first_snapshot_date", lambda: "2026-07-15")
    result = job._confirm_closed_months(date(2026, 9, 25))
    # 7/31은 스냅샷이 있고(5건), 8/31만 빠짐. 첫 게시(7/15) 이전 달은 보지 않는다
    assert result["missing_month_end"] == ["2026-08-31"]


def test_daily_run_refreshes_from_rescan_window_or_earliest_backfill_marker(monkeypatch):
    import datetime as dt
    from jobs import sync_synergy_daily as job
    from models.synergy_stats import MonthlyLiveStats, SynergyRosterMember

    roster = [SynergyRosterMember(soop_id=f"s{i}", elo_id=i, nickname=f"n{i}", role="", affiliation=None,
                                  race=None, tier=None, modified_at=None) for i in range(120)]
    roster[3] = SynergyRosterMember(soop_id="s3", elo_id=999, nickname="n3", role="", affiliation=None,
                                    race=None, tier=None, modified_at="2026-07-15 10:00:00")
    calls = {}
    monkeypatch.setattr(job, "load_roster_for_synergy", lambda: roster)
    monkeypatch.setattr(job, "fetch_monthly", lambda y, m, ids: {i: MonthlyLiveStats() for i in ids})
    monkeypatch.setattr(job, "aggregate_sponsor_stats", lambda a, b: {})
    monkeypatch.setattr(job, "load_poonggo_month", lambda m: {})
    monkeypatch.setattr(job, "upsert_poonggo_month", lambda m, d: len(d))
    monkeypatch.setattr(job, "upsert_daily_snapshot", lambda d, rows: len(rows))
    monkeypatch.setattr(job, "apply_roster_backfill", lambda m, d: calls.setdefault("backfill", d) and 1)
    monkeypatch.setattr(job, "refresh_sponsor_stats",
                        lambda a, b: calls.setdefault("refresh", (a, b)) and {"days_checked": 0, "days_changed": [], "rows_changed": 0})
    monkeypatch.setattr(job, "clear_modified_at", lambda m: len(m))
    monkeypatch.setattr(job, "_confirm_closed_months",
                        lambda today: {"checked": 0, "confirmed": 0, "poonggo_rows": 0, "missing_month_end": []})

    class Fixed(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 25, 12, 0, tzinfo=tz)
    monkeypatch.setattr(job, "datetime", Fixed)

    result = job.run()
    assert calls["backfill"] == "2026-07-15"
    # 재수집 범위(9/1)보다 이른 소급 수정일(7/15)부터 어제까지 다시 센다
    assert calls["refresh"] == ("2026-07-15", "2026-09-24")
    assert result.metadata["modified_at_cleared"] == 1


def test_closed_month_confirm_rejects_poonggo_drop(monkeypatch):
    """월말 확정도 이번 달과 같은 급감 방어를 거친다(빈 응답이 0으로 채워져 확정되는 것 방지)."""
    import pytest
    from jobs import sync_synergy_daily as job
    from models.synergy_stats import MonthlyLiveStats, SynergyRosterMember

    roster = [SynergyRosterMember(soop_id=f"s{i}", elo_id=i, nickname=f"n{i}", role="", affiliation=None,
                                  race=None, tier=None, modified_at=None) for i in range(10)]
    saved = {}
    monkeypatch.setattr(job, "first_snapshot_date", lambda: "2026-07-01")
    monkeypatch.setattr(job, "existing_snapshot_count", lambda d: 5)
    monkeypatch.setattr(job, "load_snapshot_roster", lambda d: roster)
    monkeypatch.setattr(job, "get_month_confirmation", lambda m: {})
    monkeypatch.setattr(job, "load_poonggo_month", lambda m: {f"s{i}": MonthlyLiveStats(balloons=1000) for i in range(10)})
    monkeypatch.setattr(job, "upsert_poonggo_month", lambda m, d: saved.setdefault("poonggo", d))
    monkeypatch.setattr(job, "aggregate_sponsor_stats", lambda a, b: {})
    monkeypatch.setattr(job, "update_closed_month_numeric_stats", lambda *a: saved.setdefault("snapshot", a))
    monkeypatch.setattr(job, "upsert_month_confirmation", lambda *a: saved.setdefault("confirm", a))

    monkeypatch.setattr(job, "fetch_monthly", lambda y, m, ids: {i: MonthlyLiveStats() for i in ids})
    with pytest.raises(RuntimeError, match="dropped"):
        job._confirm_closed_months(date(2026, 9, 5))
    assert saved == {}

    # 정상(늘어난) 응답은 그대로 확정한다
    monkeypatch.setattr(job, "fetch_monthly", lambda y, m, ids: {i: MonthlyLiveStats(balloons=1200) for i in ids})
    result = job._confirm_closed_months(date(2026, 9, 5))
    assert result["confirmed"] >= 1 and "confirm" in saved


def test_eloboard_failure_still_publishes_but_defers_month_confirmation(monkeypatch):
    """EloBoard 수집이 실패한 실행: 일별 게시는 하고 지난달 확정만 미룬다."""
    import datetime as dt
    from jobs import sync_synergy_daily as job
    from models.synergy_stats import MonthlyLiveStats, SynergyRosterMember

    roster = [SynergyRosterMember(soop_id=f"s{i}", elo_id=i, nickname=f"n{i}", role="", affiliation=None,
                                  race=None, tier=None, modified_at=None) for i in range(120)]
    calls = {}
    monkeypatch.setattr(job, "load_roster_for_synergy", lambda: roster)
    monkeypatch.setattr(job, "fetch_monthly", lambda y, m, ids: {i: MonthlyLiveStats() for i in ids})
    monkeypatch.setattr(job, "aggregate_sponsor_stats", lambda a, b: {})
    monkeypatch.setattr(job, "load_poonggo_month", lambda m: {})
    monkeypatch.setattr(job, "upsert_poonggo_month", lambda m, d: len(d))
    monkeypatch.setattr(job, "upsert_daily_snapshot", lambda d, rows: calls.setdefault("daily", len(rows)))
    monkeypatch.setattr(job, "apply_roster_backfill", lambda m, d: 0)
    monkeypatch.setattr(job, "refresh_sponsor_stats", lambda a, b: {"days_checked": 0, "days_changed": [], "rows_changed": 0})
    monkeypatch.setattr(job, "clear_modified_at", lambda m: 0)
    monkeypatch.setattr(job, "_confirm_closed_months", lambda today: calls.setdefault("confirm", True) and
                        {"checked": 1, "confirmed": 1, "poonggo_rows": 0, "missing_month_end": []})

    class Fixed(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 2, 12, 0, tzinfo=tz)
    monkeypatch.setattr(job, "datetime", Fixed)

    monkeypatch.setenv("ELOBOARD_OK", "false")
    result = job.run()
    assert calls.get("daily") == 120 and "confirm" not in calls
    assert result.metadata["eloboard_ok"] is False

    monkeypatch.setenv("ELOBOARD_OK", "true")
    job.run()
    assert calls.get("confirm") is True


def test_confirmed_months_do_not_load_the_roster(monkeypatch):
    """이미 확정된 달은 월말 명단(전체 행)을 내려받지 않는다."""
    from jobs import sync_synergy_daily as job
    loads = []
    monkeypatch.setattr(job, "first_snapshot_date", lambda: "2025-10-01")
    monkeypatch.setattr(job, "existing_snapshot_count", lambda d: 5)
    monkeypatch.setattr(job, "get_month_confirmation", lambda m: {"poonggo_complete": True, "sponsor_complete": True})
    monkeypatch.setattr(job, "load_snapshot_roster", lambda d: loads.append(d) or [object()])
    result = job._confirm_closed_months(date(2026, 9, 30))
    assert loads == [] and result["checked"] == 0
