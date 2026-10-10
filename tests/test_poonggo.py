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


import pytest


@pytest.mark.parametrize("payload", [
    [{"id": "a", "amt": "INVALID", "broadTime": 1, "cview": 1}],   # 숫자가 아님
    [{"id": "a", "amt": -5, "broadTime": 1, "cview": 1}],          # 음수
    ["INVALID_ROW"],                                                # 모양이 틀린 행
    [{"amt": 3}],                                                   # id 없는 행
])
def test_malformed_poonggo_response_is_an_error_not_zero(monkeypatch, payload):
    monkeypatch.setattr(poonggo, "_fetch_json", lambda _url: payload)
    with pytest.raises(RuntimeError):
        poonggo.fetch_monthly(2026, 9, ["a", "b"])


def test_mass_drop_in_monthly_totals_is_held_back():
    from jobs.sync_synergy_daily import _check_poonggo_drop
    prev = {f"s{i}": MonthlyLiveStats(balloons=1000) for i in range(20)}
    ok = {**{k: MonthlyLiveStats(balloons=1200) for k in prev}, "s0": MonthlyLiveStats(balloons=10)}
    assert _check_poonggo_drop(prev, ok, "d") == 1            # 한 계정 정정은 받는다
    bad = {k: MonthlyLiveStats(balloons=0) for k in prev}     # 전원이 0으로 떨어짐
    with pytest.raises(RuntimeError):
        _check_poonggo_drop(prev, bad, "d")
