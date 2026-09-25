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

            # 영상부터 저장하고 채널 정보는 그다음에 바꾼다(영상 저장이 실패하면 채널 정보도 그대로)
            written += upsert_collected_videos(url, items, existing)
            update_channel_metadata(url, info)
            successes += 1
            methods[method] = methods.get(method, 0) + 1
        except Exception as exc:
            skipped += 1
            errors.append({"channel": url, "error": f"{type(exc).__name__}: {exc}"})

    # 성공한 채널이 하나도 없으면 실패로 남긴다. 영상 저장은 채널마다 upsert라, 도중에 실패한 채널은
    # 일부 영상만 갱신됐을 수 있다(다시 실행하면 같은 값으로 덮어써 안전하다). 지워지는 것은 없다.
    if successes == 0:
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
