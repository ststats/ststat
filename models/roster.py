from __future__ import annotations

from dataclasses import dataclass
from typing import Any


RACE_MAP = {
    "T": "테란",
    "Z": "저그",
    "P": "프로토스",
    "R": "랜덤",
}


@dataclass(frozen=True)
class TierApiPlayer:
    soop_id: str
    elo_name: str
    elo_id: int | None
    gender: str | None
    race: str | None
    tier: str | None
    affiliation: str | None

    @classmethod
    def from_api(cls, row: dict[str, Any], tier_label: str | None) -> "TierApiPlayer | None":
        soop_id = str(row.get("soop_id") or "").strip()
        if not soop_id:
            return None

        elo_id = row.get("player_id")
        try:
            elo_id = int(elo_id) if elo_id not in (None, "") else None
        except (TypeError, ValueError):
            elo_id = None

        division = str(row.get("division") or "").strip().lower()
        gender = "여자" if division == "women" else ("남자" if division else None)

        return cls(
            soop_id=soop_id,
            elo_name=str(row.get("name") or "").strip(),
            elo_id=elo_id,
            gender=gender,
            race=RACE_MAP.get(row.get("race"), row.get("race")),
            tier=tier_label,
            affiliation=str(row.get("college") or "").strip() or None,
        )


@dataclass(frozen=True)
class ExistingRosterMember:
    soop_id: str
    elo_name: str | None
    elo_id: int | None
    modified_at: str | None


@dataclass(frozen=True)
class RosterCandidate:
    id: str
    nickname: str
    elo_id: int | None
    gender: str | None
    race: str | None
    tier: str | None
    affiliation: str | None
    source: str = "ststat_sync_roster"
