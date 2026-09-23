from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from models.roster import RosterCandidate, TierApiPlayer
from models.sync_job import JobResult
from repositories.roster import (
    load_pending_ids,
    load_roster,
    update_elo_names,
    upsert_candidates,
)

TIERS_URL = "https://eloboard.co.kr/api/tiers"
MAX_NEW_CANDIDATES_PER_RUN = 100
HTTP_TIMEOUT_SECONDS = 30


def normalize_tier(label):
    if isinstance(label, str) and label.endswith("티어"):
        return label[: -len("티어")].strip()
    return str(label).strip() if label not in (None, "") else None


def flatten_players(api_data) -> list[TierApiPlayer]:
    players: list[TierApiPlayer] = []
    if not isinstance(api_data, dict):
        return players

    tiers = api_data.get("tiers")
    if not isinstance(tiers, list):
        return players

    for tier_obj in tiers:
        if not isinstance(tier_obj, dict):
            continue
        tier_label = normalize_tier(tier_obj.get("label"))
        raw_players = tier_obj.get("players")
        if not isinstance(raw_players, list):
            continue
        for raw in raw_players:
            if not isinstance(raw, dict):
                continue
            player = TierApiPlayer.from_api(raw, tier_label)
            if player:
                players.append(player)
    return players


def fetch_tier_players() -> list[TierApiPlayer]:
    req = Request(
        TIERS_URL,
        headers={
            "User-Agent": "ststat/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"EloBoard tier API fetch failed: {exc}") from exc

    players = flatten_players(payload)
    if not players:
        raise RuntimeError("EloBoard tier API returned zero usable players; refusing to write anything")
    return players


def run() -> JobResult:
    roster = load_roster()
    if not roster:
        raise RuntimeError("tier_members is empty; refusing to treat the whole API as new candidates")

    pending_ids = load_pending_ids()
    api_players = fetch_tier_players()

    elo_name_updates: dict[str, str] = {}
    new_candidates: list[RosterCandidate] = []
    seen_candidate_ids: set[str] = set()

    for player in api_players:
        normalized_id = player.soop_id.lower()
        existing = roster.get(normalized_id)

        if existing:
            # Preserve the existing Synergy behavior: only EloBoard source name is automatic.
            # nickname/race/tier/affiliation/role/etc. are manual/admin-owned and untouched.
            if player.elo_name and player.elo_name != (existing.elo_name or ""):
                elo_name_updates[existing.soop_id] = player.elo_name
            continue

        if normalized_id in pending_ids or normalized_id in seen_candidate_ids:
            continue

        seen_candidate_ids.add(normalized_id)
        new_candidates.append(
            RosterCandidate(
                id=player.soop_id,
                nickname=player.elo_name,
                elo_id=player.elo_id,
                gender=player.gender,
                race=player.race,
                tier=player.tier,
                affiliation=player.affiliation,
            )
        )

    if len(new_candidates) > MAX_NEW_CANDIDATES_PER_RUN:
        raise RuntimeError(
            f"Abnormal candidate count: {len(new_candidates)} > {MAX_NEW_CANDIDATES_PER_RUN}; "
            "refusing to write because roster/API matching may be broken"
        )

    updated = update_elo_names(elo_name_updates)
    candidates_written = upsert_candidates(new_candidates)

    return JobResult(
        records_read=len(api_players),
        records_written=updated + candidates_written,
        records_skipped=max(0, len(api_players) - updated - candidates_written),
        metadata={
            "roster_count": len(roster),
            "elo_names_updated": updated,
            "new_candidates": candidates_written,
            "manual_fields_touched": 0,
            "modified_at_cleared": 0,
        },
    )
