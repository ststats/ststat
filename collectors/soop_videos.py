from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit

_SOOP_ID = re.compile(r"[a-z0-9_]{1,50}")
# 숲 다시보기 주소. 스타유니브 history.js histVideo와 같은 주소를 받는다.
_SOOP_VOD_URL = re.compile(r"vod\.(?:sooplive\.(?:co\.kr|com)|afreecatv\.com)/player/(\d{1,20})")


def soop_vod_id(url) -> str | None:
    """숲 다시보기 주소를 영상 id(soop:번호)로. 아니면 None."""
    match = _SOOP_VOD_URL.search(str(url or ""))
    return f"soop:{match[1]}" if match else None


def soop_image(value) -> str | None:
    """SOOP 이미지 서버의 https 주소만. '//'로 시작하면 https를 붙인다."""
    text = str(value or "").strip()
    if text.startswith("//"):
        text = "https:" + text
    url = urlsplit(text)
    host = url.hostname or ""
    if url.scheme == "https" and any(
        host.endswith("." + domain) for domain in ("afreecatv.com", "sooplive.co.kr", "sooplive.com")
    ):
        return text
    return None


class _ThumbnailParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.thumbnail = ""

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag != "meta" or fields.get("property", "").lower() != "og:image":
            return
        self.thumbnail = soop_image(fields.get("content")) or self.thumbnail


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


def _int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def parse_vod(item: dict) -> dict | None:
    """다시보기 목록 한 칸. 번호·제목·등록 시각이 없으면 버린다(썸네일·길이·조회수는 없어도 된다)."""
    title_no = _int(item.get("title_no"))
    title = str(item.get("title_name") or "").strip()
    reg_date = str(item.get("reg_date") or "").strip()
    try:
        datetime.strptime(reg_date, "%Y-%m-%d %H:%M:%S")  # 모양만 맞고 없는 날짜(2월 30일)는 DB가 받지 않는다
    except ValueError:
        return None
    if not title_no or not title:
        return None
    ucc = item.get("ucc") if isinstance(item.get("ucc"), dict) else {}
    count = item.get("count") if isinstance(item.get("count"), dict) else {}
    duration_ms = _int(ucc.get("total_file_duration"))
    return {
        "video_id": str(title_no),
        "title": title,
        # SOOP 시각은 한국 시간이다
        "published": reg_date.replace(" ", "T") + "+09:00",
        "thumb": soop_image(ucc.get("thumb")),
        "duration_sec": duration_ms // 1000 if duration_ms is not None else None,
        "views": _int(count.get("read_cnt")),
    }


def collect_member_vods(soop_id: str, limit: int) -> list[dict]:
    """방송국 다시보기(review) 첫 쪽, 최신순."""
    if not _SOOP_ID.fullmatch(soop_id):
        raise ValueError("Invalid SOOP id")
    query = urlencode({"page": 1, "per_page": limit, "orderby": "reg_date"})
    request = urllib.request.Request(
        f"https://chapi.sooplive.com/api/{soop_id}/vods/review?{query}",
        # 방송국 API는 브라우저처럼 부른다(live-status의 게시판 수집과 같다)
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.loads(response.read(5_000_000).decode("utf-8"))
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise ValueError("SOOP VOD response format changed")
    vods = [vod for vod in (parse_vod(item) for item in items if isinstance(item, dict)) if vod]
    if items and not vods:
        raise ValueError("SOOP VOD items could not be read")
    return vods[:limit]
