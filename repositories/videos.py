from __future__ import annotations

from datetime import datetime, timezone

from repositories.supabase import fetch_all, get_supabase


def load_soop_picks_without_thumbnail() -> list[dict]:
    db = get_supabase()
    return fetch_all(lambda: db.table("video_picks").select("id").eq("kind", "soop")
                     .or_("thumb.is.null,thumb.eq.").order("id"))


def fill_soop_pick_thumbnail(video_id: str, thumbnail: str) -> int:
    # 쓰는 순간에 다시 확인한다 - 가져오는 사이 관리자가 이미지를 넣었을 수 있다.
    rows = (get_supabase().table("video_picks").update({"thumb": thumbnail})
            .eq("id", video_id).eq("kind", "soop")
            .or_("thumb.is.null,thumb.eq.").execute().data or [])
    return len(rows)


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
    rows = fetch_all(lambda: db.table("videos").select("id,channel_url,title,published,thumb,views,short")
                     .order("published", desc=True).order("id"))
    return {str(row["id"]): row for row in rows if row.get("id")}


def update_channel_metadata(channel_url: str, info: dict) -> None:
    """자동 메타데이터만 고친다. display_name/source_order/active는 관리자 소유라 쓰지 않는다."""
    # 빈 값은 쓰지 않는다 - RSS 대체나 썸네일 실패로 빈 값이 오면 저장된 값이 지워져 다음 실행이 채널을 다시 찾는다.
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
    """자동 수집 필드만 upsert한다. 관리자 소유 hidden은 건드리지 않는다."""
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
            # 조회수를 모르면 저장된 값을 둔다(0으로 덮어쓰지 않음)
            "views": int(item["views"]) if item.get("views") is not None
            else int((existing.get(video_id) or {}).get("views") or 0),
            "short": bool(item.get("short")),
            "updated_at": now,
        })
    for start in range(0, len(payload), 300):
        db.table("videos").upsert(payload[start:start + 300], on_conflict="id").execute()
    return len(payload)
