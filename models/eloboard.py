from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
MIN_MATCH_DATE = "2000-01-01"


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
    def from_api(cls, row: dict[str, Any], max_date: str | None = None) -> "EloMatch | None":
        return cls.parse(row, max_date=max_date)[0]

    @classmethod
    def parse(cls, row: dict[str, Any], max_date: str | None = None) -> "tuple[EloMatch | None, str]":
        """(경기, '') 또는 (None, 거부 이유). 저장 전에 모양이 틀린 행을 걸러 이유와 함께 남긴다."""
        try:
            match_id = int(row.get("id"))
            match_date = str(row.get("played_on") or "")[:10]
        except (TypeError, ValueError):
            return None, "bad_id"
        if match_id <= 0:
            return None, "bad_id"
        if not _DATE_RE.fullmatch(match_date):
            return None, "bad_date"
        try:
            dt.date.fromisoformat(match_date)
        except ValueError:
            return None, "bad_date"
        if match_date < MIN_MATCH_DATE or (max_date and match_date > max_date):
            return None, "date_out_of_range"

        raw_parts = row.get("participants") or []
        if not isinstance(raw_parts, list) or len(raw_parts) != 2:
            return None, "bad_participants"

        winner_raw = next((p for p in raw_parts if isinstance(p, dict) and p.get("result") == "win"), None)
        loser_raw = next((p for p in raw_parts if isinstance(p, dict) and p.get("result") == "loss"), None)
        if winner_raw is None or loser_raw is None:
            return None, "bad_participants"

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
        if winner is None or loser is None or winner.elo_id <= 0 or loser.elo_id <= 0:
            return None, "bad_player_id"
        if winner.elo_id == loser.elo_id:
            return None, "same_player"

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
        ), ""
