from __future__ import annotations

import os

from collectors.youtube_videos import collect_channel, resolve_short_flags
from models.sync_job import JobResult
from repositories.videos import (
    load_active_channels,
    load_existing_videos,
    update_channel_metadata,
    upsert_collected_videos,
)


def run() -> JobResult:
    channels = load_active_channels()
    if not channels:
        return JobResult(metadata={"message": "No active video channels configured"})

    existing = load_existing_videos()
    full = os.getenv("YOUTUBE_FULL_SYNC", "").strip().lower() in {"1", "true", "yes"}

    read = 0
    written = 0
    skipped = 0
    successes = 0
    methods: dict[str, int] = {"api": 0, "rss": 0}
    errors: list[dict] = []

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

            # A successfully fetched channel returning nothing is suspicious.
            # Do not write/clear anything; preserve the last good DB state.
            if not items:
                skipped += 1
                errors.append({"channel": url, "error": "empty collection result"})
                continue

            update_channel_metadata(url, info)
            written += upsert_collected_videos(url, items, existing)
            successes += 1
            methods[method] = methods.get(method, 0) + 1
        except Exception as exc:
            skipped += 1
            errors.append({"channel": url, "error": f"{type(exc).__name__}: {exc}"})

    # Do not report success if every source failed. The old DB contents remain untouched.
    if successes == 0:
        raise RuntimeError(f"All YouTube channels failed; previous data preserved. errors={errors[:5]}")

    return JobResult(
        records_read=read,
        records_written=written,
        records_skipped=skipped,
        metadata={
            "channels_configured": len(channels),
            "channels_succeeded": successes,
            "methods": methods,
            "errors": errors[:20],
            "full_sync": full,
            "manual_fields_preserved": [
                "video_channels.display_name",
                "video_channels.source_order",
                "video_channels.active",
                "videos.hidden",
                "video_picks.*",
            ],
        },
    )
