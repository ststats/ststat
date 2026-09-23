from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EloParticipant:
    elo_id: int
    name: str
    race: str | None
    result: str


@dataclass(frozen=True)
class EloMatch:
    elo_match_id: int
    match_date: str
    winner: EloParticipant
    loser: EloParticipant
    map_id: int | None
    map_name: str | None
    category: str

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "EloMatch | None":
        try:
            match_id = int(row.get("id"))
            match_date = str(row.get("played_on") or "")[:10]
        except (TypeError, ValueError):
            return None
        if not match_date:
            return None

        raw_parts = row.get("participants") or []
        if not isinstance(raw_parts, list) or len(raw_parts) != 2:
            return None

        winner_raw = next((p for p in raw_parts if isinstance(p, dict) and p.get("result") == "win"), None)
        loser_raw = next((p for p in raw_parts if isinstance(p, dict) and p.get("result") == "loss"), None)
        if winner_raw is None or loser_raw is None:
            return None

        def participant(raw: dict[str, Any]) -> EloParticipant | None:
            try:
                elo_id = int(raw.get("player_id"))
            except (TypeError, ValueError):
                return None
            return EloParticipant(
                elo_id=elo_id,
                name=str(raw.get("name") or "").strip() or str(elo_id),
                race=(str(raw.get("race") or "").strip() or None),
                result=str(raw.get("result") or ""),
            )

        winner = participant(winner_raw)
        loser = participant(loser_raw)
        if winner is None or loser is None:
            return None

        raw_map_id = row.get("map_id")
        try:
            map_id = int(raw_map_id) if raw_map_id not in (None, "") else None
        except (TypeError, ValueError):
            map_id = None

        return cls(
            elo_match_id=match_id,
            match_date=match_date,
            winner=winner,
            loser=loser,
            map_id=map_id,
            map_name=(str(row.get("map_name") or "").strip() or None),
            category=str(row.get("category") or "").strip(),
        )
