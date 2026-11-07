from __future__ import annotations

import time

from collectors.member_youtube import channel_url, collect_member_youtube
from collectors.soop_videos import collect_member_vods
from models.sync_job import JobResult
from repositories.member_videos import load_active_members, load_youtube_short_flags, replace_member_videos

REQUEST_DELAY = 0.5
VIDEO_LIMIT = 20  # 멤버마다 종류별 최근 몇 개


def _sync(kind: str, targets: dict[str, str], collect) -> dict:
    """targets: SOOP ID → 수집 대상(SOOP ID 또는 유튜브 주소). 절반 넘게 못 받으면 이 종류는 바꾸지 않는다."""
    rows: list[dict] = []
    scanned: list[str] = []
    errors: list[dict] = []
    for i, (soop_id, source) in enumerate(targets.items()):
        if i:
            time.sleep(REQUEST_DELAY)
        try:
            videos = collect(source)
        except Exception as exc:
            errors.append({"soop_id": soop_id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        scanned.append(soop_id)
        rows.extend({"soop_id": soop_id, **video} for video in videos)
    result = {"members": len(targets), "succeeded": len(scanned), "failed_members": len(errors), "errors": errors[:20]}
    if len(scanned) < len(targets) / 2:
        return {
            **result,
            "written": 0,
            "failed": f"{kind} {len(targets)}명 중 {len(errors)}명을 받지 못해 바꾸지 않았습니다.",
        }
    # 받았는데 전원이 빈 목록이면 응답 형식이 바뀐 것으로 보고 지우지 않는다(sync_videos와 같은 판단)
    if scanned and not rows:
        return {**result, "written": 0, "failed": f"{kind} 받은 {len(scanned)}명이 모두 빈 목록이라 바꾸지 않았습니다."}
    # 못 받은 멤버의 목록은 그대로 두고, 대상에서 빠진 멤버(퇴단·유튜브 주소 삭제·잘못된 주소)의 목록은 지운다
    written = replace_member_videos(kind, rows, scanned, list(targets))
    return {**result, "read": len(rows), "written": written}


def run() -> JobResult:
    members = load_active_members()
    if not members:
        raise RuntimeError("활동 멤버가 없습니다.")
    shorts = load_youtube_short_flags()
    # 유튜브 채널 주소가 아닌 값(영상 링크 등)은 대상에서 빼서 예전 목록이 남지 않게 한다
    channels: dict[str, str] = {}
    invalid_urls: list[dict] = []
    for m in members:
        if not m["youtube_url"]:
            continue
        try:
            channels[m["soop_id"]] = channel_url(m["youtube_url"])
        except ValueError:
            invalid_urls.append({"soop_id": m["soop_id"], "youtube_url": m["youtube_url"]})
    results = {
        "soop": _sync(
            "soop",
            {m["soop_id"]: m["soop_id"] for m in members},
            lambda soop_id: collect_member_vods(soop_id, VIDEO_LIMIT),
        ),
        "youtube": _sync("youtube", channels, lambda url: collect_member_youtube(url, shorts, VIDEO_LIMIT)),
    }
    results["youtube"]["invalid_urls"] = invalid_urls
    failed = [r["failed"] for r in results.values() if r.get("failed")]
    if failed:
        raise RuntimeError(" ".join(failed) + f" results={results}")
    return JobResult(
        records_read=sum(r.get("read", 0) for r in results.values()),
        records_written=sum(r["written"] for r in results.values()),
        records_skipped=sum(r["failed_members"] for r in results.values()),
        metadata=results,
    )
