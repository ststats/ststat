from __future__ import annotations

import os

from collectors.soop_videos import collect_thumbnail
from collectors.youtube_videos import collect_channel, resolve_short_flags
from models.sync_job import JobResult
from repositories.videos import (
    load_active_channels,
    load_existing_videos,
    update_channel_metadata,
    upsert_collected_videos,
    load_soop_picks_without_thumbnail,
    fill_soop_pick_thumbnail,
)


def run() -> JobResult:
    channels = load_active_channels()
    existing = load_existing_videos() if channels else {}
    full = os.getenv("YOUTUBE_FULL_SYNC", "").strip().lower() in {"1", "true", "yes"}

    read = 0
    written = 0
    skipped = 0
    successes = 0
    methods: dict[str, int] = {"api": 0, "rss": 0}
    errors: list[dict] = []
    api_fallbacks: list[dict] = []

    picks = load_soop_picks_without_thumbnail()
    thumbnails_written = 0
    for pick in picks:
        read += 1
        try:
            thumbnails_written += fill_soop_pick_thumbnail(pick["id"], collect_thumbnail(pick["id"]))
        except Exception as exc:
            skipped += 1
            errors.append({"pick": pick["id"], "error": f"{type(exc).__name__}: {exc}"})
    written += thumbnails_written

    for channel in channels:
        url = str(channel.get("channel_url") or "").strip()
        if not url:
            skipped += 1
            continue
        try:
            archived_count = sum(1 for video in existing.values() if video.get("channel_url") == url)
            info, items, method = collect_channel(url, channel, archived_count, full=full)
            items = resolve_short_flags(items, existing)
            read += len(items)

            # 성공했는데 빈 결과는 의심스러워 아무것도 쓰지 않는다
            if not items:
                skipped += 1
                errors.append({"channel": url, "error": "empty collection result"})
                continue

            # 영상 저장이 실패하면 채널 정보도 그대로 두려고 영상부터 저장한다
            written += upsert_collected_videos(url, items, existing)
            update_channel_metadata(url, info)
            successes += 1
            methods[method] = methods.get(method, 0) + 1
            if info.get("api_error"):
                api_fallbacks.append({"channel": url, "api_error": info["api_error"]})
        except Exception as exc:
            skipped += 1
            errors.append({"channel": url, "error": f"{type(exc).__name__}: {exc}"})

    # 실패한 채널은 일부 영상만 갱신됐을 수 있지만 upsert라 다시 실행해도 안전하다
    if channels and successes == 0:
        raise RuntimeError(f"All YouTube channels failed; nothing deleted, failed channels may be partially updated. errors={errors[:5]}")

    return JobResult(
        records_read=read,
        records_written=written,
        records_skipped=skipped,
        metadata={
            "channels_configured": len(channels),
            "channels_succeeded": successes,
            "methods": methods,
            "errors": errors[:20],
            "api_fallbacks": api_fallbacks[:20],
            "full_sync": full,
            "soop_thumbnails_requested": len(picks),
            "soop_thumbnails_written": thumbnails_written,
            "manual_fields_preserved": [
                "video_channels.display_name",
                "video_channels.source_order",
                "video_channels.active",
                "videos.hidden",
                "video_picks fields except empty thumb",
            ],
        },
    )
