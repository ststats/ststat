"""지난 방송통계의 스폰 승패 재정정과 월말 누락 경고."""
from datetime import date

from repositories import synergy_stats as repo


def _row(day, soop, elo, wins, losses):
    return {"stat_date": day, "soop_id": soop, "elo_id": elo, "sponsor_wins": wins, "sponsor_losses": losses}


def _setup(monkeypatch, days, matches, rows_by_day):
    published = {}
    monkeypatch.setattr(repo, "snapshot_dates_between", lambda a, b: [d for d in days if a <= d <= b])
    monkeypatch.setattr(repo, "_paged_matches", lambda a, b: [m for m in matches if a <= m["match_date"] <= b])
    monkeypatch.setattr(repo, "load_daily_snapshot_rows", lambda d: [dict(r) for r in rows_by_day[d]])
    monkeypatch.setattr(repo, "upsert_daily_snapshot", lambda d, rows: published.setdefault(d, rows) and len(rows))
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

    assert sorted(published) == ["2026-09-10", "2026-09-20"]
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
    assert published == {}
    assert result["days_checked"] == 2


def test_missing_month_end_snapshot_is_reported(monkeypatch):
    from jobs import sync_synergy_daily as job

    monkeypatch.setattr(job, "existing_snapshot_count", lambda d: 0 if d == "2026-08-31" else 5)
    monkeypatch.setattr(job, "get_month_confirmation",
                        lambda m: {"poonggo_complete": m != "2026-08-01", "sponsor_complete": m != "2026-08-01"})
    monkeypatch.setattr(job, "load_snapshot_roster", lambda d: [object()])
    result = job._confirm_closed_months(date(2026, 9, 25))
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
