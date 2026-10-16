import pytest

from collectors import trackify
from models.synergy_stats import MonthlyLiveStats


def _page(items, more=False):
    return {"sortKey": "balloon", "periodType": "monthly", "items": items, "total": len(items), "more": more}


def test_monthly_fills_missing_ids_with_zero_and_reads_every_metric(monkeypatch):
    urls = []
    monkeypatch.setattr(trackify, "_fetch_json", lambda url: urls.append(url) or _page([
        {"broadUserId": "LIVE", "balloon": 223750, "broadTimeSec": 246700, "uniqueViewers": 302821,
         "viewership": 248792061},
        {"broadUserId": "unexpected", "balloon": 9, "broadTimeSec": 9, "uniqueViewers": 9, "viewership": 9},
    ]))

    result = trackify.fetch_monthly(2026, 10, ["live", "silent"])

    assert result == {"live": MonthlyLiveStats(223750, 246700, 302821, 248792061),
                      "silent": MonthlyLiveStats(viewership_seconds=0)}
    assert "range=monthly&date=2026-10" in urls[0] and "ids=live,silent" in urls[0]


def test_ids_are_sent_at_most_100_per_request(monkeypatch):
    monkeypatch.setattr(trackify, "SLEEP_BETWEEN_REQUESTS_SEC", 0)
    calls = []
    monkeypatch.setattr(trackify, "_fetch_json", lambda url: calls.append(url) or _page([]))

    trackify.fetch_monthly(2026, 10, [f"id{i}" for i in range(250)])

    assert len(calls) == 3
    assert all(url.split("ids=")[1].count(",") < 100 for url in calls)
    assert trackify.IDS_PER_REQUEST <= 100


@pytest.mark.parametrize("payload", [
    _page([{"broadUserId": "a", "balloon": "INVALID"}]),     # 숫자가 아님
    _page([{"broadUserId": "a", "viewership": -1}]),         # 음수
    _page(["INVALID_ROW"]),                                  # 모양이 틀린 행
    _page([], more=True),                                    # 한 쪽에 다 오지 않음
    {"status": 400, "error": "Bad Request"},                 # 오류 응답
])
def test_malformed_trackify_response_is_an_error_not_zero(monkeypatch, payload):
    monkeypatch.setattr(trackify, "_fetch_json", lambda _url: payload)
    with pytest.raises(RuntimeError):
        trackify.fetch_monthly(2026, 10, ["a", "b"])


def test_daily_job_uses_trackify_unless_switched_to_poonggo(monkeypatch):
    from collectors import poonggo
    from jobs import sync_synergy_daily as job
    used = []
    monkeypatch.setattr(poonggo, "fetch_monthly", lambda *a: used.append("poonggo") or {})
    monkeypatch.setattr(trackify, "fetch_monthly", lambda *a: used.append("trackify") or {})
    monkeypatch.setattr(job, "load_missing_broadcasts", lambda: [])

    monkeypatch.delenv("MONTHLY_SOURCE", raising=False)
    job.fetch_monthly(2026, 10, ["a"])
    monkeypatch.setenv("MONTHLY_SOURCE", "poonggo")
    job.fetch_monthly(2026, 10, ["a"])

    assert used == ["trackify", "poonggo"]


def test_daily_rows_carry_viewership_and_poonggo_leaves_it_empty():
    from models.synergy_stats import SynergyRosterMember
    from processors.synergy_daily import build_daily_rows
    roster = [SynergyRosterMember(s, None, s, "", None, None, None, None) for s in ("t", "p")]
    rows = build_daily_rows("2026-10-09", "2026-10-01", roster,
                            {"t": MonthlyLiveStats(1, 2, 3, 4), "p": MonthlyLiveStats(1, 2, 3)}, {})
    assert [r["viewership_seconds"] for r in rows] == [4, None]


def test_rate_limited_requests_wait_and_retry(monkeypatch):
    import io
    from urllib.error import HTTPError

    waits, calls = [], []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        if len(calls) <= 2:
            headers = {"Retry-After": "7"} if len(calls) == 1 else {}
            raise HTTPError(req.full_url, 429, "Too Many Requests", headers, None)
        return Resp(b'{"items": [], "more": false}')

    monkeypatch.setattr(trackify, "urlopen", fake_urlopen)
    monkeypatch.setattr(trackify.time, "sleep", waits.append)

    assert trackify._fetch_json("https://example.test") == {"items": [], "more": False}
    # 첫 429는 Retry-After(7초), 두 번째는 기본 대기(60초 × 2번째)
    assert waits == [7.0, trackify.RATE_LIMIT_WAIT_SEC * 2]


