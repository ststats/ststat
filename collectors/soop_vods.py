from __future__ import annotations

import json
import re
import urllib.request
from urllib.parse import urlencode, urlsplit

VOD_LIMIT = 20
_SOOP_ID = re.compile(r"[a-z0-9_]{1,50}")
_REG_DATE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_IMAGE_DOMAINS = ("afreecatv.com", "sooplive.co.kr", "sooplive.com")


def _thumb(value) -> str | None:
    text = str(value or "").strip()
    if text.startswith("//"):
        text = "https:" + text
    url = urlsplit(text)
    host = url.hostname or ""
    if url.scheme == "https" and any(host.endswith("." + domain) for domain in _IMAGE_DOMAINS):
        return text
    return None


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
    if not title_no or not title or not _REG_DATE.fullmatch(reg_date):
        return None
    ucc = item.get("ucc") if isinstance(item.get("ucc"), dict) else {}
    count = item.get("count") if isinstance(item.get("count"), dict) else {}
    duration_ms = _int(ucc.get("total_file_duration"))
    return {
        "title_no": title_no,
        "title": title,
        "reg_date": reg_date,
        "thumb": _thumb(ucc.get("thumb")),
        "duration_sec": duration_ms // 1000 if duration_ms is not None else None,
        "views": _int(count.get("read_cnt")),
    }


def collect_member_vods(soop_id: str, limit: int = VOD_LIMIT) -> list[dict]:
    """방송국 다시보기(review) 첫 쪽, 최신순."""
    if not _SOOP_ID.fullmatch(soop_id):
        raise ValueError("Invalid SOOP id")
    query = urlencode({"page": 1, "per_page": limit, "orderby": "reg_date"})
    request = urllib.request.Request(
        f"https://chapi.sooplive.com/api/{soop_id}/vods/review?{query}",
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
