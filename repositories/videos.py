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
    # 빈 값은 쓰지 않는다: API가 실패해 RSS로 받으면 업로드 목록 ID가 없고, 채널 페이지에서 썸네일을
    # 못 읽을 때도 있다. 그걸로 저장된 값을 지우면 다음 실행이 채널을 처음부터 다시 찾는다.
    fields = {
        "channel_id": info.get("id"),
        "title": info.get("title"),
        "thumb": info.get("thumb"),
        "uploads": info.get("uploads"),
    }
    db = get_supabase()
    db.table("video_channels").update({
        **{k: v for k, v in fields.items() if v},
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
            # 조회수를 모르면(None) 지금 저장된 값을 그대로 둔다(0으로 덮어쓰지 않음)
            "views": int(item["views"]) if item.get("views") is not None
            else int((existing.get(video_id) or {}).get("views") or 0),
            "short": bool(item.get("short")),
            "updated_at": now,
        })
    for start in range(0, len(payload), 300):
        db.table("videos").upsert(payload[start:start + 300], on_conflict="id").execute()
    return len(payload)
