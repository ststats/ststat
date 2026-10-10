from models.synergy_stats import MonthlyLiveStats, SponsorStats, SynergyRosterMember
import pytest

from jobs.sync_synergy_daily import _require_poonggo_coverage
from processors.synergy_daily import build_daily_rows


def test_build_daily_rows_preserves_synergy_shape():
    roster = [SynergyRosterMember(
        soop_id="abc",
        elo_id=14,
        nickname="테스트",
        role="",
        affiliation="TEAM",
        race="프로토스",
        tier="갓",
        modified_at=None,
    )]
    rows = build_daily_rows(
        "2026-09-23",
        "2026-09-01",
        roster,
        {"abc": MonthlyLiveStats(100, 200, 300)},
        {14: SponsorStats(4, 5)},
    )
    row = rows[0]
    assert row["soop_id"] == "abc"
    assert row["balloons"] == 100
    assert row["broadcast_seconds"] == 200
    assert row["cumulative_viewers"] == 300
    assert row["sponsor_wins"] == 4
    assert row["sponsor_losses"] == 5
    assert row["affiliation"] == "TEAM"


def test_current_snapshot_uses_current_roster_metadata():
    roster = [SynergyRosterMember(
        soop_id="abc", elo_id=None, nickname="새닉", role="학생",
        affiliation="NEW", race="테란", tier="1", modified_at="2026-09-20"
    )]
    row = build_daily_rows("2026-09-23", "2026-09-01", roster, {}, {})[0]
    assert row["nickname"] == "새닉"
    assert row["affiliation"] == "NEW"
    assert row["tier"] == "1"


def test_partial_poonggo_response_is_not_published_as_zeroes():
    roster = [
        SynergyRosterMember(
            soop_id=str(i), elo_id=None, nickname=str(i), role="",
            affiliation=None, race=None, tier=None, modified_at=None,
        )
        for i in range(10)
    ]
    partial = {str(i): MonthlyLiveStats() for i in range(9)}
    with pytest.raises(RuntimeError, match="coverage too small"):
        _require_poonggo_coverage(roster, partial, "test")
