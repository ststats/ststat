from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
API_BASE = "https://www.googleapis.com/youtube/v3"
API_PAGE = 50
API_MAX_PAGES = int(os.getenv("YOUTUBE_FULL_MAX_PAGES", "40"))
API_RECENT_PAGES = int(os.getenv("YOUTUBE_RECENT_PAGES", "2"))
SHORT_MAX_SEC = 185
DELAY = float(os.getenv("YOUTUBE_DELAY", "0.5"))
RSS_RETRY = 3
RSS_RETRY_WAIT = 4
UA = os.getenv("YOUTUBE_UA", "ststat/1.0 (+https://github.com/ststats/ststat)")

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def encode_url(url: str) -> str:
    parts = urllib.parse.urlsplit(str(url))
    host = parts.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")
    except Exception:
        host = host.encode("ascii", "ignore").decode("ascii")
    netloc = f"{host}:{parts.port}" if parts.port else host
    path = urllib.parse.quote(parts.path, safe="/%@:+$,;=~!*'()-._")
    query = urllib.parse.quote(parts.query, safe="%=&?/:@+$,;~!*'()-._")
    return urllib.parse.urlunsplit((parts.scheme, netloc, path, query, ""))


def http_get(url: str, *, allow_redirect: bool = True, timeout: int = 20) -> tuple[int, str]:
    req = urllib.request.Request(
        encode_url(url),
        headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"},
    )
    opener = urllib.request.build_opener() if allow_redirect else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as res:
            return int(res.status), res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), exc.read().decode("utf-8", "replace")
        except Exception:
            return int(exc.code), ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, ""


def resolve_channel(url: str, cached: dict | None = None) -> dict:
    cached = cached or {}
    if cached.get("channel_id") and cached.get("thumb"):
        return {
            "id": cached.get("channel_id") or "",
            "title": cached.get("title") or "",
            "thumb": cached.get("thumb") or "",
            "url": url,
            "uploads": cached.get("uploads") or "",
        }

    match = re.search(r"/channel/(UC[\w-]{22})", url)
    channel_id = match.group(1) if match else ""
    status, page = http_get(url if url.startswith("http") else "https://www.youtube.com/" + url.lstrip("/"))
    time.sleep(DELAY)

    if not channel_id and page:
        match = (
            re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', page)
            or re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', page)
        )
        channel_id = match.group(1) if match else ""

    if not channel_id:
        raise RuntimeError(f"YouTube channel id not found: {url} (HTTP {status})")

    thumb = ""
    title = ""
    if page:
        match = re.search(r'<meta property="og:image" content="([^"]+)"', page)
        thumb = html.unescape(match.group(1)) if match else ""
        match = re.search(r'<meta property="og:title" content="([^"]+)"', page)
        title = html.unescape(match.group(1)) if match else ""

    return {"id": channel_id, "title": title, "thumb": thumb, "url": url, "uploads": ""}


def fetch_rss(channel_id: str) -> tuple[int, str]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    last = (0, "")
    for attempt in range(RSS_RETRY):
        last = http_get(url)
        if last[0] == 200 and last[1]:
            return last
        if attempt < RSS_RETRY - 1:
            time.sleep(RSS_RETRY_WAIT * (2 ** attempt))
    return last


