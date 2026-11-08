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
    gender: str | None = None
    birth_date: str | None = None


@dataclass(frozen=True)
class MonthlyLiveStats:
    balloons: int = 0
    broadcast_seconds: int = 0
    cumulative_viewers: int = 0
    # 뷰어십(시청자 수 × 방송 초). 트래키파이만 준다 - 풍고에서 받은 달은 None(사이트가 지표를 숨긴다)
    viewership_seconds: int | None = None


@dataclass(frozen=True)
class SponsorStats:
    wins: int = 0
    losses: int = 0
