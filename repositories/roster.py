from __future__ import annotations

from datetime import date, datetime, timezone

from models.roster import ExistingRosterMember, RosterCandidate
from repositories.supabase import get_supabase


# tier_members는 읽기만 한다. 선수 정보 수정은 어드민·티어표 갱신에서만 한다.


def candidate_id(elo_id, soop_id=None) -> str:
    """대기 명단 행 id. 한 사람은 ELO ID 하나로 한 줄(경기 기록에서 먼저 본 선수와 같은 id)."""
    return f"elo:{elo_id}" if elo_id is not None else str(soop_id)


def load_roster() -> list[ExistingRosterMember]:
    db = get_supabase()
    rows = []
    start = 0
    while True:
        batch = (
            db.table("tier_members")
            .select("id,soop_id,name,elo_id,modified_at")
            .order("source_order")
            .order("id")
            .range(start, start + 999)
            .execute()
            .data
            or []
        )
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000

    out: list[ExistingRosterMember] = []
    for row in rows:
        elo_id = row.get("elo_id")
        try:
            elo_id = int(elo_id) if elo_id is not None else None
        except (TypeError, ValueError):
            elo_id = None
        out.append(ExistingRosterMember(
            id=row["id"],
            soop_id=(str(row.get("soop_id") or "").strip() or None),
            elo_name=(str(row.get("name") or "").strip() or None),
            elo_id=elo_id,
            modified_at=(str(row.get("modified_at") or "").strip() or None),
        ))
    return out


def load_linked_elo_ids() -> set[int]:
    """선수에 '연결 계정'으로 붙은 ELO ID(종족 변경 등으로 생긴 다른 계정). 명단에 있는 사람으로 본다.

    표는 staruniv.sql이 만든다. 아직 SQL을 안 돌려 표가 없으면 빈 집합(연결 없음)으로 본다.
    """
    db = get_supabase()
    out: set[int] = set()
    start = 0
    while True:
        try:
            batch = (db.table("tier_member_elo_links").select("elo_id").order("elo_id")
                     .range(start, start + 999).execute().data or [])
        except Exception as exc:  # 표가 없을 때(PGRST205 등)
            if "tier_member_elo_links" in str(exc):
                return set()
            raise
        out.update(int(r["elo_id"]) for r in batch if r.get("elo_id") is not None)
        if len(batch) < 1000:
            return out
        start += 1000


CANDIDATE_INFO = ("soop_id", "gender", "race", "tier", "affiliation")


def load_pending_ids() -> dict[str, dict]:
    """대기 명단 행(상태 무관) {id 소문자: 행}. 새 id는 'elo:<ELO ID>'. 티어 목록 정보(CANDIDATE_INFO)도 같이 읽는다
    - 경기 기록에서 먼저 올라온 줄은 SOOP ID·티어·소속이 비어 있어, 티어 목록에 나오면 채운다(fill_candidates)."""
    db = get_supabase()
    rows = []
    start = 0
    while True:
        batch = (
            db.table("tier_member_candidates")
            .select("id," + ",".join(CANDIDATE_INFO))
            .order("id")
            .range(start, start + 999)
            .execute()
            .data
            or []
        )
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000
    return {
        str(row.get("id") or "").strip().lower(): row
        for row in rows
        if str(row.get("id") or "").strip()
    }


def fill_candidates(updates: list[dict]) -> int:
    """이미 있는 대기 명단 줄의 티어 목록 정보만 고친다(id + CANDIDATE_INFO). 상태·발견일은 그대로."""
    if not updates:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    db = get_supabase()
    for row in updates:
        db.table("tier_member_candidates").update(
            {**{k: row[k] for k in CANDIDATE_INFO if k in row}, "updated_at": now}
        ).eq("id", row["id"]).execute()
    return len(updates)


def drop_resolved_candidates(elo_ids: set[int]) -> int:
    """명단에 이미 있는 선수(ELO ID 기준)를 대기 명단에서 지운다(티어표 갱신으로 추가된 뒤 등)."""
    if not elo_ids:
        return 0
    db = get_supabase()
    rows = []
    start = 0
    while True:
        batch = (db.table("tier_member_candidates").select("id,elo_id").order("id")
                 .range(start, start + 999).execute().data or [])
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000
    gone = [r["id"] for r in rows if r.get("elo_id") is not None and int(r["elo_id"]) in elo_ids]
    for i in range(0, len(gone), 200):
        db.table("tier_member_candidates").delete().in_("id", gone[i:i + 200]).execute()
    return len(gone)


def upsert_candidates(candidates: list[RosterCandidate]) -> int:
    if not candidates:
        return 0

    today = date.today().isoformat()
    payload = [
        {
            "id": c.id,
            "nickname": c.nickname,
            "elo_id": c.elo_id,
            "soop_id": c.soop_id,
            "gender": c.gender,
            "race": c.race,
            "tier": c.tier,
            "affiliation": c.affiliation,
            "source": c.source,
            "found_at": today,
            "last_seen_at": today,
            "status": "pending",
        }
        for c in candidates
    ]

    db = get_supabase()
    db.table("tier_member_candidates").upsert(payload, on_conflict="id").execute()
    return len(payload)