def fetch_feed(channel_id: str) -> tuple[str, list[dict]]:
    status, body = fetch_rss(channel_id)
    if status != 200 or not body:
        raise RuntimeError(f"YouTube RSS failed: channel={channel_id}, HTTP {status}")
    root = ET.fromstring(body)
    title = (root.findtext("atom:title", default="", namespaces=NS) or "").strip()
    out: list[dict] = []
    for entry in root.findall("atom:entry", NS):
        vid = (entry.findtext("yt:videoId", default="", namespaces=NS) or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            continue
        media = entry.find("media:group", NS)
        thumb = ""
        views = 0
        if media is not None:
            tn = media.find("media:thumbnail", NS)
            if tn is not None:
                thumb = tn.attrib.get("url", "")
            stat = media.find("media:community/media:statistics", NS)
            if stat is not None:
                try:
                    views = int(stat.attrib.get("views", "0"))
                except ValueError:
                    views = 0
        link = entry.find("atom:link", NS)
        href = link.attrib.get("href", "") if link is not None else ""
        out.append({
            "id": vid,
            "title": (entry.findtext("atom:title", default="", namespaces=NS) or "").strip(),
            "published": (entry.findtext("atom:published", default="", namespaces=NS) or "").strip(),
            "thumb": thumb or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "views": views,
            "short": True if "/shorts/" in href else None,
        })
    return title, out


def is_short(video_id: str) -> bool:
    status, _ = http_get(f"https://www.youtube.com/shorts/{video_id}", allow_redirect=False, timeout=10)
    return status == 200


def api_get(path: str, **params) -> dict:
    if not API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is not configured")
    query = urllib.parse.urlencode({**params, "key": API_KEY})
    status, body = http_get(f"{API_BASE}/{path}?{query}", timeout=30)
    if status != 200:
        detail = body[:500] if body else f"HTTP {status}"
        raise RuntimeError(f"YouTube API {path} failed: {detail}")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected YouTube API response for {path}")
    return payload


def api_channel(url: str, cached: dict | None = None) -> dict:
    cached = cached or {}
    match = re.search(r"/channel/(UC[\w-]{22})", url)
    params = {"part": "snippet,contentDetails"}
    if match:
        params["id"] = match.group(1)
    elif re.search(r"/@([^/?#]+)", url):
        params["forHandle"] = "@" + urllib.parse.unquote(re.search(r"/@([^/?#]+)", url).group(1))
    elif re.search(r"/user/([^/?#]+)", url):
        params["forUsername"] = urllib.parse.unquote(re.search(r"/user/([^/?#]+)", url).group(1))
    else:
        found = resolve_channel(url, cached)
        params["id"] = found["id"]

    items = api_get("channels", **params).get("items") or []
    if not items:
        raise RuntimeError(f"YouTube channel not found: {url}")
    item = items[0]
    snippet = item.get("snippet") or {}
    thumbs = snippet.get("thumbnails") or {}
    thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
    return {
        "id": item["id"],
        "title": snippet.get("title", ""),
        "thumb": thumb,
        "url": url,
        "uploads": ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads", ""),
    }


def api_uploads(playlist_id: str, max_pages: int) -> list[dict]:
    out: list[dict] = []
    token = None
    for _ in range(max_pages):
        params = {"part": "snippet,contentDetails", "playlistId": playlist_id, "maxResults": API_PAGE}
        if token:
            params["pageToken"] = token
        data = api_get("playlistItems", **params)
        for item in data.get("items") or []:
            snippet = item.get("snippet") or {}
            vid = (
                (item.get("contentDetails") or {}).get("videoId")
                or (snippet.get("resourceId") or {}).get("videoId")
                or ""
            )
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                continue
            thumbs = snippet.get("thumbnails") or {}
            out.append({
                "id": vid,
                "title": (snippet.get("title") or "").strip(),
                "published": (
                    (item.get("contentDetails") or {}).get("videoPublishedAt")
                    or snippet.get("publishedAt")
                    or ""
                ),
                "thumb": (
                    thumbs.get("medium") or thumbs.get("high") or thumbs.get("default") or {}
                ).get("url", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"),
            })
        token = data.get("nextPageToken")
        if not token:
            break
    return out


def iso_duration_sec(text: str) -> int:
    match = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(text or ""))
    if not match:
        return 0
    h, minute, sec = (int(x) if x else 0 for x in match.groups())
    return h * 3600 + minute * 60 + sec


def api_stats(ids: list[str]) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for index in range(0, len(ids), 50):
        chunk = ids[index:index + 50]
        data = api_get("videos", part="statistics,contentDetails", id=",".join(chunk), maxResults=50)
        for item in data.get("items") or []:
            try:
                views = int((item.get("statistics") or {}).get("viewCount", 0))
            except (TypeError, ValueError):
                views = 0
            duration = iso_duration_sec((item.get("contentDetails") or {}).get("duration"))
            out[item["id"]] = (views, duration)
    return out


def collect_channel(url: str, cached: dict | None, archived_count: int, *, full: bool = False) -> tuple[dict, list[dict], str]:
    """Collect one channel. API is preferred; RSS is the fallback."""
    cached = cached or {}
    if API_KEY:
        try:
            info = api_channel(url, cached)
            if not info.get("uploads"):
                raise RuntimeError("uploads playlist missing")
            pages = API_MAX_PAGES if (full or archived_count == 0) else API_RECENT_PAGES
            items = api_uploads(info["uploads"], pages)
            stats = api_stats([item["id"] for item in items])
            videos: list[dict] = []
            for item in items:
                views, seconds = stats.get(item["id"], (0, 0))
                videos.append({
                    **item,
                    "views": views,
                    "short": None if (seconds and seconds <= SHORT_MAX_SEC) else False,
                })
            return info, videos, "api"
        except Exception:
            # API key configuration/quota/channel quirks should not kill the whole pipeline.
            pass

    info = resolve_channel(url, cached)
    feed_title, items = fetch_feed(info["id"])
    if feed_title:
        info["title"] = feed_title
    return info, items, "rss"


def resolve_short_flags(items: list[dict], previous: dict[str, dict]) -> list[dict]:
    resolved: list[dict] = []
    for item in items:
        prev = previous.get(item["id"], {})
        short = item.get("short")
        if short is None:
            short = prev.get("short")
        if short is None:
            short = is_short(item["id"])
            time.sleep(DELAY)
        resolved.append({**item, "short": bool(short)})
    return resolved
