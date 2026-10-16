from __future__ import annotations

import re
from urllib.parse import urlsplit

from collectors.youtube_videos import collect_channel, resolve_short_flags

VIDEO_LIMIT = 20


def channel_url(value: str) -> str:
    """멤버 관리의 유튜브 칸(주소 또는 "@핸들")을 채널 주소로. 유튜브 주소가 아니면 거른다."""
    raw = str(value or "").strip()
    if re.fullmatch(r"@[\w.\-]+", raw):
        return "https://www.youtube.com/" + raw
    if not re.match(r"https?://", raw, re.I):
        raw = "https://" + raw
    host = (urlsplit(raw).hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host != "youtube.com":
        raise ValueError(f"Not a YouTube channel address: {value}")
    return raw


def collect_member_youtube(url: str, previous: dict[str, dict]) -> list[dict]:
    """멤버 채널의 최신 영상(쇼츠 포함). 쇼츠 여부를 모르는 새 영상은 다음 실행으로 미룬다."""
    _info, items, _method = collect_channel(channel_url(url), None, 1)
    latest = sorted(items, key=lambda item: item.get("published") or "", reverse=True)[:VIDEO_LIMIT]
    return [{
        "video_id": item["id"],
        "title": item.get("title") or "",
        "published": item.get("published") or None,
        "thumb": item.get("thumb") or None,
        "views": item.get("views"),
        "short": item["short"],
    } for item in resolve_short_flags(latest, previous)]
