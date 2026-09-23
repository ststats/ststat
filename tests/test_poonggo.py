from models.synergy_stats import MonthlyLiveStats
from collectors import poonggo


def test_monthly_omitted_ids_are_valid_zero_stats(monkeypatch):
    monkeypatch.setattr(poonggo, "IDS_PER_REQUEST", 10)
    monkeypatch.setattr(poonggo, "_fetch_json", lambda _url: [
        {"id": "LIVE", "amt": "12", "broadTime": "34", "cview": "56"},
    ])

    result = poonggo.fetch_monthly(2026, 9, ["live", "silent"])

    assert result["live"] == MonthlyLiveStats(12, 34, 56)
    assert result["silent"] == MonthlyLiveStats()


def test_monthly_ignores_unrequested_ids(monkeypatch):
    monkeypatch.setattr(poonggo, "IDS_PER_REQUEST", 10)
    monkeypatch.setattr(poonggo, "_fetch_json", lambda _url: [
        {"id": "requested", "amt": 1, "broadTime": 2, "cview": 3},
        {"id": "unexpected", "amt": 999, "broadTime": 999, "cview": 999},
    ])

    result = poonggo.fetch_monthly(2026, 9, ["requested"])

    assert set(result) == {"requested"}
    assert result["requested"] == MonthlyLiveStats(1, 2, 3)
