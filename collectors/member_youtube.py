from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

from collectors.youtube_videos import collect_channel, resolve_short_flags


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


def _valid_time(text) -> bool:
    try:
        datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def collect_member_youtube(url: str, previous: dict[str, dict], limit: int) -> list[dict]:
    """멤버 채널의 최신 영상(쇼츠 포함). 쇼츠 여부를 모르는 새 영상은 다음 실행으로 미룬다.
    url은 channel_url로 거른 주소. 기록된 영상 수(archived_count) 1은 처음 수집이 아니라 최근 쪽만 받으라는 뜻이다."""
    _info, items, _method = collect_channel(url, None, 1)
    dated = [item for item in items if _valid_time(item.get("published"))]
    latest = sorted(dated, key=lambda item: item["published"], reverse=True)[:limit]
    return [
        {
            "video_id": item["id"],
            "title": item.get("title") or "",
            "published": item["published"],
            "thumb": item.get("thumb") or None,
            "views": item.get("views"),
            "short": item["short"],
        }
        for item in resolve_short_flags(latest, previous)
    ]