MISSING = {"id": 7, "soop_id": "long", "started_at": "2026-09-30T22:00:00", "ended_at": "2026-10-02T03:00:00",
           "avg_viewers": 10, "applied_through": None}


def test_missing_broadcast_is_added_to_each_month_for_its_part(monkeypatch):
    from jobs import sync_synergy_daily as job
    monkeypatch.delenv("MONTHLY_SOURCE", raising=False)
    monkeypatch.setattr(job, "load_missing_broadcasts", lambda: [MISSING])
    monkeypatch.setattr(trackify, "fetch_monthly", lambda y, m, ids: {
        "long": MonthlyLiveStats(5, 100, 7, 1000), "other": MonthlyLiveStats(viewership_seconds=0)})

    sep, oct_, nov = (job.fetch_monthly(2026, m, ["long", "other"]) for m in (9, 10, 11))

    assert sep["long"] == MonthlyLiveStats(5, 100 + 2 * 3600, 7, 1000 + 10 * 2 * 3600)    # 9/30 22시~자정
    assert oct_["long"] == MonthlyLiveStats(5, 100 + 27 * 3600, 7, 1000 + 10 * 27 * 3600)  # 10/1 0시~10/2 3시
    assert nov["long"] == MonthlyLiveStats(5, 100, 7, 1000)                               # 걸치지 않은 달
    assert sep["other"] == MonthlyLiveStats(viewership_seconds=0)


def test_poonggo_fallback_does_not_add_missing_broadcasts(monkeypatch):
    from collectors import poonggo
    from jobs import sync_synergy_daily as job
    monkeypatch.setenv("MONTHLY_SOURCE", "poonggo")
    monkeypatch.setattr(job, "load_missing_broadcasts", lambda: [MISSING])
    monkeypatch.setattr(poonggo, "fetch_monthly", lambda y, m, ids: {"long": MonthlyLiveStats(5, 100, 7)})
    assert job.fetch_monthly(2026, 10, ["long"])["long"] == MonthlyLiveStats(5, 100, 7)


def test_missing_broadcast_is_added_once_to_already_published_days(monkeypatch):
    from datetime import date
    from jobs import sync_synergy_daily as job
    monkeypatch.delenv("MONTHLY_SOURCE", raising=False)
    store = {d: [{"soop_id": "long", "broadcast_seconds": 100, "viewership_seconds": 1000},
                 {"soop_id": "x", "broadcast_seconds": 1, "viewership_seconds": 1}]
             for d in ("2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03")}
    marks = {}
    rows = [dict(MISSING)]
    monkeypatch.setattr(job, "load_missing_broadcasts", lambda: rows)
    monkeypatch.setattr(job, "snapshot_dates_between", lambda f, t: [d for d in sorted(store) if f <= d <= t])
    monkeypatch.setattr(job, "load_daily_snapshot_rows", lambda d: [dict(r) for r in store[d]])
    monkeypatch.setattr(job, "upsert_daily_snapshot", lambda d, new: store.__setitem__(d, new) or len(new))
    monkeypatch.setattr(job, "mark_missing_broadcast_applied",
                        lambda i, through: marks.setdefault(i, through) and rows[0].update(applied_through=through))

    assert job._apply_missing_to_published_days(date(2026, 10, 3)) == {"broadcasts": 1, "rows": 3}
    long = {d: next(r for r in store[d] if r["soop_id"] == "long") for d in store}
    assert long["2026-09-30"]["broadcast_seconds"] == 100 + 2 * 3600           # 9월 몫
    assert long["2026-10-01"]["broadcast_seconds"] == 100 + 24 * 3600          # 10/1 0시~24시
    assert long["2026-10-02"]["viewership_seconds"] == 1000 + 10 * 27 * 3600   # 10/2 3시 종료
    assert long["2026-10-03"]["broadcast_seconds"] == 100                      # 오늘 행은 게시할 때 더한다
    assert marks == {7: "2026-10-03"}

    # 다시 돌려도(다음 실행) 이미 더한 날은 건드리지 않는다
    assert job._apply_missing_to_published_days(date(2026, 10, 4))["rows"] == 0
    assert long["2026-10-02"]["viewership_seconds"] == next(r for r in store["2026-10-02"] if r["soop_id"] == "long")["viewership_seconds"]
