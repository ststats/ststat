from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from collectors.eloboard import USER_AGENT
from models.roster import RosterCandidate, TierApiPlayer
from models.sync_job import JobResult
from repositories.roster import (
    candidate_id,
    drop_resolved_candidates,
    fill_candidates,
    load_linked_elo_ids,
    load_pending_ids,
    load_roster,
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
            "User-Agent": USER_AGENT,
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
    members = load_roster()
    if not members:
        raise RuntimeError("tier_members is empty; refusing to treat the whole API as new candidates")
    # EloBoard의 SOOP ID가 틀린 선수가 있어 ELO ID로 먼저 찾고, ELO ID가 없을 때만 SOOP ID로 찾는다.
    by_elo = {m.elo_id: m for m in members if m.elo_id is not None}
    by_soop = {m.soop_id.lower(): m for m in members if m.soop_id}
    # 어드민에서 선수에 연결한 다른 계정(종족 변경 등)
    linked = load_linked_elo_ids()

    pending_ids = load_pending_ids()
    api_players = fetch_tier_players()

    new_candidates: list[RosterCandidate] = []
    seen_candidate_ids: set[str] = set()
    soop_mismatch: list[dict] = []
    fills: list[dict] = []

    for player in api_players:
        if player.elo_id is not None and player.elo_id in linked:
            continue
        existing = by_elo.get(player.elo_id) if player.elo_id is not None else None
        if existing is None and player.elo_id is None and player.soop_id:
            existing = by_soop.get(player.soop_id.lower())
        if existing is None and player.elo_id is not None and player.soop_id:
            # 같은 사람일 가능성이 크지만 EloBoard SOOP ID가 틀릴 수 있어 잇지 않고 기록만 남긴다.
            same_soop = by_soop.get(player.soop_id.lower())
            if same_soop is not None and same_soop.elo_id is None and len(soop_mismatch) < 50:
                soop_mismatch.append({"tier_member_id": same_soop.id, "elo_id": player.elo_id,
                                      "soop_id": player.soop_id, "elo_name": player.elo_name})

        if existing:
            # 명단 선수 정보는 어드민에서만 고친다
            continue

        cid = candidate_id(player.elo_id, player.soop_id)
        if cid in seen_candidate_ids:
            continue
        if cid.lower() in pending_ids:
            # 경기 기록에서 먼저 올라온 후보는 빈 칸을 티어 목록 값으로 채운다. 같은 ELO ID는 첫 줄만.
            seen_candidate_ids.add(cid)
            row = pending_ids[cid.lower()] or {}
            info = {"soop_id": player.soop_id or None, "gender": player.gender, "race": player.race,
                    "tier": player.tier, "affiliation": player.affiliation}
            changed = {k: v for k, v in info.items() if v is not None and v != row.get(k)}
            if changed:
                fills.append({"id": row.get("id") or cid, **changed})
            continue

        seen_candidate_ids.add(cid)
        new_candidates.append(
            RosterCandidate(
                id=cid,
                nickname=player.elo_name,
                elo_id=player.elo_id,
                soop_id=player.soop_id or None,
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

    candidates_written = upsert_candidates(new_candidates)
    filled = fill_candidates(fills[:MAX_NEW_CANDIDATES_PER_RUN * 5])
    resolved = drop_resolved_candidates(set(by_elo) | linked)

    return JobResult(
        records_read=len(api_players),
        records_written=candidates_written + filled,
        records_skipped=max(0, len(api_players) - candidates_written),
        metadata={
            "roster_count": len(members),
            "new_candidates": candidates_written,
            "candidates_filled": filled,
            "candidates_resolved": resolved,
            "linked_accounts": len(linked),
            # ELO ID가 빈 명단 선수와 SOOP ID만 같은 경우(어드민에서 확인해 채운다)
            "possible_links": soop_mismatch,
        },
    )
