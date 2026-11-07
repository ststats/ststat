from __future__ import annotations

from repositories.supabase import fetch_all, get_supabase


def load_active_members() -> list[dict]:
    """활동 멤버(퇴단일 없음)의 SOOP ID와 유튜브 주소. 같은 사람의 행이 여럿이면 먼저 나온 것."""
    db = get_supabase()
    rows = fetch_all(
        lambda: (
            db.table("members")
            .select("soop_id,youtube_url")
            .is_("left_date", "null")
            .not_.is_("soop_id", "null")
            .order("source_order")
        )
    )
    members: dict[str, dict] = {}
    for row in rows:
        soop_id = str(row.get("soop_id") or "").strip().lower()
        if soop_id and soop_id not in members:
            members[soop_id] = {"soop_id": soop_id, "youtube_url": str(row.get("youtube_url") or "").strip()}
    return list(members.values())


def load_youtube_short_flags() -> dict[str, dict]:
    db = get_supabase()
    rows = fetch_all(
        lambda: (
            db.table("member_videos").select("video_id,short").eq("kind", "youtube").order("soop_id").order("video_id")
        )
    )
    return {row["video_id"]: {"short": row["short"]} for row in rows}


def replace_member_videos(kind: str, rows: list[dict], scanned: list[str], active: list[str]) -> int:
    data = (
        get_supabase()
        .rpc(
            "replace_member_videos",
            {
                "p_kind": kind,
                "p_rows": rows,
                "p_scanned": scanned,
                "p_active": active,
            },
        )
        .execute()
        .data
    )
    return int(data or 0)
