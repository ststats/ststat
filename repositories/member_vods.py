from __future__ import annotations

from repositories.supabase import fetch_all, get_supabase


def load_active_member_ids() -> list[str]:
    db = get_supabase()
    rows = fetch_all(lambda: db.table("members").select("soop_id").is_("left_date", "null")
                     .not_.is_("soop_id", "null").order("soop_id"))
    ids: list[str] = []
    for row in rows:
        soop_id = str(row.get("soop_id") or "").strip().lower()
        if soop_id and soop_id not in ids:
            ids.append(soop_id)
    return ids


def replace_member_vods(rows: list[dict], scanned: list[str], active: list[str]) -> int:
    data = get_supabase().rpc("replace_member_vods", {
        "p_rows": rows, "p_scanned": scanned, "p_active": active,
    }).execute().data
    return int(data or 0)
