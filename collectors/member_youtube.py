from __future__ import annotations

from collectors.youtube_videos import collect_channel, resolve_short_flags

VIDEO_LIMIT = 20


def collect_member_youtube(url: str, previous: dict[str, dict]) -> list[dict]:
    """멤버 채널의 최신 영상(쇼츠 포함). 쇼츠 여부를 모르는 새 영상은 다음 실행으로 미룬다."""
    _info, items, _method = collect_channel(url, None, 1)
    latest = sorted(items, key=lambda item: item.get("published") or "", reverse=True)[:VIDEO_LIMIT]
    return [{
        "video_id": item["id"],
        "title": item.get("title") or "",
        "published": item.get("published") or None,
        "thumb": item.get("thumb") or None,
        "views": item.get("views"),
        "short": item["short"],
    } for item in resolve_short_flags(latest, previous)]
