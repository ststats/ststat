from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SynergyRosterMember:
    soop_id: str
    elo_id: int | None
    nickname: str
    role: str
    affiliation: str | None
    race: str | None
    tier: str | None
    modified_at: str | None


@dataclass(frozen=True)
class MonthlyLiveStats:
    balloons: int = 0
    broadcast_seconds: int = 0
    cumulative_viewers: int = 0


@dataclass(frozen=True)
class SponsorStats:
    wins: int = 0
    losses: int = 0
