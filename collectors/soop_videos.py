from __future__ import annotations

import re
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlsplit


class _ThumbnailParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.thumbnail = ""

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag != "meta" or fields.get("property", "").lower() != "og:image":
            return
        value = fields.get("content", "") or ""
        url = urlsplit(value)
        host = url.hostname or ""
        if url.scheme == "https" and any(host.endswith("." + domain) for domain in
                                        ("afreecatv.com", "sooplive.co.kr", "sooplive.com")):
            self.thumbnail = value


def collect_thumbnail(video_id: str) -> str:
    """SOOP 미리보기 이미지. 실패하면 다음 실행에서 다시 시도한다."""
    if not re.fullmatch(r"soop:\d{1,20}", video_id):
        raise ValueError("Invalid SOOP video id")
    request = urllib.request.Request(
        "https://vod.sooplive.co.kr/player/" + video_id.split(":", 1)[1],
        headers={"User-Agent": "ststat/1.0 (+https://github.com/ststats/ststat)"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        parser = _ThumbnailParser()
        parser.feed(response.read(2_000_000).decode("utf-8", "replace"))
    if not parser.thumbnail:
        raise ValueError("SOOP preview image not found")
    return parser.thumbnail
