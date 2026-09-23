from __future__ import annotations

from datetime import datetime, timezone

from repositories.supabase import get_supabase

def load_active_channels() -> list[dict]:
    db = get_supabase()
    rows = (
        db.table("video_channels")
        .select("channel_url,channel_id,title,display_name,thumb,uploads,source_order,active")
        .eq("active", True)
        .order("source_order")
        .execute()
        .data
        or []
    )
    return rows


def load_existing_videos() -> dict[str, dict]:
    db = get_supabase()
    rows = []
    start = 0
    while True:
        batch = (
            db.table("videos")
            .select("id,channel_url,title,published,thumb,views,short")
            .order("published", desc=True)
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
    return {str(row["id"]): row for row in rows if row.get("id")}


def update_channel_metadata(channel_url: str, info: dict) -> None:
    """Update only ststat-owned automatic metadata.

    display_name/source_order/active are admin-owned and intentionally omitted.
    """
    db = get_supabase()
    db.table("video_channels").update({
        "channel_id": info.get("id") or None,
        "title": info.get("title") or None,
        "thumb": info.get("thumb") or None,
        "uploads": info.get("uploads") or None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("channel_url", channel_url).execute()


def upsert_collected_videos(channel_url: str, items: list[dict], existing: dict[str, dict]) -> int:
    """Upsert automatic video fields while preserving admin-owned hidden."""
    if not items:
        return 0
    db = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    payload = []
    for item in items:
        video_id = str(item.get("id") or "")
        if not video_id:
            continue
        payload.append({
            "id": video_id,
            "channel_url": channel_url,
            "title": str(item.get("title") or ""),
            "published": item.get("published") or None,
            "thumb": item.get("thumb") or None,
            "views": int(item.get("views") or 0),
            "short": bool(item.get("short")),
            "updated_at": now,
        })
    for start in range(0, len(payload), 300):
        db.table("videos").upsert(payload[start:start + 300], on_conflict="id").execute()
    return len(payload)
