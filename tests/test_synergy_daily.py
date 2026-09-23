from models.synergy_stats import MonthlyLiveStats, SponsorStats, SynergyRosterMember
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
